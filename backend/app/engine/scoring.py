# 作者：相空
"""
评分引擎 — rank_momentum / mixed / pure20
直接从实盘脚本移植，逻辑完全一致。
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .universe import SECTOR_MAP


def _get_closes(
    etf_data: Dict[str, pd.DataFrame],
    code: str,
    date: pd.Timestamp,
    min_len: int,
) -> Optional[np.ndarray]:
    """获取截至 date 的收盘价序列 (含上市日期保护)"""
    if code not in etf_data:
        return None
    df = etf_data[code]
    if df.empty:
        return None
    # 上市日期保护: 上市不满60天的ETF数据噪声大, 跳过
    listing_date = df["date"].min()
    if listing_date is not None and (date - listing_date).days < 60:
        return None
    df_hist = df[df["date"] <= date]
    if len(df_hist) < min_len:
        return None
    return df_hist["close"].values


def _momentum(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
    period: int,
) -> Dict[str, float]:
    """计算所有赛道的 N 日动量"""
    scores = {}
    for sector, code in SECTOR_MAP.items():
        closes = _get_closes(etf_data, code, date, period + 5)
        if closes is None or len(closes) < period + 1:
            continue
        scores[sector] = float(closes[-1] / closes[-(period + 1)] - 1)
    return scores


def _score_rank_momentum(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> Dict[str, float]:
    """排名动量: 50% 当前 20 日动量 + 50% 排名变化"""
    mom_now = _momentum(etf_data, date, 20)
    date_prev = date - pd.Timedelta(days=14)
    mom_prev = _momentum(etf_data, date_prev, 20)

    if len(mom_now) < 3 or len(mom_prev) < 3:
        return mom_now

    sorted_now = sorted(mom_now.items(), key=lambda x: -x[1])
    sorted_prev = sorted(mom_prev.items(), key=lambda x: -x[1])
    rank_now = {s: i for i, (s, _) in enumerate(sorted_now)}
    rank_prev = {s: i for i, (s, _) in enumerate(sorted_prev)}
    n = max(len(mom_now), 1)

    scores = {}
    for sector in mom_now:
        rn = rank_now.get(sector, n)
        rp = rank_prev.get(sector, n)
        rank_change = (rp - rn) / n
        scores[sector] = 0.5 * mom_now[sector] + 0.5 * rank_change * 0.1
    return scores


def _score_mixed(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> Dict[str, float]:
    """混合动量: 50% 20日 + 30% 10日 + 20% 5日"""
    scores = {}
    for sector, code in SECTOR_MAP.items():
        closes = _get_closes(etf_data, code, date, 25)
        if closes is None:
            continue
        s = 0.0
        if len(closes) >= 21:
            s += 0.5 * (closes[-1] / closes[-21] - 1)
        if len(closes) >= 11:
            s += 0.3 * (closes[-1] / closes[-11] - 1)
        if len(closes) >= 6:
            s += 0.2 * (closes[-1] / closes[-6] - 1)
        scores[sector] = float(s)
    return scores


def compute_sector_scores(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
    score_mode: str = "rank_momentum",
) -> Dict[str, float]:
    """统一评分入口"""
    if score_mode == "rank_momentum":
        return _score_rank_momentum(etf_data, date)
    elif score_mode == "mixed":
        return _score_mixed(etf_data, date)
    elif score_mode == "sector_rotation":
        return _momentum(etf_data, date, 20)  # RPS过滤在 selection.py 中处理
    else:  # pure20
        return _momentum(etf_data, date, 20)


def get_momentum_20d(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> Dict[str, float]:
    return _momentum(etf_data, date, 20)


def get_momentum_5d(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> Dict[str, float]:
    return _momentum(etf_data, date, 5)
