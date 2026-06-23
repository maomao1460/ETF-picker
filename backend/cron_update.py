# 作者：相空
#!/usr/bin/env python3
"""
ETF Dashboard — 定时数据更新脚本
功能:
  1. 刷新所有 ETF + 基准数据 (写入 daily_prices 缓存)
  2. 自动生成今日组合快照 (portfolio_snapshots)
  3. 以默认参数生成今日信号 (signals)

用法:
  python cron_update.py                 # 正常更新
  python cron_update.py --force         # 强制重拉所有数据
  python cron_update.py --signal-only   # 仅生成信号
  python cron_update.py --snapshot-only # 仅生成快照
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保 app 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import DATA_DIR
from app.db import init_db, get_db
from app.engine.universe import SECTOR_MAP
from app.engine.data import get_etf_data, get_benchmark_data, get_latest_price
from app.engine.signal import generate_signal

# ─── 日志 ──────────────────────────────────────────────────────
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "cron_update.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cron_update")

LOOKBACK_START = "2020-01-01"


# ─── 1. 刷新价格数据 ──────────────────────────────────────────
def refresh_prices(force: bool = False):
    """拉取所有 ETF + CSI300 最新数据并写入 DB 缓存"""
    end = datetime.now().strftime("%Y-%m-%d")
    codes = list(SECTOR_MAP.values())

    log.info(f"开始刷新 {len(codes)} 只 ETF 价格数据 (force={force})")
    start_t = time.time()

    data = get_etf_data(codes, LOOKBACK_START, end, force_refresh=force)
    log.info(f"ETF 数据: 成功 {len(data)}/{len(codes)} 只, 耗时 {time.time()-start_t:.1f}s")

    # 基准
    benchmark = get_benchmark_data(LOOKBACK_START, end, force_refresh=force)
    if benchmark is not None and not benchmark.empty:
        log.info(f"CSI300 基准: {len(benchmark)} 条记录")
    else:
        log.warning("CSI300 基准数据拉取失败")

    return data


# ─── 2. 生成组合快照 ──────────────────────────────────────────
def generate_snapshot():
    """生成今日组合快照 (portfolio_snapshots)"""
    today = datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM portfolio_snapshots WHERE date=?", (today,)
        ).fetchone()
        if exists:
            log.info(f"快照已存在: {today}, 跳过")
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

    log.info(
        f"快照生成: {today} | 总资产 {total_value:,.2f} | "
        f"持仓 {positions_value:,.2f} | 现金 {cash:,.2f} | "
        f"日收益 {daily_return*100:+.2f}%"
    )


# ─── 3. 生成信号 ──────────────────────────────────────────────
def generate_daily_signal():
    """以默认参数生成今日交易信号"""
    end = datetime.now().strftime("%Y-%m-%d")

    # 检查今日是否已有信号
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM signals WHERE date=?", (end,)
        ).fetchone()
        if exists:
            log.info(f"信号已存在: {end}, 跳过")
            return

    codes = list(SECTOR_MAP.values())
    etf_data = get_etf_data(codes, LOOKBACK_START, end)
    benchmark = get_benchmark_data(LOOKBACK_START, end)

    if not etf_data:
        log.error("无法获取行情数据, 信号生成失败")
        return

    # 读取持仓和现金
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE status='active'"
        ).fetchall()
        holdings = [dict(r) for r in rows]

        row = conn.execute("SELECT current_cash FROM portfolio_config WHERE id=1").fetchone()
        cash = row["current_cash"] if row else 100_000

    # 默认参数
    default_params = {
        "top_n": 5,
        "rebal_days": 5,
        "min_hold": 15,
        "score_mode": "rank_momentum",
        "max_offensive": 100,
        "spike_filter": True,
        "consec_down": 2,
        "force_refresh": False,
    }

    result = generate_signal(etf_data, benchmark, default_params, holdings, cash)

    # 保存
    with get_db() as conn:
        conn.execute(
            "INSERT INTO signals (date, params_json, rankings_json, top_picks_json, "
            "market_json, actions_json, holdings_snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                result.get("date"),
                json.dumps(result.get("params"), ensure_ascii=False),
                json.dumps(result.get("rankings"), ensure_ascii=False),
                json.dumps(
                    [r for r in result.get("rankings", []) if r.get("in_target")],
                    ensure_ascii=False,
                ),
                json.dumps(result.get("market"), ensure_ascii=False),
                json.dumps(result.get("actions"), ensure_ascii=False),
                json.dumps(result.get("holdings_snapshot"), ensure_ascii=False),
            ),
        )

    actions = result.get("actions", {})
    log.info(
        f"信号生成: {result.get('date')} | "
        f"建议买入 {len(actions.get('buy', []))} 只, "
        f"建议卖出 {len(actions.get('sell', []))} 只"
    )


# ─── 主流程 ──────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ETF Dashboard 定时更新脚本")
    parser.add_argument("--force", action="store_true", help="强制重拉所有数据")
    parser.add_argument("--signal-only", action="store_true", help="仅生成信号")
    parser.add_argument("--snapshot-only", action="store_true", help="仅生成快照")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info(f"ETF Dashboard 定时更新 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    try:
        # 确保 DB 存在
        init_db()

        if args.signal_only:
            generate_daily_signal()
        elif args.snapshot_only:
            generate_snapshot()
        else:
            # 完整流程
            refresh_prices(force=args.force)
            generate_snapshot()
            generate_daily_signal()

        log.info("✅ 更新完成")

    except Exception as e:
        log.exception(f"❌ 更新失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
