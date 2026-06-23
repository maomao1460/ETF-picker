# 作者：相空
"""
回测引擎 — 支持单次回测、参数扫描和 Walk-forward 复用同一套执行逻辑。
"""
from typing import Dict, Optional, Set

import numpy as np
import pandas as pd

from .universe import OFFENSIVE_SECTORS, SECTOR_MAP
from .selection import (
    select_top_n,
    apply_offensive_limit,
    apply_spike_filter,
    compute_rps,
    compute_volume_ratio,
    select_sector_rotation_targets,
)

TRADE_STAT_KEYS = (
    "round_trip_count",
    "trade_win_rate",
    "avg_hold_days",
    "avg_win_pnl",
    "avg_loss_pnl",
    "avg_round_trip_return",
    "profit_factor",
)


def _calc_rar(nav_arr: np.ndarray) -> float:
    """RAR: log(nav) 对时间 OLS 回归斜率 x 252"""
    if len(nav_arr) < 20:
        return 0.0
    log_nav = np.log(nav_arr)
    t = np.arange(len(log_nav), dtype=float)
    t_mean = t.mean()
    log_mean = log_nav.mean()
    slope = np.sum((t - t_mean) * (log_nav - log_mean)) / np.sum((t - t_mean) ** 2)
    return float(slope * 252)


def prepare_backtest_context(
    etf_data: Dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
) -> dict:
    """构建回测共享上下文，供单次回测和扫描复用。"""
    all_dates = set()
    for df in etf_data.values():
        if not df.empty:
            all_dates.update(df["date"].dropna().tolist())
    all_dates = sorted([d for d in all_dates if pd.notna(d)])

    price_cache: dict[str, dict[pd.Timestamp, float]] = {}
    amount_cache: dict[str, dict[pd.Timestamp, float]] = {}
    for sector, code in SECTOR_MAP.items():
        df = etf_data.get(code)
        if df is None or df.empty:
            continue
        raw = dict(zip(df["date"], df["close"]))
        filled: dict[pd.Timestamp, float] = {}
        last_price = None
        for date in all_dates:
            price = raw.get(date)
            if price is not None:
                last_price = float(price)
            if last_price is not None:
                filled[date] = last_price
        if filled:
            price_cache[sector] = filled

        # 成交额缓存 (用于板块轮动量比计算)
        if "amount" in df.columns:
            raw_amt = dict(zip(df["date"], df["amount"]))
            filled_amt: dict[pd.Timestamp, float] = {}
            last_amt = None
            for date in all_dates:
                amt = raw_amt.get(date)
                if amt is not None:
                    last_amt = float(amt)
                if last_amt is not None:
                    filled_amt[date] = last_amt
            if filled_amt:
                amount_cache[sector] = filled_amt

    aligned_prices = pd.DataFrame(
        {sector: pd.Series(prices) for sector, prices in price_cache.items()},
        index=pd.Index(all_dates, name="date"),
    ).sort_index()

    aligned_amounts = None
    if amount_cache:
        aligned_amounts = pd.DataFrame(
            {sector: pd.Series(amts) for sector, amts in amount_cache.items()},
            index=pd.Index(all_dates, name="date"),
        ).sort_index()

    obs_count_series: dict[str, pd.Series] = {}
    mature_series: dict[str, pd.Series] = {}
    listing_dates: dict[str, pd.Timestamp] = {}
    date_index = aligned_prices.index
    for sector, code in SECTOR_MAP.items():
        df = etf_data.get(code)
        if df is None or df.empty or sector not in aligned_prices.columns:
            continue
        sector_dates = pd.Index(pd.to_datetime(df["date"]).dropna().unique()).sort_values()
        if len(sector_dates) == 0:
            continue
        listing_date = pd.Timestamp(sector_dates.min())
        listing_dates[sector] = listing_date
        obs_count_series[sector] = (
            pd.Series(1, index=sector_dates)
            .reindex(date_index, fill_value=0)
            .cumsum()
            .astype(int)
        )
        mature_series[sector] = pd.Series(
            date_index >= listing_date + pd.Timedelta(days=60),
            index=date_index,
        )

    obs_count_frame = pd.DataFrame(obs_count_series, index=date_index).sort_index()
    mature_frame = pd.DataFrame(mature_series, index=date_index).sort_index()

    bench = benchmark.copy()
    if not bench.empty:
        bench = bench.sort_values("date").reset_index(drop=True)
    benchmark_aligned = pd.Series(index=date_index, dtype=float)
    if not bench.empty:
        benchmark_aligned = (
            bench.set_index("date")["close"]
            .astype(float)
            .reindex(date_index)
            .ffill()
        )

    return {
        "etf_data": etf_data,
        "benchmark": bench,
        "all_dates": all_dates,
        "date_to_idx": {date: idx for idx, date in enumerate(all_dates)},
        "price_cache": price_cache,
        "amount_cache": amount_cache,
        "aligned_prices": aligned_prices,
        "aligned_amounts": aligned_amounts,
        "obs_count_frame": obs_count_frame,
        "mature_frame": mature_frame,
        "listing_dates": listing_dates,
        "benchmark_aligned": benchmark_aligned,
        "score_cache": {},
        "spike_cache": None,
        "risk_cache": None,
        "vol_cache": {},
        "rps_cache": {},
    }


def _ensure_score_cache(context: dict, score_mode: str) -> pd.DataFrame:
    cache = context["score_cache"]
    if score_mode not in cache:
        prices = context["aligned_prices"]
        obs_count = context["obs_count_frame"].reindex(columns=prices.columns, fill_value=0)
        mature = context["mature_frame"].reindex(columns=prices.columns, fill_value=False)

        if prices.empty:
            cache[score_mode] = pd.DataFrame(index=context["all_dates"])
            return cache[score_mode]

        ret20 = prices.pct_change(20, fill_method=None)
        valid20 = ret20.where(mature & (obs_count >= 25))

        if score_mode == "pure20" or score_mode == "sector_rotation":
            cache[score_mode] = valid20
            return cache[score_mode]

        if score_mode == "mixed":
            ret10 = prices.pct_change(10, fill_method=None)
            ret5 = prices.pct_change(5, fill_method=None)
            valid10 = ret10.where(mature & (obs_count >= 15))
            valid5 = ret5.where(mature & (obs_count >= 10))
            cache[score_mode] = 0.5 * valid20.fillna(0) + 0.3 * valid10.fillna(0) + 0.2 * valid5.fillna(0)
            return cache[score_mode]

        # rank_momentum: pure20 as base (ranking is done in backtest loop)
        if score_mode == "rank_momentum":
            cache[score_mode] = valid20
            return cache[score_mode]

        cache[score_mode] = valid20
    return cache[score_mode]


def _ensure_spike_cache(context: dict) -> pd.DataFrame:
    """Pre-compute spike flags for every date."""
    if context["spike_cache"] is not None:
        return context["spike_cache"]

    prices = context["aligned_prices"]
    mature = context["mature_frame"].reindex(columns=prices.columns, fill_value=False)
    if prices.empty:
        context["spike_cache"] = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=bool)
        return context["spike_cache"]

    daily_ret = prices.pct_change().where(mature, np.nan)
    spike = daily_ret.rolling(20, min_periods=1).max() > 0.075

    context["spike_cache"] = spike.fillna(False)
    return context["spike_cache"]


def _ensure_risk_cache(context: dict) -> dict:
    """Pre-compute MA200 risk-on/off for every date."""
    if context["risk_cache"] is not None:
        return context["risk_cache"]

    bench = context["benchmark_aligned"]
    dates = context["all_dates"]
    risk_series = {}
    for date in dates:
        hist = bench[bench.index <= date]
        if len(hist) < 200:
            risk_series[date] = True
        else:
            ma200 = float(hist.tail(200).mean())
            risk_series[date] = float(hist.iloc[-1]) > ma200
    context["risk_cache"] = risk_series
    return risk_series


def _compute_buy_weights(
    weight_scheme,
    to_buy_ranked: list[str],
    scores: dict[str, float],
    vol_frame: Optional[pd.DataFrame],
    date: pd.Timestamp = None,
) -> list[float]:
    scheme = _normalize_weight_scheme(weight_scheme)
    n = len(to_buy_ranked)
    if n == 0:
        return []
    if scheme == "equal":
        return [1.0 / n] * n
    elif scheme == "pyramid":
        raw_w = [3] + [1] * (n - 1)
    elif scheme == "momentum":
        raw_w = [max(float(scores.get(sector, 0.0)), 0.0) for sector in to_buy_ranked]
        if sum(raw_w) <= 0:
            raw_w = [1.0] * n
    elif scheme == "top_heavy":
        raw_w = [1.0 / (i + 1) for i in range(n)]
    elif scheme == "inv_vol":
        raw_w = []
        if vol_frame is not None and date in vol_frame.index:
            for sector in to_buy_ranked:
                vol = vol_frame.at[date, sector] if sector in vol_frame.columns else np.nan
                if pd.isna(vol) or float(vol) <= 0:
                    raw_w.append(0.0)
                else:
                    raw_w.append(1.0 / float(vol))
        if sum(raw_w) <= 0:
            raw_w = [1.0 / n] * n
    else:
        raw_w = [1.0 / n] * n
    total = sum(raw_w)
    return [w / total if total > 0 else 1.0 / n for w in raw_w]


def _normalize_weight_scheme(weight_scheme) -> str:
    mapping = {
        "equal": "equal", "momentum": "momentum", "inv_vol": "inv_vol",
        "pyramid": "pyramid", "top_heavy": "top_heavy",
    }
    return mapping.get(weight_scheme, "equal")


def summarize_backtest_result(
    curve: list[dict],
    trades: list[dict],
    round_trips: list[dict],
    benchmark: pd.DataFrame,
) -> dict:
    """从 equity_curve + trades 计算所有回测指标。

    ADR-4: 数据块 ≤1 MB — equity_curve 采样压缩 (≤500 点)."""
    nav_arr = np.array([pt["nav"] for pt in curve])
    n = len(nav_arr)

    if n < 20:
        return {
            "metrics": {"total_return": 0, "annual_return": 0, "sharpe": 0,
                        "max_drawdown": 0, "win_rate": 0, "num_trades": 0},
            "equity_curve": curve,
            "trades": trades,
            "round_trips": round_trips,
            "trade_stats": {},
            "yearly": {},
            "monthly": {},
        }

    # 基本收益
    total_return = float((nav_arr[-1] / nav_arr[0] - 1) * 100)
    days = len(curve)
    annual_return = float(((nav_arr[-1] / nav_arr[0]) ** (252 / max(days, 1)) - 1) * 100)

    # 最大回撤
    peak = np.maximum.accumulate(nav_arr)
    drawdowns = (nav_arr - peak) / peak * 100
    max_dd = float(drawdowns.min())

    # Sharpe
    daily_rets = np.diff(nav_arr) / nav_arr[:-1]
    sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)) if np.std(daily_rets) > 0 else 0.0

    # 交易统计
    num_trades = len(round_trips) if round_trips else len([t for t in trades if t.get("action") == "买入"])
    wins = sum(1 for rt in (round_trips or []) if rt.get("pnl", 0) > 0)
    win_rate = round(wins / max(num_trades, 1) * 100, 1)

    # 分年/分月
    if curve:
        curve_df = pd.DataFrame(curve)
        curve_df["date"] = pd.to_datetime(curve_df["date"])
        curve_df["year"] = curve_df["date"].dt.year
        yearly = {}
        for year, grp in curve_df.groupby("year"):
            first_nav = grp["nav"].iloc[0]
            last_nav = grp["nav"].iloc[-1]
            yearly[str(year)] = round(float((last_nav / first_nav - 1) * 100), 2)

        curve_df["month"] = curve_df["date"].dt.strftime("%Y-%m")
        monthly = {}
        for month, grp in curve_df.groupby("month"):
            first_nav = grp["nav"].iloc[0]
            last_nav = grp["nav"].iloc[-1]
            monthly[str(month)] = round(float((last_nav / first_nav - 1) * 100), 2)
    else:
        yearly, monthly = {}, {}

    # 交易往返统计
    trade_stats = {}
    if round_trips:
        avg_hold = np.mean([rt.get("hold_days", 0) for rt in round_trips])
        avg_win = np.mean([rt["pnl"] for rt in round_trips if rt.get("pnl", 0) > 0]) if wins > 0 else 0
        avg_loss = np.mean([rt["pnl"] for rt in round_trips if rt.get("pnl", 0) <= 0]) if num_trades - wins > 0 else 0
        total_win = sum(rt["pnl"] for rt in round_trips if rt.get("pnl", 0) > 0)
        total_loss = abs(sum(rt["pnl"] for rt in round_trips if rt.get("pnl", 0) <= 0))
        trade_stats = {
            "round_trip_count": num_trades,
            "trade_win_rate": round(win_rate, 1),
            "avg_hold_days": round(float(avg_hold), 1),
            "avg_win_pnl": round(float(avg_win), 2),
            "avg_loss_pnl": round(float(avg_loss), 2),
            "avg_round_trip_return": round(float(np.mean([rt.get("pnl_pct", 0) for rt in round_trips]) * 100), 2) if round_trips else 0,
            "profit_factor": round(total_win / max(total_loss, 1), 2),
        }

    return {
        "metrics": {
            "total_return": round(total_return, 2),
            "annual_return": round(annual_return, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "win_rate": round(win_rate, 1),
            "num_trades": num_trades,
        },
        "equity_curve": curve,
        "trades": trades,
        "round_trips": round_trips,
        "trade_stats": trade_stats,
        "yearly": yearly,
        "monthly": monthly,
    }


def _compute_rps_from_frame(
    score_frame: pd.DataFrame,
    date: pd.Timestamp,
) -> Dict[str, float]:
    """从已计算的评分 DataFrame 推算 RPS（排名百分位）"""
    if date not in score_frame.index:
        return {}
    row = score_frame.loc[date]
    values = {s: float(v) for s, v in row.dropna().items()}
    if len(values) < 2:
        return {s: 50.0 for s in values}
    sorted_items = sorted(values.items(), key=lambda x: -x[1])
    total = len(sorted_items)
    return {s: (total - i) / total * 100 for i, (s, _) in enumerate(sorted_items)}


def backtest_sector_rotation(
    etf_data: Dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    *,
    top_n: int = 5,
    rebal_days: int = 1,
    min_hold: int = 15,
    score_mode: str = "rank_momentum",
    initial_capital: float = 100_000,
    rank_offset: int = 0,
    max_offensive: Optional[int] = 4,
    exclude_sectors: Optional[Set[str]] = None,
    weight_scheme: str = "equal",
    ma200_risk: bool = False,
    spike_filter: bool = False,
    consec_down_exit: int = 0,
    backtest_start: Optional[str] = None,
    backtest_end: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict:
    """
    行业赛道轮动回测引擎。

    Returns:
        {metrics, equity_curve, trades, round_trips, trade_stats, yearly, monthly}
    """
    context = context or prepare_backtest_context(etf_data, benchmark)
    all_dates = context["all_dates"]
    if len(all_dates) < 100:
        return {"error": "数据不足"}

    is_sector_rotation = (score_mode == "sector_rotation")

    # 过滤日期范围
    if backtest_start:
        all_dates = [d for d in all_dates if d >= pd.Timestamp(backtest_start)]
    if backtest_end:
        all_dates = [d for d in all_dates if d <= pd.Timestamp(backtest_end)]

    price_cache = context["price_cache"]
    amount_cache = context.get("amount_cache", {})
    aligned_amounts = context.get("aligned_amounts")

    score_frame = _ensure_score_cache(context, score_mode)
    spike_frame = _ensure_spike_cache(context) if spike_filter else None
    risk_series = _ensure_risk_cache(context) if ma200_risk else None

    exclude_set = exclude_sectors or set()
    vol_frame = None

    cash = initial_capital
    holdings: dict = {}
    trades = []
    round_trips = []
    closed_round_trips = {}
    curve = []

    prev_date = None
    rebal_counter = 0  # 从0开始，首日立即调仓

    for date_idx, date in enumerate(all_dates):
        if date < all_dates[0] + pd.Timedelta(days=60):
            prev_date = date
            curve.append({
                "date": date.strftime("%Y-%m-%d"),
                "nav": 1.0,
                "cash_pct": 1.0,
                "n_sectors": 0,
            })
            continue

        date_str = date.strftime("%Y-%m-%d")

        # 每日跟踪连跌
        if prev_date is not None and holdings:
            for sector, info in holdings.items():
                cur_p = price_cache.get(sector, {}).get(date)
                prev_p = price_cache.get(sector, {}).get(prev_date)
                if cur_p and prev_p:
                    if cur_p < prev_p:
                        info["consec_down"] = info.get("consec_down", 0) + 1
                    else:
                        info["consec_down"] = 0

            consec_sells = [
                sector
                for sector, info in holdings.items()
                if info.get("consec_down", 0) >= consec_down_exit
            ]
            for sector in consec_sells:
                close_position = lambda s, reason: _close_internal(
                    s, reason, holdings, price_cache, date, date_idx, trades, round_trips, closed_round_trips, cash
                )
                cash = _close_internal(
                    sector, f"卖出(连跌{holdings[sector].get('consec_down', 0)}天)",
                    holdings, price_cache, date, date_idx, trades, round_trips, closed_round_trips, cash
                )
            consec_down_sold_today = set(consec_sells)
        else:
            consec_down_sold_today = set()

        portfolio_value = cash
        for sector, info in holdings.items():
            price = price_cache.get(sector, {}).get(date)
            if price:
                portfolio_value += info["shares"] * price

        nav = portfolio_value / initial_capital
        curve.append({
            "date": date_str,
            "nav": round(nav, 6),
            "cash_pct": round(cash / max(portfolio_value, 1), 4),
            "n_sectors": len(holdings),
        })

        prev_date = date
        rebal_counter += 1
        if rebal_counter < rebal_days:
            continue
        rebal_counter = 0

        if ma200_risk and risk_series is not None and not bool(risk_series.get(date, True)):
            for sector in list(holdings.keys()):
                cash = _close_internal(
                    sector, "卖出(Risk-OFF)", holdings, price_cache,
                    date, date_idx, trades, round_trips, closed_round_trips, cash
                )
            continue

        if date not in score_frame.index:
            continue
        score_row = score_frame.loc[date]
        scores = {
            sector: float(value)
            for sector, value in score_row.dropna().items()
        }
        if not scores:
            continue

        # ─── 共享的 Spike 过滤 ───
        if exclude_set:
            scores = {s: sc for s, sc in scores.items() if s not in exclude_set}
        if spike_filter and spike_frame is not None and date in spike_frame.index:
            spike_row = spike_frame.loc[date]
            spiked = set(spike_row[spike_row.astype(bool)].index.tolist())
            scores = apply_spike_filter(scores, spiked)

        # ─── 选股：根据模式分流 ───
        if is_sector_rotation:
            # 板块轮动模式：RPS + 量比双确认
            rps_20 = _compute_rps_from_frame(score_frame, date)
            vol_info = _compute_vol_info_backtest(amount_cache, date)
            target_set = select_sector_rotation_targets(
                scores, context["etf_data"], date, top_n, rps_20, vol_info
            )
        else:
            # 原有动量模式：评分排序取 Top N
            target_set = select_top_n(scores, top_n, rank_offset, exclude_set)

        target_set -= consec_down_sold_today

        # ─── 共享的进攻型限制 ───
        if max_offensive is not None and target_set:
            holdings_set = set(holdings.keys())
            target_set = apply_offensive_limit(target_set, holdings_set, max_offensive, scores)

        # 卖出
        to_sell = []
        for sector, info in holdings.items():
            if sector not in target_set:
                hold_days = max(date_idx - info["buy_idx"], 0)
                if hold_days >= min_hold:
                    to_sell.append(sector)
        for sector in to_sell:
            cash = _close_internal(
                sector, "卖出", holdings, price_cache,
                date, date_idx, trades, round_trips, closed_round_trips, cash
            )

        # 买入
        to_buy = [s for s in target_set if s not in holdings]
        if to_buy and cash > 0:
            to_buy_ranked = sorted(to_buy, key=lambda s: -scores.get(s, 0))
            weights = _compute_buy_weights(weight_scheme, to_buy_ranked, scores, vol_frame, date)
            total_cash = cash
            for sector, weight in zip(to_buy_ranked, weights):
                price = price_cache.get(sector, {}).get(date)
                if price and price > 0:
                    shares = int(total_cash * weight / price / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * price
                    cash -= cost
                    holdings[sector] = {
                        "shares": shares,
                        "buy_date": date,
                        "buy_idx": date_idx,
                        "buy_price": float(price),
                        "consec_down": 0,
                    }
                    trades.append({
                        "date": date_str,
                        "sector": sector,
                        "action": "买入",
                        "shares": shares,
                        "price": round(price, 4),
                        "amount": round(cost, 2),
                        "hold_days": 0,
                        "pnl": 0,
                    })

    return summarize_backtest_result(curve, trades, round_trips, context["benchmark"])


def _close_internal(
    sector, reason, holdings, price_cache, date, date_idx,
    trades, round_trips, closed_round_trips, cash,
) -> float:
    """内部平仓函数，返回更新后的 cash"""
    info = holdings.pop(sector)
    price = price_cache.get(sector, {}).get(date)
    if not price:
        return cash
    sell_amount = info["shares"] * price
    cash += sell_amount
    hold_days = max(date_idx - info["buy_idx"], 0)
    pnl = sell_amount - info["shares"] * info["buy_price"]
    pnl_pct = pnl / (info["shares"] * info["buy_price"]) if info["buy_price"] > 0 else 0.0
    trades.append({
        "date": date.strftime("%Y-%m-%d"),
        "sector": sector,
        "action": "卖出",
        "shares": info["shares"],
        "price": round(price, 4),
        "amount": round(sell_amount, 2),
        "hold_days": hold_days,
        "pnl": round(pnl, 2),
    })
    round_trip_key = (sector, info["buy_date"].strftime("%Y-%m-%d"))
    if round_trip_key not in closed_round_trips:
        round_trips.append({
            "sector": sector,
            "buy_date": info["buy_date"].strftime("%Y-%m-%d"),
            "sell_date": date.strftime("%Y-%m-%d"),
            "hold_days": hold_days,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 6),
        })
        closed_round_trips[round_trip_key] = True
    return cash


def _compute_vol_info_backtest(amount_cache: dict, date: pd.Timestamp) -> dict:
    """回测中动态计算量比信息"""
    result = {}
    short_w = 5
    long_w = 60
    for sector, amts in amount_cache.items():
        dates = sorted(amts.keys())
        if len(dates) < long_w + short_w:
            continue
        pos = next((i for i, d in enumerate(dates) if d > date), len(dates)) - 1
        if pos < long_w + short_w - 1:
            continue
        hist = [amts[d] for d in dates[max(0, pos - long_w - short_w + 1):pos + 1]]
        if len(hist) < long_w + short_w:
            continue
        short_avg = float(np.mean(hist[-short_w:]))
        long_avg = float(np.mean(hist[-long_w:]))
        ratio = short_avg / long_avg if long_avg > 0 else 1.0
        result[sector] = {"ratio": round(ratio, 4), "consec_above_13": 3 if ratio >= 1.3 else 0}
    return result
