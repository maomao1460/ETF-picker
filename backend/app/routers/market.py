# 作者：相空
"""
市场概览 API
"""
import math
import time
from datetime import datetime
from threading import Lock

from fastapi import APIRouter

from ..engine.universe import SECTOR_MAP
from ..engine.data import get_etf_data, get_benchmark_data
from ..engine.scoring import get_momentum_20d, get_momentum_5d
from ..engine.risk import get_ma200_status, detect_spike_sectors

router = APIRouter(prefix="/api/market", tags=["market"])

LOOKBACK_START = "2020-01-01"
REFRESH_COOLDOWN_SECONDS = 30

_refresh_lock = Lock()
_refresh_state_lock = Lock()
_last_refresh_at = 0.0
_last_refresh_result: dict | None = None


def _get_refresh_state() -> tuple[float, dict | None]:
    with _refresh_state_lock:
        return _last_refresh_at, dict(_last_refresh_result) if _last_refresh_result else None


def _set_refresh_state(result: dict):
    global _last_refresh_at, _last_refresh_result
    with _refresh_state_lock:
        _last_refresh_at = time.time()
        _last_refresh_result = dict(result)


def _decorate_refresh_result(result: dict, *, reused: bool, waited: bool) -> dict:
    last_refresh_at, _ = _get_refresh_state()
    cooldown_seconds = 0
    if last_refresh_at > 0:
        cooldown_seconds = max(
            int(math.ceil(REFRESH_COOLDOWN_SECONDS - (time.time() - last_refresh_at))),
            0,
        )

    payload = dict(result)
    payload["reused"] = reused
    payload["waited"] = waited
    payload["cooldown_seconds"] = cooldown_seconds
    return payload


def _run_refresh() -> dict:
    end = datetime.now().strftime("%Y-%m-%d")
    codes = list(SECTOR_MAP.values())
    etf_data = get_etf_data(codes, LOOKBACK_START, end, force_refresh=True)
    benchmark = get_benchmark_data(LOOKBACK_START, end, force_refresh=True)
    return {
        "etf_count": len(etf_data),
        "benchmark_rows": len(benchmark) if not benchmark.empty else 0,
        "refreshed_at": datetime.now().isoformat(),
    }


@router.get("/overview")
def market_overview():
    """市场概览: 情绪 + MA200 + 赛道热力图"""
    end = datetime.now().strftime("%Y-%m-%d")
    codes = list(SECTOR_MAP.values())

    etf_data = get_etf_data(codes, LOOKBACK_START, end)
    benchmark = get_benchmark_data(LOOKBACK_START, end)

    if not etf_data:
        return {"error": "无数据"}

    # 找最新日
    import pandas as pd
    latest_date = None
    for code, df in etf_data.items():
        if not df.empty:
            d = df["date"].max()
            if latest_date is None or d > latest_date:
                latest_date = d

    if latest_date is None:
        return {"error": "无数据"}

    # 动量
    mom_20d = get_momentum_20d(etf_data, latest_date)
    mom_5d = get_momentum_5d(etf_data, latest_date)

    n_pos = sum(1 for v in mom_20d.values() if v > 0)
    n_total = len(mom_20d)
    sentiment_pct = n_pos / max(n_total, 1) * 100

    # MA200
    ma200_status = get_ma200_status(benchmark, latest_date) if not benchmark.empty else {}

    # Spikes
    spiked = detect_spike_sectors(etf_data, latest_date)

    # 赛道热力图数据
    heatmap = []
    for sector in SECTOR_MAP:
        heatmap.append({
            "sector": sector,
            "mom20": round(mom_20d.get(sector, 0) * 100, 2),
            "mom5": round(mom_5d.get(sector, 0) * 100, 2),
            "spiked": sector in spiked,
        })
    heatmap.sort(key=lambda x: -x["mom20"])

    return {
        "date": latest_date.strftime("%Y-%m-%d"),
        "sentiment": {
            "level": "strong" if sentiment_pct >= 70 else "neutral" if sentiment_pct >= 40 else "weak",
            "pct": round(sentiment_pct, 1),
            "positive": n_pos,
            "total": n_total,
        },
        "ma200": ma200_status,
        "spike_sectors": sorted(spiked),
        "heatmap": heatmap,
    }


@router.get("/refresh")
def refresh_data():
    """手动触发数据刷新，串行化 force refresh 以避免高频请求"""
    now = time.time()
    last_refresh_at, last_refresh_result = _get_refresh_state()
    if last_refresh_result and now - last_refresh_at < REFRESH_COOLDOWN_SECONDS:
        return _decorate_refresh_result(last_refresh_result, reused=True, waited=False)

    acquired = _refresh_lock.acquire(blocking=False)
    if not acquired:
        with _refresh_lock:
            _, last_refresh_result = _get_refresh_state()
            if last_refresh_result:
                return _decorate_refresh_result(last_refresh_result, reused=True, waited=True)
        _refresh_lock.acquire()
        acquired = True

    try:
        now = time.time()
        last_refresh_at, last_refresh_result = _get_refresh_state()
        if last_refresh_result and now - last_refresh_at < REFRESH_COOLDOWN_SECONDS:
            return _decorate_refresh_result(last_refresh_result, reused=True, waited=False)

        result = _run_refresh()
        _set_refresh_state(result)
        return _decorate_refresh_result(result, reused=False, waited=False)
    finally:
        if acquired:
            _refresh_lock.release()
