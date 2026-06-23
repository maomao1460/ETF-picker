# 作者：相空
"""
风控模块 — MA200 / Spike / 连续下跌
"""
from typing import Dict, Set

import numpy as np
import pandas as pd

from .universe import SECTOR_MAP
from .scoring import _get_closes


def is_risk_on_ma200(benchmark: pd.DataFrame, date: pd.Timestamp) -> bool:
    """CSI300 是否在 MA200 之上 (Risk-ON)"""
    if benchmark.empty:
        return True
    hist = benchmark[benchmark["date"] <= date]
    if len(hist) < 200:
        return True
    ma200 = hist["close"].tail(200).mean()
    return float(hist["close"].iloc[-1]) > ma200


def get_ma200_status(benchmark: pd.DataFrame, date: pd.Timestamp) -> dict:
    """获取 MA200 详细状态"""
    if benchmark.empty or len(benchmark[benchmark["date"] <= date]) < 200:
        return {"risk_on": True, "csi300_close": 0, "ma200": 0, "pct_above": 0}
    hist = benchmark[benchmark["date"] <= date]
    ma200 = float(hist["close"].tail(200).mean())
    close = float(hist["close"].iloc[-1])
    return {
        "risk_on": close > ma200,
        "csi300_close": close,
        "ma200": ma200,
        "pct_above": (close / ma200 - 1) * 100,
    }


def detect_spike_sectors(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
    lookback: int = 20,
    threshold: float = 0.075,
) -> Set[str]:
    """检测近 lookback 日内有单日涨幅超过 threshold 的赛道"""
    spiked = set()
    for sector, code in SECTOR_MAP.items():
        closes = _get_closes(etf_data, code, date, lookback + 10)
        if closes is None or len(closes) < lookback + 1:
            continue
        recent = closes[-(lookback + 1):]
        daily_returns = np.diff(recent) / recent[:-1]
        if len(daily_returns) > 0 and float(np.max(daily_returns)) > threshold:
            spiked.add(sector)
    return spiked


def compute_consec_down(
    etf_data: Dict[str, pd.DataFrame],
    sector: str,
    date: pd.Timestamp,
    max_lookback: int = 20,
) -> int:
    """计算某赛道截至 date 的连续下跌天数"""
    code = SECTOR_MAP.get(sector)
    if not code or code not in etf_data:
        return 0
    df = etf_data[code]
    hist = df[df["date"] <= date].tail(max_lookback + 1)
    if len(hist) < 2:
        return 0

    closes = hist["close"].values
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            count += 1
        else:
            break
    return count
