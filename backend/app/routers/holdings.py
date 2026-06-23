# 作者：相空
"""
持仓 API — CRUD + 平仓 (ADR-2: 不暴露 DELETE, 用 close)
"""
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..models import HoldingCreate, HoldingUpdate, HoldingClose
from ..engine.universe import SECTOR_MAP
from ..engine.data import get_latest_price

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


def _log_adjustment(conn, holding_id, action, field=None, old_val=None, new_val=None, reason=None):
    conn.execute(
        "INSERT INTO holding_adjustments (holding_id, action, field_changed, old_value, new_value, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (holding_id, action, field, str(old_val) if old_val is not None else None,
         str(new_val) if new_val is not None else None, reason),
    )


@router.get("")
async def list_holdings():
    """活跃持仓列表 (附加实时价格 + 盈亏)"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE status='active' ORDER BY buy_date DESC"
        ).fetchall()

    result = []
    for r in rows:
        h = dict(r)
        code = h.get("code", "")
        cur_price = get_latest_price(code)
        if cur_price:
            h["cur_price"] = round(cur_price, 4)
            if h.get("buy_price") and h["buy_price"] > 0:
                h["pnl_pct"] = round((cur_price / h["buy_price"] - 1) * 100, 2)
                h["pnl"] = round((cur_price - h["buy_price"]) * h.get("shares", 0), 2)
                h["market_value"] = round(cur_price * h.get("shares", 0), 2)
            # 持有天数
            try:
                buy_dt = datetime.strptime(h["buy_date"], "%Y-%m-%d")
                h["hold_days"] = (datetime.now() - buy_dt).days
            except Exception:
                h["hold_days"] = 0
        result.append(h)
    return result


@router.post("")
async def create_holding(body: HoldingCreate):
    """新增持仓 (自动扣减 cash)"""
    cost = body.buy_price * body.shares
    with get_db() as conn:
        # 检查 cash
        row = conn.execute("SELECT current_cash FROM portfolio_config WHERE id=1").fetchone()
        cash = row["current_cash"] if row else 0
        if cash < cost:
            raise HTTPException(400, f"现金不足: ¥{cash:,.2f} < ¥{cost:,.2f}")

        conn.execute(
            "INSERT INTO holdings (sector, code, buy_date, buy_price, shares, cost_amount, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body.sector, body.code, body.buy_date, body.buy_price, body.shares, cost, body.notes),
        )
        h_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "UPDATE portfolio_config SET current_cash = current_cash - ?, updated_at = datetime('now') WHERE id=1",
            (cost,),
        )
        _log_adjustment(conn, h_id, "create", reason=f"买入 {body.sector} {body.shares}股 @ {body.buy_price}")

    return {"id": h_id, "cost_deducted": round(cost, 2)}


@router.put("/{holding_id}")
async def update_holding(holding_id: int, body: HoldingUpdate):
    """修改持仓字段 (自动记录 adjustments)"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM holdings WHERE id=? AND status='active'", (holding_id,)).fetchone()
        if not row:
            raise HTTPException(404, "持仓不存在")

        updates = []
        params = []
        if body.buy_price is not None and body.buy_price != row["buy_price"]:
            _log_adjustment(conn, holding_id, "update", "buy_price", row["buy_price"], body.buy_price)
            updates.append("buy_price=?")
            params.append(body.buy_price)
        if body.shares is not None and body.shares != row["shares"]:
            _log_adjustment(conn, holding_id, "update", "shares", row["shares"], body.shares)
            updates.append("shares=?")
            params.append(body.shares)
            updates.append("cost_amount=?")
            params.append(body.shares * (body.buy_price or row["buy_price"]))
        if body.notes is not None:
            updates.append("notes=?")
            params.append(body.notes)

        if updates:
            updates.append("updated_at=datetime('now')")
            conn.execute(
                f"UPDATE holdings SET {', '.join(updates)} WHERE id=?",
                params + [holding_id],
            )

    return {"ok": True}


@router.post("/{holding_id}/close")
async def close_holding(holding_id: int, body: HoldingClose):
    """平仓 (ADR-2: 只用 close, 不用 DELETE)"""
    close_date = body.close_date or datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        row = conn.execute("SELECT * FROM holdings WHERE id=? AND status='active'", (holding_id,)).fetchone()
        if not row:
            raise HTTPException(404, "持仓不存在或已关闭")

        pnl = (body.close_price - row["buy_price"]) * row["shares"]

        conn.execute(
            "UPDATE holdings SET status='closed', close_date=?, close_price=?, close_pnl=?, "
            "updated_at=datetime('now') WHERE id=?",
            (close_date, body.close_price, pnl, holding_id),
        )

        # 回收现金
        proceeds = body.close_price * row["shares"]
        conn.execute(
            "UPDATE portfolio_config SET current_cash = current_cash + ?, updated_at=datetime('now') WHERE id=1",
            (proceeds,),
        )

        _log_adjustment(conn, holding_id, "close", reason=f"平仓 @ {body.close_price}, 盈亏: {pnl:+.2f}")

    return {"pnl": round(pnl, 2), "proceeds": round(proceeds, 2)}


@router.get("/closed")
async def list_closed(limit: int = 50):
    """已平仓记录"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE status='closed' ORDER BY close_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
