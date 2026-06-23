# 作者：相空
"""
技术指标 + 趋势信号计算
"""
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .universe import SECTOR_MAP


def _sanitize(values: list) -> list:
    """将 NaN / Infinity 替换为 None, 保证 JSON 可序列化"""
    return [
        None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
        else v
        for v in values
    ]


def compute_indicators(df: pd.DataFrame) -> dict:
    """计算单只 ETF 的各项技术指标, 返回序列和最新值"""
    if df.empty or len(df) < 20:
        return {}

    closes = df["close"].values
    n = len(closes)

    # 均线
    ma5 = pd.Series(closes).rolling(5).mean().tolist()
    ma20 = pd.Series(closes).rolling(20).mean().tolist()
    ma200 = pd.Series(closes).rolling(200).mean().tolist() if n >= 200 else [None] * n

    # 动量
    mom_20d = []
    for i in range(n):
        if i >= 20:
            mom_20d.append(float(closes[i] / closes[i - 20] - 1))
        else:
            mom_20d.append(None)

    # 波动率 (20日)
    returns = pd.Series(closes).pct_change()
    vol_20d = returns.rolling(20).std().tolist()

    # 距最高/最低 (250日窗口)
    lookback = min(250, n)
    high_250 = float(np.max(closes[-lookback:]))
    low_250 = float(np.min(closes[-lookback:]))
    current = float(closes[-1])

    # 提取最新标量值 (取最后一个非 None 的值)
    sanitized_mom = _sanitize(mom_20d)
    sanitized_vol = _sanitize(vol_20d)
    mom_latest = next((v for v in reversed(sanitized_mom) if v is not None), None)
    vol_latest = next((v for v in reversed(sanitized_vol) if v is not None), None)

    return {
        "ma5": _sanitize(ma5),
        "ma20": _sanitize(ma20),
        "ma200": _sanitize(ma200),
        "momentum_20d": sanitized_mom,
        "volatility_20d": sanitized_vol,
        "momentum_20d_latest": mom_latest,
        "volatility_20d_latest": vol_latest,
        "distance_from_high": (current / high_250 - 1) if high_250 > 0 else 0,
        "distance_from_low": (current / low_250 - 1) if low_250 > 0 else 0,
        "current_price": current,
    }


def compute_trend_signals(
    etf_data: Dict[str, pd.DataFrame],
    latest_date: pd.Timestamp,
) -> Dict[str, dict]:
    """
    多因子趋势方向分析 — 9 维度综合判断每赛道趋势
    """
    results = {}
    for sector, code in SECTOR_MAP.items():
        if code not in etf_data:
            continue
        df = etf_data[code]
        if df.empty:
            continue

        hist = df[df["date"] <= latest_date].copy()
        if len(hist) < 120:
            continue

        p = hist["close"].values
        n = len(p)

        ma20 = pd.Series(p).rolling(20).mean().iloc[-1]
        ma60 = pd.Series(p).rolling(60).mean().iloc[-1]
        ma120 = pd.Series(p).rolling(120).mean().iloc[-1]
        current = p[-1]

        above_ma20 = current > ma20
        above_ma60 = current > ma60
        above_ma120 = current > ma120
        ma_bull = ma20 > ma60 > ma120
        ma_bear = ma20 < ma60 < ma120

        ret_1m = (p[-1] / p[-20] - 1) if n >= 20 else 0
        ret_3m = (p[-1] / p[-60] - 1) if n >= 60 else 0
        ret_6m = (p[-1] / p[-120] - 1) if n >= 120 else 0

        acceleration = 0
        if n >= 120:
            recent_mom = p[-1] / p[-60] - 1
            prior_mom = p[-60] / p[-120] - 1
            acceleration = recent_mom - prior_mom

        high_60d = np.max(p[-60:])
        low_60d = np.min(p[-60:])
        near_high = (current / high_60d) > 0.95
        near_low = (current / low_60d) < 1.05

        bull_count = sum([
            above_ma20, above_ma60, above_ma120,
            ma_bull,
            ret_1m > 0, ret_3m > 0, ret_6m > 0,
            acceleration > 0,
            near_high,
        ])
        bear_count = sum([
            not above_ma20, not above_ma60, not above_ma120,
            ma_bear,
            ret_1m < 0, ret_3m < 0, ret_6m < 0,
            acceleration < 0,
            near_low,
        ])

        if bull_count >= 7:
            trend, emoji = "强势上涨", "🟢"
        elif bull_count >= 5:
            trend, emoji = "温和上涨", "🟡"
        elif bear_count >= 7:
            trend, emoji = "明确下跌", "🔴"
        elif bear_count >= 5:
            trend, emoji = "偏弱下行", "🟠"
        else:
            trend, emoji = "方向不明", "⚪"

        if ma_bull:
            ma_status = "多头"
        elif ma_bear:
            ma_status = "空头"
        else:
            ma_status = "交叉"

        high_120d = np.max(p[-120:])
        dd_from_high = float(current / high_120d - 1)

        results[sector] = {
            "trend": trend, "emoji": emoji,
            "bull_count": int(bull_count), "bear_count": int(bear_count),
            "ret_1m": float(ret_1m), "ret_3m": float(ret_3m), "ret_6m": float(ret_6m),
            "ma_status": ma_status, "dd_from_high": dd_from_high,
            "acceleration": float(acceleration),
        }

    return results
