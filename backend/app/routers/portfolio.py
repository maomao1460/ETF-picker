# 作者：相空
"""
组合 / 现金 / 快照 API
ADR-6: 快照自动生成 (每次访问 holdings 时检查)
"""
from datetime import datetime

from fastapi import APIRouter

from ..db import get_db
from ..models import CashUpdate, CapitalUpdate
from ..engine.data import get_latest_price

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _ensure_today_snapshot():
    """如果今日无快照, 自动生成 (ADR-6)"""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM portfolio_snapshots WHERE date=?", (today,)
        ).fetchone()
        if exists:
            return

        # 计算持仓市值
        rows = conn.execute("SELECT * FROM holdings WHERE status='active'").fetchall()
        positions_value = 0.0
        details = []
        for r in rows:
            code = r["code"]
            price = get_latest_price(code)
            if price:
                mv = price * r["shares"]
                positions_value += mv
                details.append({
                    "sector": r["sector"], "code": code,
                    "shares": r["shares"], "price": round(price, 4),
                    "market_value": round(mv, 2),
                })

        config = conn.execute("SELECT * FROM portfolio_config WHERE id=1").fetchone()
        cash = config["current_cash"] if config else 0
        initial = config["initial_capital"] if config else 100_000
        total_value = cash + positions_value
        cum_return = (total_value / initial - 1) if initial > 0 else 0

        # 日收益率
        prev = conn.execute(
            "SELECT total_value FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
        daily_return = 0.0
        if prev and prev["total_value"] > 0:
            daily_return = total_value / prev["total_value"] - 1

        import json
        conn.execute(
            "INSERT INTO portfolio_snapshots (date, total_value, cash, positions_value, "
            "n_holdings, daily_return, cumulative_return, holdings_detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                today, total_value, cash, positions_value,
                len(details), daily_return, cum_return,
                json.dumps(details, ensure_ascii=False),
            ),
        )


@router.get("/summary")
async def portfolio_summary():
    """总资产 / 现金 / 持仓市值 / 盈亏"""
    _ensure_today_snapshot()

    with get_db() as conn:
        config = conn.execute("SELECT * FROM portfolio_config WHERE id=1").fetchone()
        rows = conn.execute("SELECT * FROM holdings WHERE status='active'").fetchall()

    cash = config["current_cash"] if config else 0
    initial = config["initial_capital"] if config else 100_000

    positions = []
    positions_value = 0.0
    for r in rows:
        price = get_latest_price(r["code"])
        if price:
            mv = price * r["shares"]
            positions_value += mv
            pnl = (price - r["buy_price"]) * r["shares"]
            positions.append({
                "sector": r["sector"], "code": r["code"],
                "buy_price": r["buy_price"], "cur_price": round(price, 4),
                "shares": r["shares"],
                "market_value": round(mv, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((price / r["buy_price"] - 1) * 100, 2) if r["buy_price"] > 0 else 0,
            })

    total = cash + positions_value
    return {
        "initial_capital": initial,
        "total_value": round(total, 2),
        "current_cash": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_pnl": round(total - initial, 2),
        "total_pnl_pct": round((total / initial - 1) * 100, 2) if initial > 0 else 0,
        "total_return": round(total / initial - 1, 6) if initial > 0 else 0,
        "n_holdings": len(positions),
        "positions": positions,
    }


@router.put("/cash")
async def update_cash(body: CashUpdate):
    """手动修改现金"""
    with get_db() as conn:
        old = conn.execute("SELECT current_cash FROM portfolio_config WHERE id=1").fetchone()
        old_val = old["current_cash"] if old else 0
        conn.execute(
            "UPDATE portfolio_config SET current_cash=?, updated_at=datetime('now') WHERE id=1",
            (body.amount,),
        )
    return {"old_cash": old_val, "new_cash": body.amount, "reason": body.reason}


@router.put("/capital")
async def update_capital(body: CapitalUpdate):
    """修改初始本金"""
    with get_db() as conn:
        conn.execute(
            "UPDATE portfolio_config SET initial_capital=?, updated_at=datetime('now') WHERE id=1",
            (body.initial_capital,),
        )
    return {"initial_capital": body.initial_capital}


@router.get("/snapshots")
async def get_snapshots(days: int = 90):
    """净值曲线"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, total_value, cash, positions_value, n_holdings, "
            "daily_return, cumulative_return "
            "FROM portfolio_snapshots ORDER BY date DESC LIMIT ?",
            (days,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


@router.get("/performance")
async def get_performance():
    """收益率 / 最大回撤"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, total_value, cumulative_return FROM portfolio_snapshots ORDER BY date"
        ).fetchall()

    if not rows:
        return {"max_drawdown": 0, "cumulative_return": 0, "n_snapshots": 0}

    values = [r["total_value"] for r in rows]
    import numpy as np
    peak = np.maximum.accumulate(values)
    dd = 1 - np.array(values) / peak
    max_dd = float(dd.max()) if len(dd) > 0 else 0

    return {
        "cumulative_return": round(rows[-1]["cumulative_return"] * 100, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "n_snapshots": len(rows),
        "latest_date": rows[-1]["date"],
    }


@router.get("/daily-pnl")
async def daily_pnl(months: int = 6):
    """以日为单位的盈亏金额, 供 P&L 日历使用"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, total_value, daily_return "
            "FROM portfolio_snapshots ORDER BY date"
        ).fetchall()

    if len(rows) < 2:
        return []

    result = []
    for i in range(1, len(rows)):
        prev_val = rows[i - 1]["total_value"]
        cur_val = rows[i]["total_value"]
        pnl = cur_val - prev_val
        result.append({
            "date": rows[i]["date"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(rows[i]["daily_return"] * 100, 2) if rows[i]["daily_return"] else 0,
            "total_value": round(cur_val, 2),
        })

    # 只返回最近 N 月
    if months and result:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        result = [r for r in result if r["date"] >= cutoff]

    return result
