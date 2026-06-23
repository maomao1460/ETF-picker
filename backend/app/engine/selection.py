# 作者：相空
"""
选股/风控公共逻辑 — 供 signal.py 和 backtest.py 复用
"""
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from .universe import SECTOR_MAP, OFFENSIVE_SECTORS


def filter_positive(scores: Dict[str, float]) -> Dict[str, float]:
    """过滤出评分 > 0 的板块"""
    return {s: sc for s, sc in scores.items() if sc > 0}


def select_top_n(
    scores: Dict[str, float],
    top_n: int,
    rank_offset: int = 0,
    exclude_sectors: Optional[Set[str]] = None,
) -> Set[str]:
    """从正面评分中按排名取 Top N（回测和实时信号共用）"""
    positive = {s: sc for s, sc in scores.items() if sc > 0 and (exclude_sectors is None or s not in exclude_sectors)}
    if not positive:
        return set()
    sorted_items = sorted(positive.items(), key=lambda x: -x[1])
    candidates = sorted_items[rank_offset:]
    return set(s for s, _ in candidates[:top_n])


def apply_offensive_limit(
    target_set: Set[str],
    holdings: Set[str],
    max_offensive: int,
    scores: Dict[str, float],
) -> Set[str]:
    """进攻型板块数量限制：超出上限时按评分保留最优"""
    if max_offensive is None or not target_set:
        return target_set
    current_offensive_held = sum(1 for s in holdings if s in OFFENSIVE_SECTORS and s in target_set)
    new_offensive = [s for s in target_set if s in OFFENSIVE_SECTORS and s not in holdings]
    allowed_new = max(0, max_offensive - current_offensive_held)
    if len(new_offensive) > allowed_new:
        ranked = sorted(new_offensive, key=lambda s: -scores.get(s, 0))
        return target_set - set(ranked[allowed_new:])
    return target_set


def apply_spike_filter(
    scores: Dict[str, float],
    spiked_sectors: Set[str],
) -> Dict[str, float]:
    """剔除近20日暴涨赛道"""
    if not spiked_sectors:
        return scores
    return {s: sc for s, sc in scores.items() if s not in spiked_sectors}


# ─── 板块轮动模式专用 ────────────────────────────────────────

def compute_rps(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
    period: int,
) -> Dict[str, float]:
    """计算所有板块 N 日收益率的排名百分位 (RPS)"""
    from .scoring import _get_closes

    returns = {}
    for sector, code in SECTOR_MAP.items():
        closes = _get_closes(etf_data, code, date, period + 5)
        if closes is None or len(closes) < period + 1:
            continue
        returns[sector] = float(closes[-1] / closes[-(period + 1)] - 1)

    if len(returns) < 2:
        return {s: 50.0 for s in returns}

    # 排名百分位: (total - rank + 1) / total * 100
    sorted_items = sorted(returns.items(), key=lambda x: -x[1])
    total = len(sorted_items)
    rps = {}
    for rank, (sector, _) in enumerate(sorted_items):
        rps[sector] = (total - rank) / total * 100
    return rps


def compute_volume_ratio(
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
    market_turnover: Optional[Dict[str, float]] = None,
    short_window: int = 5,
    long_window: int = 60,
) -> Dict[str, dict]:
    """
    计算每个板块的两步成交量比（课程原版公式）:
    1. 成交占比(t) = 板块成交额(t) / 全市场成交额(t)
    2. 信号比值 = 近N日均成交占比 / 近M日均成交占比
    
    如果 market_turnover 为 None，退化为板块自身量比。
    """
    result = {}
    for sector, code in SECTOR_MAP.items():
        if code not in etf_data:
            continue
        df = etf_data[code]
        hist = df[df["date"] <= date]
        if len(hist) < long_window + 5:
            continue

        # 成交额序列
        if "amount" in df.columns and df["amount"].notna().any():
            amounts = hist["amount"].values
        else:
            amounts = hist["volume"].values * hist["close"].values

        # 如果有全市场成交额，使用两步公式
        if market_turnover:
            dates = hist["date"].dt.strftime("%Y-%m-%d").tolist()
            shares = []
            for i, d in enumerate(dates):
                mkt = market_turnover.get(d, 0)
                if mkt > 0 and i < len(amounts):
                    shares.append(float(amounts[i]) / mkt)
                elif i < len(amounts):
                    shares.append(float(amounts[i]) / 1e12)  # fallback: 用万亿级近似

            if len(shares) < long_window + 5:
                continue

            short_avg = float(np.mean(shares[-short_window:]))
            long_avg_share = float(np.mean(shares[-long_window:]))
            ratio = short_avg / long_avg_share if long_avg_share > 0 else 1.0

            # 连续达标天数
            consec_above = 0
            if ratio >= 1.3:
                consec_above = 1
                for i in range(len(shares) - short_window - 1, -1, -1):
                    prev_ratio = float(np.mean(shares[i:i + short_window])) / long_avg_share if long_avg_share > 0 else 0
                    if prev_ratio >= 1.3:
                        consec_above += 1
                    else:
                        break

            result[sector] = {
                "ratio": round(ratio, 4),
                "short_avg": round(short_avg, 6),
                "long_avg": round(long_avg_share, 6),
                "consec_above": consec_above,
            }
        else:
            # 无全市场数据，退化为自身量比
            short_avg = float(np.mean(amounts[-short_window:]))
            long_avg = float(np.mean(amounts[-long_window:]))
            ratio = short_avg / long_avg if long_avg > 0 else 1.0

            consec_above = 0
            if ratio >= 1.3:
                consec_above = 1
                for i in range(len(amounts) - short_window - 1, -1, -1):
                    prev_ratio = float(np.mean(amounts[i:i + short_window])) / long_avg if long_avg > 0 else 0
                    if prev_ratio >= 1.3:
                        consec_above += 1
                    else:
                        break

            result[sector] = {
                "ratio": round(ratio, 4),
                "short_avg": round(short_avg, 2),
                "long_avg": round(long_avg, 2),
                "consec_above": consec_above,
            }
    return result


def select_sector_rotation_targets(
    scores: Dict[str, float],
    etf_data: Dict[str, pd.DataFrame],
    date: pd.Timestamp,
    top_n: int,
    rps_20: Optional[Dict[str, float]] = None,
    rps_60: Optional[Dict[str, float]] = None,
    vol_info: Optional[Dict[str, dict]] = None,
    surge_diffusion: Optional[Dict[str, dict]] = None,
    params: Optional[Dict] = None,
) -> Set[str]:
    """
    板块轮动模式选股 — 严格三信号确认：
    1. RPS(20) >= rps_threshold (默认85) 且 RPS(60) >= rps60_threshold (默认80)
    2. 量比连续 >= vol_consec_days 日 (默认3) >= vol_ratio_entry (默认1.3)
    3. 异常涨幅扩散: surge_count >= surge_min_stocks (默认3)
    
    三信号齐亮才入选。参数均可通过 params dict 覆盖。
    """
    if rps_20 is None or rps_60 is None or vol_info is None:
        positive = {s: sc for s, sc in scores.items() if sc > 0}
        if not positive:
            return set()
        sorted_items = sorted(positive.items(), key=lambda x: -x[1])
        return set(s for s, _ in sorted_items[:top_n])

    p = params or {}
    rps_threshold = p.get("rps_threshold", 85)
    rps60_threshold = p.get("rps60_threshold", 80)
    vol_ratio_entry = p.get("vol_ratio_entry", 1.3)
    vol_consec_days = p.get("vol_consec_days", 3)
    surge_min_stocks = p.get("surge_min_stocks", 3)

    candidates = {}
    for sector, sc in scores.items():
        if sector not in rps_20 or sector not in rps_60 or sector not in vol_info:
            continue
        # Signal 1: RPS
        if rps_20[sector] < rps_threshold:
            continue
        if rps_60[sector] < rps60_threshold:
            continue
        # Signal 2: Volume
        if vol_info[sector].get("consec_above", 0) < vol_consec_days:
            continue
        # Signal 3: Surge diffusion
        sd = (surge_diffusion or {}).get(sector, {})
        if not sd.get("has_diffusion", False):
            continue
        if sd.get("surge_count", 0) < surge_min_stocks:
            continue
        candidates[sector] = sc

    if not candidates:
        return set()

    sorted_items = sorted(candidates.items(), key=lambda x: -x[1])
    return set(s for s, _ in sorted_items[:top_n])


def should_exit_sector_rotation(
    sector: str,
    rps_20: Dict[str, float],
    vol_info: Dict[str, dict],
    params: Optional[Dict] = None,
) -> bool:
    """
    板块轮动离场判断:
    RPS(20) < rps_exit_threshold (默认50) 或 量比 < vol_ratio_exit (默认0.8)
    """
    p = params or {}
    rps_exit = p.get("rps_exit_threshold", 50)
    vol_exit = p.get("vol_ratio_exit", 0.8)
    
    rps = rps_20.get(sector, 0)
    vi = vol_info.get(sector, {})
    vol_ratio = vi.get("ratio", 1.0)

    if rps < rps_exit:
        return True
    if vol_ratio < vol_exit:
        return True
    return False


def compute_surge_threshold(stock_code: str) -> float:
    """根据股票代码前缀返回异常涨幅阈值"""
    if stock_code.startswith(("688", "300", "301")):
        return 0.199   # 科创板/创业板 20%
    elif stock_code.startswith(("43", "83", "87", "92")):
        return 0.299   # 北交所 30%
    elif stock_code.startswith(("60", "00", "001", "002", "003")):
        return 0.099   # A股主板 10%
    else:
        return 0.10    # 港股/美股等, 统一10%


def _get_closes_for_etf(
    etf_data: Dict[str, pd.DataFrame],
    code: str,
    date: pd.Timestamp,
    min_len: int,
):
    """获取截至 date 的收盘价序列 (复制 scoring._get_closes 逻辑)"""
    if code not in etf_data:
        return None
    df = etf_data[code]
    if df.empty:
        return None
    df_hist = df[df["date"] <= date]
    if len(df_hist) < min_len:
        return None
    return df_hist["close"].values
