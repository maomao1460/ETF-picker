# 作者：相空
"""
行情数据拉取 + 缓存 — akshare 多数据源回退
"""
import time
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import akshare as ak

from ..config import ETF_ADJUST, MARKET_CLOSE_HOUR, FETCH_DELAY, CSI300_SYMBOL, LOCAL_DATA_DIR
from ..db import get_db


# ─── 工具函数 ─────────────────────────────────────────────────
def _to_sina_symbol(code: str) -> str:
    if code.startswith(("15", "16")):
        return f"sz{code}"
    return f"sh{code}"


def _normalize_index_df(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    col_map = {"日期": "date", "开盘": "open", "收盘": "close",
               "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount"}
    df = df.rename(columns=col_map)
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df.sort_values("date").reset_index(drop=True)


def _is_cache_fresh(code: str) -> bool:
    """检查 daily_prices 中某 code 的缓存是否在有效期内"""
    now = datetime.now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(date) as max_date FROM daily_prices WHERE code=?",
            (code,)
        ).fetchone()
    if not row or not row["max_date"]:
        return False

    max_date = datetime.strptime(row["max_date"], "%Y-%m-%d")

    # 如果现在在 16:00 前，前一个交易日的数据就够了
    if now.hour < MARKET_CLOSE_HOUR:
        target = (now - timedelta(days=1)).date()
    else:
        target = now.date()

    # 跳过周末
    while target.weekday() >= 5:
        target -= timedelta(days=1)

    return max_date.date() >= target


# ─── 本地 CSV 数据源 (邢不行格式) ──────────────────────────
_LOCAL_COL_MAP = {
    "交易日期": "date",
    "开盘价":   "open",
    "最高价":   "high",
    "最低价":   "low",
    "收盘价":   "close",
    "成交量":   "volume",
}


def _fetch_etf_local(code: str, start: str, end: str) -> pd.DataFrame:
    """从本地 CSV 读取 ETF 历史日线 (邢不行格式), 自动换算前复权价格"""
    if not LOCAL_DATA_DIR:
        return pd.DataFrame()

    from pathlib import Path
    matches = list(Path(LOCAL_DATA_DIR).glob(f"{code}.*.csv"))
    if not matches:
        return pd.DataFrame()

    df = pd.read_csv(matches[0], encoding="gbk", header=1)
    df = df.rename(columns=_LOCAL_COL_MAP)

    # 只保留有 OHLCV 的行（排除货币基金等净值行）
    df = df[df["open"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"])

    # 前复权换算: qfq = raw × 后复权因子 / 最新后复权因子
    if "后复权因子" in df.columns:
        factor = pd.to_numeric(df["后复权因子"], errors="coerce").fillna(1.0)
        latest = float(factor.iloc[-1]) if factor.iloc[-1] > 0 else 1.0
        if latest != 1.0:
            for col in ("open", "high", "low", "close"):
                df[col] = pd.to_numeric(df[col], errors="coerce") * factor / latest

    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return (
        df[["date", "open", "high", "low", "close", "volume"]]
        .sort_values("date")
        .reset_index(drop=True)
    )


# ─── ETF 数据拉取 (多源回退) ─────────────────────────────────
def _fetch_etf_em(code: str, start: str, end: str) -> pd.DataFrame:
    df = ak.fund_etf_hist_em(
        symbol=code, period="daily",
        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
        adjust=ETF_ADJUST,
    )
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                             "最高": "high", "最低": "low", "成交量": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    # Eastmoney ETF 成交量口径是“手”，统一转成股数，和 sina / DB 保持一致。
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100
    return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def _fetch_etf_sina(code: str) -> pd.DataFrame:
    df = ak.fund_etf_hist_sina(symbol=_to_sina_symbol(code))
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def _repair_suspicious_etf_series(
    code: str,
    df: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    修复 ETF 最新一根 K 线的异常口径问题。

    典型现象:
    - 最新价单日跳变 30%+，但同日 sina 原始行情正常
    - Eastmoney / sina 在除权或份额事件附近口径不一致
    """
    if df.empty or len(df) < 2:
        return df

    latest_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    if prev_close <= 0:
        return df

    latest_change = latest_close / prev_close - 1
    if abs(latest_change) < 0.35:
        return df

    try:
        sina_df = _fetch_etf_sina(code)
    except Exception:
        return df

    sina_df = sina_df[(sina_df["date"] >= start) & (sina_df["date"] <= end)].reset_index(drop=True)
    if sina_df.empty or len(sina_df) < 2:
        return df
    if pd.Timestamp(sina_df["date"].iloc[-1]) != pd.Timestamp(df["date"].iloc[-1]):
        return df

    sina_latest = float(sina_df["close"].iloc[-1])
    sina_prev = float(sina_df["close"].iloc[-2])
    if sina_prev <= 0 or sina_latest <= 0:
        return df

    sina_change = sina_latest / sina_prev - 1
    price_ratio = latest_close / sina_latest

    if abs(sina_change) <= 0.20 and (price_ratio > 1.25 or price_ratio < 0.8):
        print(
            f"[WARN] {code} latest bar suspicious: fetched_close={latest_close:.4f}, "
            f"sina_close={sina_latest:.4f}, fetched_change={latest_change:.2%}, "
            f"sina_change={sina_change:.2%}; fallback to sina series"
        )
        return sina_df

    return df


def fetch_etf_hist(code: str, start: str, end: str) -> pd.DataFrame:
    """获取单只 ETF 历史日线, 多源回退 (本地 CSV → Eastmoney → Sina)"""
    for fetcher in [
        lambda: _fetch_etf_local(code, start, end),
        lambda: _fetch_etf_em(code, start, end),
        lambda: _fetch_etf_sina(code),
    ]:
        try:
            df = fetcher()
            if not df.empty:
                # 按日期范围过滤
                df = df[(df["date"] >= start) & (df["date"] <= end)]
                if not df.empty:
                    return _repair_suspicious_etf_series(code, df.reset_index(drop=True), start, end)
        except Exception:
            continue
    return pd.DataFrame()


# ─── 指数数据拉取 ────────────────────────────────────────────
def fetch_index_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """获取指数日线 (CSI300 等), 多源回退"""
    pure_code = symbol.replace("sh", "").replace("sz", "")
    fetchers = [
        lambda: _try_index_sina(symbol, start, end),
        lambda: _try_index_tx(symbol, start, end),
        lambda: _try_index_em(symbol, start, end),
        lambda: _try_index_csindex(pure_code, start, end),
    ]
    for f in fetchers:
        try:
            df = f()
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


def _try_index_sina(symbol, start, end):
    df = ak.stock_zh_index_daily(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    if df.empty:
        return None
    return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def _try_index_tx(symbol, start, end):
    df = ak.stock_zh_index_daily_tx(symbol=symbol)
    df = df.rename(columns={"amount": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    if df.empty:
        return None
    return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def _try_index_em(symbol, start, end):
    df = ak.stock_zh_index_daily_em(symbol=symbol)
    return _normalize_index_df(df, start, end)


def _try_index_csindex(pure_code, start, end):
    df = ak.stock_zh_index_hist_csindex(
        symbol=pure_code,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    return _normalize_index_df(df, start, end)


# ─── DB 缓存层 ─────────────────────────────────────────────
def _save_prices_to_db(code: str, df: pd.DataFrame):
    """把 DataFrame 写入 daily_prices 表"""
    if df.empty:
        return
    rows = []
    for _, r in df.iterrows():
        rows.append((
            code,
            r["date"].strftime("%Y-%m-%d"),
            float(r.get("open", 0)),
            float(r.get("high", 0)),
            float(r.get("low", 0)),
            float(r.get("close", 0)),
            float(r.get("volume", 0)),
            float(r.get("amount", 0)),
        ))
    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_prices (code, date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _load_prices_from_db(code: str, start: str, end: str) -> pd.DataFrame:
    """从 DB 缓存读取价格"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, amount FROM daily_prices "
            "WHERE code=? AND date>=? AND date<=? ORDER BY date",
            (code, start, end),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    return df


# ─── 高级接口 ────────────────────────────────────────────────
def get_etf_data(
    codes: List[str],
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    """
    批量获取 ETF 数据 (优先 DB 缓存, 缺失时拉取并写入)

    Returns:
        {code: DataFrame}
    """
    result = {}
    for code in codes:
        # 1. 检查缓存
        if not force_refresh and _is_cache_fresh(code):
            df = _load_prices_from_db(code, start_date, end_date)
            if len(df) >= 60:
                repaired = _repair_suspicious_etf_series(code, df, start_date, end_date)
                if not repaired.equals(df):
                    _save_prices_to_db(code, repaired)
                    df = repaired
                result[code] = df
                continue

        # 2. 拉取
        try:
            df = fetch_etf_hist(code, start_date, end_date)
            if not df.empty:
                _save_prices_to_db(code, df)
                result[code] = df
        except Exception as e:
            print(f"[WARN] {code} 拉取失败: {e}")

        if force_refresh:
            time.sleep(FETCH_DELAY)

    return result


def get_benchmark_data(
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """获取 CSI300 基准数据"""
    code = CSI300_SYMBOL

    if not force_refresh and _is_cache_fresh(code):
        df = _load_prices_from_db(code, start_date, end_date)
        if len(df) >= 200:
            return df

    df = fetch_index_daily(CSI300_SYMBOL, start_date, end_date)
    if not df.empty:
        _save_prices_to_db(code, df)
    return df


def get_latest_price(code: str) -> Optional[float]:
    """获取 DB 中某 code 的最新收盘价"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT close FROM daily_prices WHERE code=? ORDER BY date DESC LIMIT 1",
            (code,)
        ).fetchone()
    return float(row["close"]) if row else None


# ─── ETF 成分股 ─────────────────────────────────────────────

def _is_constituent_cache_fresh(code: str) -> bool:
    """检查 ETF 成分股缓存是否在有效期内 (季度更新)"""
    from datetime import datetime
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) as max_fetch FROM etf_constituents WHERE code=?",
            (code,)
        ).fetchone()
    if not row or not row["max_fetch"]:
        return False
    # 季度数据，60天内有效
    fetch_dt = datetime.strptime(row["max_fetch"], "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - fetch_dt).days < 60


def get_etf_constituents(code: str) -> List[Dict]:
    """
    获取 ETF 成分股列表 (优先 DB 缓存, 缺失时从 akshare 拉取)

    Returns:
        [{"stock_code": "688981", "stock_name": "中芯国际", "weight": 8.02}, ...]
    """
    # 1. 检查缓存
    if _is_constituent_cache_fresh(code):
        with get_db() as conn:
            rows = conn.execute(
                "SELECT stock_code, stock_name, weight FROM etf_constituents WHERE code=? ORDER BY weight DESC",
                (code,)
            ).fetchall()
        if rows:
            return [{"stock_code": r["stock_code"], "stock_name": r["stock_name"], "weight": r["weight"]} for r in rows]

    # 2. 从 akshare 拉取
    import akshare as ak
    try:
        df = ak.fund_portfolio_hold_em(symbol=code, date="2025")
        if df.empty:
            return []

        constituents = []
        with get_db() as conn:
            # 清除旧数据
            conn.execute("DELETE FROM etf_constituents WHERE code=?", (code,))
            for _, row in df.iterrows():
                stock_code = str(row["股票代码"]).zfill(6)
                stock_name = str(row["股票名称"])
                weight = float(row["占净值比例"]) if pd.notna(row["占净值比例"]) else 0.0
                constituents.append({"stock_code": stock_code, "stock_name": stock_name, "weight": weight})
                conn.execute(
                    "INSERT OR REPLACE INTO etf_constituents (code, stock_code, stock_name, weight, quarter) VALUES (?, ?, ?, ?, ?)",
                    (code, stock_code, stock_name, weight, str(row.get("季度", "")))
                )

        time.sleep(FETCH_DELAY)
        return constituents
    except Exception as e:
        print(f"[WARN] {code} 成分股拉取失败: {e}")
        return []


# ─── 个股日线 ──────────────────────────────────────────────

def _is_stock_cache_fresh(code: str) -> bool:
    """检查个股日线缓存是否新鲜"""
    from datetime import datetime
    now = datetime.now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(date) as max_date FROM stock_daily_prices WHERE code=?",
            (code,)
        ).fetchone()
    if not row or not row["max_date"]:
        return False
    max_date = datetime.strptime(row["max_date"], "%Y-%m-%d")
    if now.hour < MARKET_CLOSE_HOUR:
        target = (now - timedelta(days=1)).date()
    else:
        target = now.date()
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return max_date.date() >= target


def get_stocks_daily(
    codes: List[str],
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    """
    批量获取个股日线 (优先 DB 缓存, 缺失时从 akshare 拉取)

    Returns:
        {stock_code: DataFrame}
    """
    import akshare as ak
    # Disable proxy for stock data (avoid Connection aborted errors)
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    result = {}
    for code in codes:
        # 1. 检查缓存
        if not force_refresh and _is_stock_cache_fresh(code):
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT date, open, high, low, close, volume, amount FROM stock_daily_prices "
                    "WHERE code=? AND date>=? AND date<=? ORDER BY date",
                    (code, start_date, end_date),
                ).fetchall()
            if rows:
                df = pd.DataFrame([dict(r) for r in rows])
                df["date"] = pd.to_datetime(df["date"])
                result[code] = df
                continue

        # 2. 从 akshare 拉取
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""), adjust="qfq")
            if df is not None and not df.empty:
                col_map = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount"}
                df = df.rename(columns=col_map)
                df["date"] = pd.to_datetime(df["date"])

                # 写入 DB
                rows = []
                for _, r in df.iterrows():
                    rows.append((code, r["date"].strftime("%Y-%m-%d"), float(r.get("open", 0)), float(r.get("high", 0)), float(r.get("low", 0)), float(r.get("close", 0)), float(r.get("volume", 0)), float(r.get("amount", 0))))
                with get_db() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO stock_daily_prices (code, date, open, high, low, close, volume, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                result[code] = df[["date", "open", "high", "low", "close", "volume", "amount"]]
        except Exception as e:
            print(f"[WARN] stock {code} 拉取失败: {e}")

        time.sleep(FETCH_DELAY)
    return result


# ─── 全市场成交额 ──────────────────────────────────────────

def get_total_market_turnover(
    start_date: str,
    end_date: str,
) -> Dict[str, float]:
    """
    获取沪深全市场日成交额。
    以上证指数(000001) + 深证成指(399001) 成交额合计，缓存到 daily_prices(code="market_total")

    Returns:
        {"2025-06-01": 1234567890123.0, ...}
    """
    import akshare as ak

    market_code = "market_total"

    # 1. 检查缓存
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, amount FROM daily_prices WHERE code=? AND date>=? AND date<=? ORDER BY date",
            (market_code, start_date, end_date),
        ).fetchall()
    if rows and len(rows) >= 200:
        return {r["date"]: r["amount"] for r in rows if r["amount"] and r["amount"] > 0}

    # 2. 拉取上证成交额
    sh_amounts = {}
    try:
        df_sh = ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""))
        if df_sh is not None and not df_sh.empty:
            for _, row in df_sh.iterrows():
                d = pd.Timestamp(row["日期"]).strftime("%Y-%m-%d")
                amt = float(row.get("成交额", 0))
                if amt > 0:
                    sh_amounts[d] = amt
    except Exception as e:
        print(f"[WARN] 上证成交额拉取失败: {e}")

    # 3. 拉取深证成交额
    sz_amounts = {}
    try:
        df_sz = ak.stock_zh_index_daily_em(symbol="sz399001", start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""))
        if df_sz is not None and not df_sz.empty:
            for _, row in df_sz.iterrows():
                d = pd.Timestamp(row["日期"]).strftime("%Y-%m-%d")
                amt = float(row.get("成交额", 0))
                if amt > 0:
                    sz_amounts[d] = amt
    except Exception as e:
        print(f"[WARN] 深证成交额拉取失败: {e}")

    # 4. 合并 + 缓存
    result = {}
    all_dates = set(sh_amounts.keys()) | set(sz_amounts.keys())
    rows_to_insert = []
    for d in all_dates:
        total = sh_amounts.get(d, 0) + sz_amounts.get(d, 0)
        if total > 0:
            result[d] = total
            rows_to_insert.append((market_code, d, 0, 0, 0, 0, 0, total))

    if rows_to_insert:
        with get_db() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_prices (code, date, open, high, low, close, volume, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )

    return result
