# 作者：相空
"""
信号 API — 生成 / 历史查询
"""
import json
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..models import SignalParams
from ..engine.universe import SECTOR_MAP
from ..engine.data import get_etf_data, get_benchmark_data
from ..engine.signal import generate_signal
from ..engine.data import get_etf_constituents, get_stocks_daily

router = APIRouter(prefix="/api/signal", tags=["signal"])

# 默认回看天数
LOOKBACK_START = "2020-01-01"


def _compute_data_freshness(etf_data, codes, signal_result):
    """计算数据新鲜度详情"""
    from datetime import datetime, timedelta
    from ..db import get_db

    now = datetime.now()
    freshness = {
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "etf_data": {},
        "constituent_data": {},
        "stock_data": {},
        "summary": {"total": 0, "stale": 0, "missing": 0, "fresh": 0},
    }

    # ETF 行情数据检查
    total_etfs = 0
    stale_etfs = 0
    for code in codes:
        df = etf_data.get(code)
        if df is None or df.empty:
            freshness["etf_data"][code] = {"status": "missing", "latest_date": None}
            continue
        latest = df["date"].max()
        latest_str = latest.strftime("%Y-%m-%d") if hasattr(latest, "strftime") else str(latest)[:10]
        days_behind = (now.date() - pd.Timestamp(latest).date()).days if hasattr(latest, "strftime") else 99
        status = "fresh" if days_behind <= 1 else "stale"
        freshness["etf_data"][code] = {"status": status, "latest_date": latest_str, "days_behind": days_behind}
        total_etfs += 1
        if status == "stale":
            stale_etfs += 1

    freshness["summary"] = {
        "total_etf": len(codes),
        "with_data": total_etfs,
        "stale_etf": stale_etfs,
        "signal_date": signal_result.get("date"),
        "is_today": signal_result.get("is_stale", True) == False,
    }

    # 板块轮动模式额外检查
    if signal_result.get("score_mode") == "sector_rotation":
        from ..engine.universe import SECTOR_MAP
        const_total = 0
        const_missing = 0
        for sector, code in SECTOR_MAP.items():
            constituents = get_etf_constituents(code)
            if constituents:
                const_total += len(constituents)
            else:
                const_missing += 1
        freshness["constituent_data"] = {
            "total_constituents": const_total,
            "etfs_without_constituents": const_missing,
        }

        surge = signal_result.get("surge_diffusion", {})
        sectors_scanned = len(surge)
        sectors_with_diffusion = sum(1 for d in surge.values() if d.get("has_diffusion"))
        freshness["stock_data"] = {
            "sectors_scanned": sectors_scanned,
            "sectors_with_diffusion": sectors_with_diffusion,
        }

    return freshness




def _get_end_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@router.post("/generate")
def gen_signal(params: SignalParams):
    """基于当前市场数据 + 当前 holdings 生成交易信号"""
    end = _get_end_date()
    codes = list(SECTOR_MAP.values())

    # 1. 拉取行情
    etf_data = get_etf_data(codes, LOOKBACK_START, end, force_refresh=params.force_refresh)
    benchmark = get_benchmark_data(LOOKBACK_START, end, force_refresh=params.force_refresh)
    if not etf_data:
        raise HTTPException(500, "无法获取行情数据")

    # 2. 读取当前活跃持仓
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE status='active'"
        ).fetchall()
    holdings = [dict(r) for r in rows]

    # 3. 读取现金
    with get_db() as conn:
        row = conn.execute("SELECT current_cash FROM portfolio_config WHERE id=1").fetchone()
    cash = row["current_cash"] if row else 100_000

    # 4. 生成信号
    result = generate_signal(
        etf_data, benchmark,
        params.model_dump(),
        holdings, cash,
    )

    # 4.5 计算数据新鲜度
    data_freshness = _compute_data_freshness(etf_data, codes, result)

    # 5. 保存到 signals 表 (同一日期只保留一条, 刷新时覆盖更新)
    sig_date = result.get("date")
    params_j = json.dumps(result.get("params"), ensure_ascii=False)
    rankings_j = json.dumps(result.get("rankings"), ensure_ascii=False)
    top_picks_j = json.dumps([r for r in result.get("rankings", []) if r.get("in_target")], ensure_ascii=False)
    market_j = json.dumps(result.get("market"), ensure_ascii=False)
    actions_j = json.dumps(result.get("actions"), ensure_ascii=False)
    snapshot_j = json.dumps(result.get("holdings_snapshot"), ensure_ascii=False)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM signals WHERE date=?", (sig_date,)
        ).fetchone()

        if existing:
            # 同日已有记录 → 覆盖更新
            signal_id = existing["id"]
            conn.execute(
                "UPDATE signals SET params_json=?, rankings_json=?, top_picks_json=?, "
                "market_json=?, actions_json=?, holdings_snapshot_json=?, "
                "created_at=datetime('now','localtime') WHERE id=?",
                (params_j, rankings_j, top_picks_j, market_j, actions_j, snapshot_j, signal_id),
            )
        else:
            # 新日期 → 插入
            conn.execute(
                "INSERT INTO signals (date, params_json, rankings_json, top_picks_json, "
                "market_json, actions_json, holdings_snapshot_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sig_date, params_j, rankings_j, top_picks_j, market_j, actions_j, snapshot_j),
            )
            signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    result["id"] = signal_id
    result["data_freshness"] = data_freshness
    return result


@router.get("/exit-check")
def check_exits(score_mode: str = "rank_momentum"):
    """独立离场检查：监控所有活跃持仓，返回需要离场的持仓列表"""
    from datetime import datetime
    from ..engine.universe import SECTOR_MAP
    from ..engine.data import get_etf_data, get_benchmark_data, get_total_market_turnover
    from ..engine.scoring import compute_sector_scores
    from ..engine.risk import is_risk_on_ma200, compute_consec_down
    from ..engine.selection import compute_rps, compute_volume_ratio, should_exit_sector_rotation

    end = datetime.now().strftime("%Y-%m-%d")
    codes = list(SECTOR_MAP.values())

    # 1. 拉取行情
    etf_data = get_etf_data(codes, "2020-01-01", end)
    benchmark = get_benchmark_data("2020-01-01", end)
    if not etf_data:
        raise HTTPException(500, "无法获取行情数据")

    # 2. 找最新日期
    latest_date = None
    for df in etf_data.values():
        if not df.empty:
            d = df["date"].max()
            if latest_date is None or d > latest_date:
                latest_date = d
    if latest_date is None:
        raise HTTPException(500, "无行情数据")

    date_str = latest_date.strftime("%Y-%m-%d")
    is_stale = latest_date.date() < datetime.now().date()

    # 3. 读取活跃持仓
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM holdings WHERE status='active'").fetchall()
    holdings = [dict(r) for r in rows]
    if not holdings:
        return {"date": date_str, "is_stale": is_stale, "alerts": [], "summary": "无活跃持仓"}

    # 4. 按模式检查离场条件
    alerts = []
    is_sr = (score_mode == "sector_rotation")

    if is_sr:
        rps_20 = compute_rps(etf_data, latest_date, 20)
        rps_60 = compute_rps(etf_data, latest_date, 60)
        lookback_start = (latest_date - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        market_turnover = get_total_market_turnover(lookback_start, date_str)
        vol_info = compute_volume_ratio(etf_data, latest_date, market_turnover)

    # CSI300 MA200
    risk_off = not benchmark.empty and not is_risk_on_ma200(benchmark, latest_date)

    for h in holdings:
        sector = h["sector"]
        code = h.get("code", SECTOR_MAP.get(sector, ""))
        reasons = []

        # 通用：Risk-OFF
        if risk_off:
            reasons.append("Risk-OFF (CSI300 < MA200)")

        if is_sr:
            # 板块轮动离场
            if should_exit_sector_rotation(sector, rps_20, vol_info):
                rps = rps_20.get(sector, 0)
                vi = vol_info.get(sector, {})
                if rps < 50:
                    reasons.append(f"RPS(20)={rps:.0f} 跌破50")
                if vi.get("ratio", 1) < 0.8:
                    reasons.append(f"量比={vi.get('ratio', 1):.2f} 跌破0.8")
        else:
            # 动量模式：连跌检查
            cd = compute_consec_down(etf_data, sector, latest_date)
            if cd >= 3:
                reasons.append(f"连跌{cd}天")

        if reasons:
            cur_price = None
            df = etf_data.get(code)
            if df is not None and not df.empty:
                hist = df[df["date"] <= latest_date]
                if not hist.empty:
                    cur_price = float(hist["close"].iloc[-1])
            alerts.append({
                "holding_id": h["id"],
                "sector": sector,
                "code": code,
                "buy_date": h.get("buy_date"),
                "buy_price": h.get("buy_price"),
                "cur_price": round(cur_price, 4) if cur_price else None,
                "pnl_pct": round((cur_price / h["buy_price"] - 1) * 100, 2) if cur_price and h.get("buy_price") else None,
                "reasons": reasons,
                "urgency": "high" if risk_off else "medium",
            })

    return {
        "date": date_str,
        "is_stale": is_stale,
        "score_mode": score_mode,
        "total_holdings": len(holdings),
        "exit_count": len(alerts),
        "alerts": alerts,
        "summary": f"{len(alerts)}/{len(holdings)} 持仓触发离场" if alerts else "所有持仓正常",
    }


@router.get("/intraday-check")
def intraday_check(score_mode: str = "sector_rotation"):
    """盘中分钟级离场检查：用最近60分钟量比检测资金枯竭"""
    from datetime import datetime, timedelta
    from ..db import get_db
    from ..engine.universe import SECTOR_MAP
    import akshare as ak

    now = datetime.now()
    # 非交易时段直接返回
    if now.weekday() >= 5:
        return {"status": "closed", "message": "非交易日"}
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=0, second=0, microsecond=0)
    if now < market_open:
        return {"status": "pre_market", "message": f"距开盘还有{(market_open - now).seconds // 60}分钟"}
    if now > market_close:
        return {"status": "closed", "message": "已收盘，请用 /exit-check"}

    # 1. 读取活跃持仓
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM holdings WHERE status='active'").fetchall()
    holdings = [dict(r) for r in rows]
    if not holdings:
        return {"status": "ok", "message": "无活跃持仓", "alerts": [], "checked_at": now.strftime("%Y-%m-%d %H:%M:%S")}

    # 2. 逐持仓拉分钟线 + 判断
    alerts = []
    today_str = now.strftime("%Y%m%d")

    for h in holdings:
        code = h.get("code", SECTOR_MAP.get(h["sector"], ""))
        if not code:
            continue

        try:
            df = ak.fund_etf_hist_min_em(symbol=code, period="1", adjust="")
        except Exception as e:
            alerts.append({
                "sector": h["sector"], "code": code,
                "status": "error", "message": f"拉取失败: {e}",
            })
            continue

        if df is None or df.empty:
            continue

        # 列映射
        col_map = {"时间": "time", "成交量": "volume", "收盘": "close"}
        df = df.rename(columns=col_map)
        df["time"] = pd.to_datetime(df["time"])

        # 只保留今天的数据
        today_data = df[df["time"].dt.date == now.date()]
        if len(today_data) < 30:
            # 开盘不到30分钟，数据不足
            continue

        # 最近60分钟成交量
        cutoff = now - timedelta(minutes=60)
        recent = today_data[today_data["time"] >= cutoff]
        if recent.empty:
            continue
        recent_vol = float(recent["volume"].sum())

        # 同期均值：过去20个交易日同一60分钟窗口
        past_volumes = []
        all_dates = sorted(df["time"].dt.date.unique())
        lookback_dates = [d for d in all_dates if d < now.date()][-20:]

        for past_date in lookback_dates:
            past_day = df[df["time"].dt.date == past_date]
            if past_day.empty:
                continue
            # 用过去日的数据，对应同一时间窗口
            past_cutoff_start = past_day["time"].min() + (cutoff - today_data["time"].min())
            past_cutoff_end = past_day["time"].min() + (now - today_data["time"].min())
            past_window = past_day[(past_day["time"] >= past_cutoff_start) & (past_day["time"] <= past_cutoff_end)]
            if past_window.empty:
                continue
            past_volumes.append(float(past_window["volume"].sum()))

        if len(past_volumes) < 5:
            continue

        avg_past_vol = float(np.mean(past_volumes))
        if avg_past_vol <= 0:
            continue

        ratio = recent_vol / avg_past_vol

        # 判断
        cur_price = float(recent["close"].iloc[-1]) if "close" in recent.columns else None
        alert = None
        if ratio < 0.3:
            alert = {
                "sector": h["sector"], "code": code,
                "holding_id": h["id"],
                "status": "emergency",
                "ratio": round(ratio, 3),
                "recent_vol": round(recent_vol, 0),
                "avg_vol": round(avg_past_vol, 0),
                "cur_price": round(cur_price, 4) if cur_price else None,
                "message": f"🚨 量比{ratio:.2f}！成交量仅为同期均值的{ratio*100:.0f}%，资金严重枯竭",
            }
        elif ratio < 0.5:
            alert = {
                "sector": h["sector"], "code": code,
                "holding_id": h["id"],
                "status": "warning",
                "ratio": round(ratio, 3),
                "recent_vol": round(recent_vol, 0),
                "avg_vol": round(avg_past_vol, 0),
                "cur_price": round(cur_price, 4) if cur_price else None,
                "message": f"⚠️ 量比{ratio:.2f}！缩量至同期均值的{ratio*100:.0f}%，注意风险",
            }
        if alert:
            alert["buy_price"] = h.get("buy_price")
            if cur_price and h.get("buy_price"):
                alert["pnl_pct"] = round((cur_price / h["buy_price"] - 1) * 100, 2)
            alerts.append(alert)

        import time
        time.sleep(0.3)  # 避免请求太快

    emergency_count = sum(1 for a in alerts if a.get("status") == "emergency")
    warning_count = sum(1 for a in alerts if a.get("status") == "warning")

    return {
        "status": "ok",
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total_holdings": len(holdings),
        "alerts": alerts,
        "summary": f"检查{len(holdings)}个持仓" + (
            f"，{emergency_count}紧急{warning_count}预警" if alerts else "，全部正常"
        ),
    }


@router.get("/history")
def signal_history(limit: int = 30):
    """历史信号列表 (摘要)"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, date, params_json, market_json, created_at "
            "FROM signals ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = []
    for r in rows:
        market = json.loads(r["market_json"]) if r["market_json"] else {}
        params = json.loads(r["params_json"]) if r["params_json"] else {}
        result.append({
            "id": r["id"],
            "date": r["date"],
            "params_summary": f"Top{params.get('top_n', '?')} {params.get('score_mode', '?')}",
            "market_sentiment": market.get("sentiment_level", ""),
            "created_at": r["created_at"],
        })
    return result


@router.get("/{signal_id}")
def get_signal(signal_id: int):
    """获取完整信号详情"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
    if not row:
        raise HTTPException(404, "信号不存在")

    return {
        "id": row["id"],
        "date": row["date"],
        "params": json.loads(row["params_json"]) if row["params_json"] else None,
        "rankings": json.loads(row["rankings_json"]) if row["rankings_json"] else [],
        "market": json.loads(row["market_json"]) if row["market_json"] else {},
        "actions": json.loads(row["actions_json"]) if row["actions_json"] else {},
        "holdings_snapshot": json.loads(row["holdings_snapshot_json"]) if row["holdings_snapshot_json"] else {},
        "created_at": row["created_at"],
    }
