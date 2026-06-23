# 作者：相空
"""
ETF 详情 / 列表 API
ADR-5: signals_overlay 仅用已保存信号
"""
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..engine.universe import SECTOR_MAP, SECTOR_NAMES
from ..engine.data import get_etf_data, get_latest_price
from ..engine.indicators import compute_indicators

router = APIRouter(prefix="/api/etf", tags=["etf"])

LOOKBACK_START = "2020-01-01"


@router.get("/list")
def etf_list():
    """30 赛道列表 — 含最新价 / 涨跌 / 动量 / 趋势"""
    from ..engine.scoring import get_momentum_20d, get_momentum_5d, compute_sector_scores

    end = datetime.now().strftime("%Y-%m-%d")
    codes = list(SECTOR_MAP.values())

    # 批量加载数据（走缓存，不会每次远程拉取）
    etf_data = get_etf_data(codes, LOOKBACK_START, end)

    # 找最新交易日
    latest_date = None
    for code, df in etf_data.items():
        if not df.empty:
            d = df["date"].max()
            if latest_date is None or d > latest_date:
                latest_date = d

    # 动量
    mom20 = get_momentum_20d(etf_data, latest_date) if latest_date else {}
    mom5 = get_momentum_5d(etf_data, latest_date) if latest_date else {}
    # 策略综合评分 (rank_momentum)
    composite = compute_sector_scores(etf_data, latest_date, "rank_momentum") if latest_date else {}

    result = []
    for sector, code in SECTOR_MAP.items():
        df = etf_data.get(code)
        latest_price = None
        change_pct = None
        trend = "flat"

        if df is not None and not df.empty:
            latest_price = float(df["close"].iloc[-1])
            if len(df) >= 2:
                prev_close = float(df["close"].iloc[-2])
                if prev_close > 0:
                    change_pct = (latest_price / prev_close - 1)

            # 趋势: 5日线方向
            if len(df) >= 5:
                ma5 = float(df["close"].tail(5).mean())
                dist_ma5 = (latest_price / ma5 - 1) if ma5 > 0 else None
                if latest_price > ma5 * 1.005:
                    trend = "up"
                elif latest_price < ma5 * 0.995:
                    trend = "down"
            else:
                dist_ma5 = None

            # MA20 偏离
            if len(df) >= 20:
                ma20 = float(df["close"].tail(20).mean())
                dist_ma20 = (latest_price / ma20 - 1) if ma20 > 0 else None
            else:
                dist_ma20 = None
        else:
            dist_ma5 = None
            dist_ma20 = None

        result.append({
            "sector": sector,
            "code": code,
            "name": SECTOR_NAMES.get(sector, sector),
            "latest_price": round(latest_price, 4) if latest_price else None,
            "change_pct": round(change_pct, 6) if change_pct is not None else None,
            "mom20": round(mom20.get(sector, 0), 6) if sector in mom20 else None,
            "mom5": round(mom5.get(sector, 0), 6) if sector in mom5 else None,
            "score": round(composite.get(sector, 0), 6) if sector in composite else None,
            "dist_ma5": round(dist_ma5, 6) if dist_ma5 is not None else None,
            "dist_ma20": round(dist_ma20, 6) if dist_ma20 is not None else None,
            "trend": trend,
        })

    # 按综合评分排序（与信号选股逻辑一致），无评分则用 mom20 兜底
    result.sort(key=lambda x: -(x["score"] if x["score"] is not None else (x["mom20"] or -999)))

    # ── 市场宽度统计 ──
    n_up   = sum(1 for r in result if r["trend"] == "up")
    n_down = sum(1 for r in result if r["trend"] == "down")
    n_flat = sum(1 for r in result if r["trend"] == "flat")
    total  = len(result) or 1
    down_ratio = n_down / total
    up_ratio   = n_up / total

    # 信号判定 (基于 5 年回测研究)
    # 下跌占比 >= 70%: 后 5 日上涨概率 65.7%, 均值 +0.94% → 买入信号
    # 上涨占比 >= 65%: 后 5 日表现仅 53.8%, 低于基准 → 警惕信号
    if down_ratio >= 0.70:
        signal = "buy"       # 超卖反弹信号
    elif up_ratio >= 0.65:
        signal = "caution"   # 短期涨幅过大, 警惕回调
    else:
        signal = "neutral"

    return {
        "breadth": {
            "n_up": n_up,
            "n_down": n_down,
            "n_flat": n_flat,
            "total": total,
            "down_ratio": round(down_ratio, 4),
            "up_ratio": round(up_ratio, 4),
            "signal": signal,
        },
        "items": result,
    }


@router.get("/{code}/chart")
def etf_chart(code: str, days: int = 250):
    """单只 ETF 走势 + 指标 + 信号历史"""
    # 找到 sector
    sector = None
    for s, c in SECTOR_MAP.items():
        if c == code:
            sector = s
            break
    if not sector:
        raise HTTPException(404, f"ETF {code} 不在赛道列表中")

    end = datetime.now().strftime("%Y-%m-%d")
    etf_data = get_etf_data([code], LOOKBACK_START, end)
    if code not in etf_data or etf_data[code].empty:
        raise HTTPException(404, "无数据")

    df = etf_data[code]
    # 取最近 days 行
    df = df.tail(days).reset_index(drop=True)

    # 指标
    indicators = compute_indicators(df)

    # 价格序列
    prices = []
    for _, row in df.iterrows():
        prices.append({
            "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
            "open": round(float(row["open"]), 4) if row.get("open") else None,
            "high": round(float(row["high"]), 4) if row.get("high") else None,
            "low": round(float(row["low"]), 4) if row.get("low") else None,
            "close": round(float(row["close"]), 4),
            "volume": float(row["volume"]) if row.get("volume") else None,
        })

    # 信号历史 (ADR-5: 从 signals 表)
    signals_history = []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, date, actions_json, rankings_json FROM signals ORDER BY date DESC LIMIT 100"
        ).fetchall()

    for r in rows:
        actions = json.loads(r["actions_json"]) if r["actions_json"] else {}
        rankings = json.loads(r["rankings_json"]) if r["rankings_json"] else []
        ranking_map = {
            item.get("sector"): item
            for item in rankings
            if isinstance(item, dict) and item.get("sector")
        }
        for action_type in ["sell", "buy"]:
            for a in actions.get(action_type, []):
                if a.get("sector") == sector or a.get("code") == code:
                    rank_info = ranking_map.get(sector, {})
                    signals_history.append({
                        "date": r["date"],
                        "action": action_type,
                        "action_label": "卖出" if action_type == "sell" else "买入",
                        "signal_id": r["id"],
                        "rank": rank_info.get("rank"),
                        "score": rank_info.get("score"),
                    })

    # 当前持仓
    current_holding = None
    with get_db() as conn:
        h = conn.execute(
            "SELECT * FROM holdings WHERE code=? AND status='active' LIMIT 1",
            (code,),
        ).fetchone()
    if h:
        buy_dt = datetime.strptime(h["buy_date"], "%Y-%m-%d")
        cur_price = get_latest_price(code) or 0
        current_holding = {
            "id": h["id"],
            "buy_date": h["buy_date"],
            "buy_price": h["buy_price"],
            "shares": h["shares"],
            "hold_days": (datetime.now() - buy_dt).days,
            "pnl_pct": round((cur_price / h["buy_price"] - 1) * 100, 2) if h["buy_price"] > 0 and cur_price > 0 else 0,
        }

    return {
        "sector": sector,
        "code": code,
        "name": SECTOR_NAMES.get(sector, sector),
        "prices": prices,
        "indicators": indicators,
        "signals_history": signals_history,
        "current_holding": current_holding,
    }
