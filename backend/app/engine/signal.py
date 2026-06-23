# 作者：相空
"""
信号生成引擎 — generate_signal(etf_data, benchmark, params, holdings, cash)

ADR-1: 信号生成读取 holdings 作为持仓上下文。
支持四种评分模式: rank_momentum / mixed / pure20 / sector_rotation
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from .universe import SECTOR_MAP, OFFENSIVE_SECTORS
from .scoring import compute_sector_scores, _momentum, _get_closes
from .risk import is_risk_on_ma200, get_ma200_status, detect_spike_sectors, compute_consec_down
from .indicators import compute_indicators
from .data import get_etf_constituents, get_stocks_daily, get_total_market_turnover
from .selection import (
    filter_positive,
    select_top_n,
    apply_offensive_limit,
    apply_spike_filter,
    select_sector_rotation_targets,
    should_exit_sector_rotation,
    compute_rps,
    compute_volume_ratio,
    compute_surge_threshold,
)


# --- 工具 -------------------------------------------------

def _count_trading_days(buy_dt, latest_dt, all_dates):
    count = 0
    for d in all_dates:
        if buy_dt < d <= latest_dt:
            count += 1
    return count


def _latest_price(etf_data, sector, date):
    code = SECTOR_MAP.get(sector)
    if not code or code not in etf_data:
        return 0.0
    df = etf_data[code]
    row = df[df["date"] <= date].tail(1)
    return float(row["close"].iloc[0]) if not row.empty else 0.0


# --- 异常涨幅扩散检测（全量扫描）----------------------------

def _detect_surge_diffusion_all(etf_data, latest_date):
    """全量扫描所有30个板块的异常涨幅扩散信号。成分股和个股日线缓存到DB，首次慢后续快。"""
    end_date = latest_date.strftime("%Y-%m-%d")
    start_date = (latest_date - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    result = {}

    all_stock_codes = set()
    sector_stocks = {}
    for sector, code in SECTOR_MAP.items():
        constituents = get_etf_constituents(code)
        if not constituents or len(constituents) < 5:
            continue
        top_stocks = [c["stock_code"] for c in constituents[:20]]
        sector_stocks[sector] = top_stocks
        all_stock_codes.update(top_stocks)

    if not all_stock_codes:
        return result

    stock_data = get_stocks_daily(list(all_stock_codes), start_date, end_date, force_refresh=False)

    for sector, stock_codes in sector_stocks.items():
        today_surge = 0
        yesterday_surge = 0
        for sc in stock_codes:
            sdf = stock_data.get(sc)
            if sdf is None or len(sdf) < 2:
                continue
            threshold = compute_surge_threshold(sc)
            today = sdf[sdf["date"] <= latest_date]
            if len(today) < 2:
                continue
            today_ret = float(today["close"].iloc[-1] / today["close"].iloc[-2] - 1)
            if today_ret >= threshold:
                today_surge += 1
            yesterday = today.iloc[:-1]
            if len(yesterday) >= 2:
                yest_ret = float(yesterday["close"].iloc[-1] / yesterday["close"].iloc[-2] - 1)
                if yest_ret >= threshold:
                    yesterday_surge += 1

        # 严格扩散判定：>= 3只异常涨幅 且 当日 >= 前日 (连续扩散趋势)
        has_diffusion = today_surge >= 3 and (yesterday_surge == 0 or today_surge >= yesterday_surge)
        result[sector] = {
            "surge_count": today_surge,
            "prev_count": yesterday_surge,
            "has_diffusion": has_diffusion,
        }

    return result


# --- 主入口 ------------------------------------------------

def generate_signal(etf_data, benchmark, params, holdings, cash):
    top_n = params.get("top_n", 5)
    score_mode = params.get("score_mode", "rank_momentum")
    min_hold = params.get("min_hold", 15)
    max_offensive = params.get("max_offensive", 4)
    ma200_risk = params.get("ma200_risk", True)
    spike_filter = params.get("spike_filter", True)
    consec_down_exit = params.get("consec_down", 3)
    is_sector_rotation = (score_mode == "sector_rotation")

    # 1. 最新数据日期
    latest_date = None
    for df in etf_data.values():
        if not df.empty:
            d = df["date"].max()
            if latest_date is None or d > latest_date:
                latest_date = d
    if latest_date is None:
        return {"error": "无数据"}

    date_str = latest_date.strftime("%Y-%m-%d")
    is_stale = latest_date.date() < datetime.now().date()

    # 2. 评分 + 动量
    scores = compute_sector_scores(etf_data, latest_date, score_mode)
    mom_20d = _momentum(etf_data, latest_date, 20)
    mom_5d = _momentum(etf_data, latest_date, 5)

    # 板块轮动额外数据
    rps_20 = compute_rps(etf_data, latest_date, 20) if is_sector_rotation else None
    rps_60 = compute_rps(etf_data, latest_date, 60) if is_sector_rotation else None

    # 拉取全市场成交额（用于两步量比公式）
    market_turnover = None
    if is_sector_rotation:
        lookback_start = (latest_date - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        market_turnover = get_total_market_turnover(lookback_start, date_str)

    vol_info = compute_volume_ratio(etf_data, latest_date, market_turnover) if is_sector_rotation else None

    # 3. 价格
    prices = {sector: _latest_price(etf_data, sector, latest_date) for sector in SECTOR_MAP}

    # 4. 市场状态
    n_pos = sum(1 for v in mom_20d.values() if v > 0)
    n_total = len(mom_20d) or 1
    sentiment_pct = n_pos / n_total * 100
    if sentiment_pct >= 70:
        sentiment_level, sentiment_emoji = "strong", "bull"
    elif sentiment_pct >= 40:
        sentiment_level, sentiment_emoji = "neutral", "neutral"
    else:
        sentiment_level, sentiment_emoji = "weak", "bear"

    ma200_status = get_ma200_status(benchmark, latest_date)
    csi300_mom = 0.0
    if not benchmark.empty:
        bh = benchmark[benchmark["date"] <= latest_date]
        if len(bh) >= 21:
            csi300_mom = float(bh["close"].iloc[-1] / bh["close"].iloc[-21] - 1)

    spiked_sectors = detect_spike_sectors(etf_data, latest_date) if spike_filter else set()
    if spiked_sectors:
        scores = apply_spike_filter(scores, spiked_sectors)

    risk_off = ma200_risk and not benchmark.empty and not is_risk_on_ma200(benchmark, latest_date)

    market_info = {
        "sentiment_level": sentiment_level,
        "sentiment_pct": round(sentiment_pct, 1),
        "breadth": f"{n_pos}/{n_total}",
        "ma200_status": ma200_status,
        "csi300_mom": round(csi300_mom * 100, 2),
        "spike_sectors": sorted(spiked_sectors),
        "risk_off": risk_off,
    }

    if is_sector_rotation and vol_info:
        market_info["vol_ratio_above_13"] = sum(1 for v in vol_info.values() if v.get("ratio", 0) >= 1.3)
        market_info["market_turnover_available"] = market_turnover is not None and len(market_turnover) > 0
        market_info["rps_85_count"] = sum(1 for v in (rps_20 or {}).values() if v >= 85)

    # 板块轮动：全量异常涨幅扩散检测
    surge_diffusion = _detect_surge_diffusion_all(etf_data, latest_date) if is_sector_rotation else {}
    if surge_diffusion:
        market_info["surge_sectors"] = [s for s, d in surge_diffusion.items() if d["has_diffusion"]]

    # 5. 选股
    if is_sector_rotation:
        ideal_targets = select_sector_rotation_targets(
            scores, etf_data, latest_date, top_n,
            rps_20=rps_20, rps_60=rps_60,
            vol_info=vol_info, surge_diffusion=surge_diffusion,
            params=params,
        )
    else:
        ideal_targets = select_top_n(scores, top_n)

    if risk_off:
        ideal_targets = set()

    if max_offensive is not None and ideal_targets:
        holdings_set = set(h["sector"] for h in holdings)
        ideal_targets = apply_offensive_limit(ideal_targets, holdings_set, max_offensive, scores)

    if not is_sector_rotation and max_offensive is not None and len(ideal_targets) < top_n:
        positive = [(s, sc) for s, sc in sorted(scores.items(), key=lambda x: -x[1]) if sc > 0]
        remaining = [(s, sc) for s, sc in positive if s not in ideal_targets and s not in OFFENSIVE_SECTORS]
        for s, sc in remaining[:top_n - len(ideal_targets)]:
            ideal_targets.add(s)

    # 6. 排名
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    rn = {s: i for i, (s, _) in enumerate(sorted_scores)}
    rankings = []
    for i, (sector, _) in enumerate(sorted_scores):
        code = SECTOR_MAP.get(sector, "")
        price = prices.get(sector, 0)
        change_pct = 0.0
        if code in etf_data:
            df = etf_data[code]
            hist = df[df["date"] <= latest_date]
            if len(hist) >= 2:
                change_pct = float((hist["close"].iloc[-1] / hist["close"].iloc[-2] - 1) * 100)

        entry = {
            "rank": i + 1, "sector": sector, "code": code,
            "score": round(scores.get(sector, 0), 6),
            "mom20": round(mom_20d.get(sector, 0) * 100, 2),
            "mom5": round(mom_5d.get(sector, 0) * 100, 2),
            "rank_change": rn.get(sector, i) - rn.get(sector, i),
            "price": round(price, 4),
            "change_pct": round(change_pct, 2),
            "in_target": sector in ideal_targets,
            "spiked": sector in spiked_sectors,
        }
        if is_sector_rotation:
            entry["rps_20"] = round(rps_20.get(sector, 0), 1) if rps_20 else None
            entry["rps_60"] = round(rps_60.get(sector, 0), 1) if rps_60 else None
            if vol_info and sector in vol_info:
                entry["vol_ratio"] = vol_info[sector]["ratio"]
                entry["vol_consec_above"] = vol_info[sector]["consec_above_13"]
            if sector in surge_diffusion:
                entry["surge_diffusion"] = surge_diffusion[sector]
        rankings.append(entry)

    # 7. 交易日历
    best_code = max(etf_data.keys(), key=lambda c: len(etf_data[c]))
    td_dates = sorted(etf_data[best_code]["date"].dropna().tolist())

    # 8. 根据持仓生成 actions
    holdings_map = {h["sector"]: h for h in holdings}
    sell_list, locked_list, hold_list = [], [], []

    for sector, h in holdings_map.items():
        buy_dt = pd.Timestamp(h["buy_date"])
        hold_days = _count_trading_days(buy_dt, latest_date, td_dates)
        cd = compute_consec_down(etf_data, sector, latest_date) if consec_down_exit > 0 else 0
        cur_price = prices.get(sector, 0)
        buy_price = h.get("buy_price", 0)
        pnl_pct = (cur_price / buy_price - 1) if buy_price > 0 else 0.0
        pnl = (cur_price - buy_price) * h.get("shares", 0) if buy_price > 0 else 0.0

        base = {
            "holding_id": h.get("id"), "sector": sector,
            "code": h.get("code", SECTOR_MAP.get(sector, "")),
            "hold_days": hold_days, "buy_price": round(buy_price, 4),
            "cur_price": round(cur_price, 4), "shares": h.get("shares", 0),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 6), "consec_down": cd,
        }

        exit_rot = is_sector_rotation and rps_20 and vol_info and should_exit_sector_rotation(sector, rps_20, vol_info, params)

        if risk_off:
            sell_list.append({**base, "reason": "Risk-OFF"})
        elif exit_rot:
            sell_list.append({**base, "reason": "轮动结束(RPS或量比)"})
        elif consec_down_exit > 0 and cd >= consec_down_exit:
            sell_list.append({**base, "reason": f"连跌{cd}天"})
        elif sector not in ideal_targets:
            if hold_days >= min_hold:
                sell_list.append({**base, "reason": "排名出局"})
            else:
                locked_list.append({**base, "remaining": min_hold - hold_days})
        else:
            hold_list.append(base)

    # 买入
    locked_sectors = set(x["sector"] for x in locked_list)
    kept_sectors = set(x["sector"] for x in hold_list)
    current_held = locked_sectors | kept_sectors
    consec_down_sold = set(x["sector"] for x in sell_list if x["reason"].startswith("连跌"))
    buy_targets = [s for s in ideal_targets if s not in current_held and s not in consec_down_sold]
    is_first = not holdings_map
    buy_list = []

    if is_first and ideal_targets:
        alloc = cash / max(len(ideal_targets), 1)
        for sector in sorted(ideal_targets, key=lambda s: -scores.get(s, 0)):
            price = prices.get(sector, 0)
            if price > 0:
                shares = int(alloc / price / 100) * 100
                entry = {
                    "sector": sector, "code": SECTOR_MAP.get(sector, ""),
                    "price": round(price, 4), "suggested_shares": shares,
                    "amount": round(shares * price, 2),
                    "mom20": round(mom_20d.get(sector, 0) * 100, 2),
                    "score": round(scores.get(sector, 0), 6),
                }
                if is_sector_rotation:
                    entry["rps_20"] = round(rps_20.get(sector, 0), 1) if rps_20 else None
                    entry["vol_ratio"] = vol_info[sector]["ratio"] if vol_info and sector in vol_info else None
                    entry["surge"] = surge_diffusion.get(sector, {}).get("has_diffusion", False)
                buy_list.append(entry)
    elif buy_targets:
        freed = sum(x["cur_price"] * x["shares"] for x in sell_list)
        if freed > 0:
            alloc = freed / max(len(buy_targets), 1)
            for sector in sorted(buy_targets, key=lambda s: -scores.get(s, 0)):
                price = prices.get(sector, 0)
                if price > 0:
                    shares = int(alloc / price / 100) * 100
                    entry = {
                        "sector": sector, "code": SECTOR_MAP.get(sector, ""),
                        "price": round(price, 4), "suggested_shares": shares,
                        "amount": round(shares * price, 2),
                        "mom20": round(mom_20d.get(sector, 0) * 100, 2),
                        "score": round(scores.get(sector, 0), 6),
                    }
                    if is_sector_rotation:
                        entry["rps_20"] = round(rps_20.get(sector, 0), 1) if rps_20 else None
                        entry["vol_ratio"] = vol_info[sector]["ratio"] if vol_info and sector in vol_info else None
                        entry["surge"] = surge_diffusion.get(sector, {}).get("has_diffusion", False)
                    buy_list.append(entry)

    result = {
        "date": date_str, "is_stale": is_stale, "params": params,
        "market": market_info, "rankings": rankings,
        "actions": {"sell": sell_list, "locked": locked_list, "hold": hold_list, "buy": buy_list},
        "holdings_snapshot": {h["sector"]: {"code": h.get("code"), "buy_date": h.get("buy_date"),
                                             "buy_price": h.get("buy_price"), "shares": h.get("shares")}
                              for h in holdings},
    }
    if is_sector_rotation:
        result["score_mode"] = "sector_rotation"
        result["surge_diffusion"] = surge_diffusion

    return result
