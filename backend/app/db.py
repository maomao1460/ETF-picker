# 作者：相空
"""
SQLite 数据库初始化 — ETF Dashboard
"""
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, DATA_DIR

_SCHEMA = """
-- 1. 组合配置 (单行)
CREATE TABLE IF NOT EXISTS portfolio_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  initial_capital REAL DEFAULT 100000,
  current_cash REAL DEFAULT 100000,
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 2. 价格缓存
CREATE TABLE IF NOT EXISTS daily_prices (
  code TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL, amount REAL,
  fetched_at TEXT DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(date);

-- 3. 信号记录
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  params_json TEXT,
  rankings_json TEXT,
  top_picks_json TEXT,
  market_json TEXT,
  actions_json TEXT,
  holdings_snapshot_json TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);

-- 4. 持仓
CREATE TABLE IF NOT EXISTS holdings (
  id INTEGER PRIMARY KEY,
  sector TEXT NOT NULL,
  code TEXT NOT NULL,
  buy_date TEXT NOT NULL,
  buy_price REAL NOT NULL,
  shares INTEGER NOT NULL,
  cost_amount REAL NOT NULL,
  status TEXT DEFAULT 'active' CHECK(status IN ('active','closed','deleted')),
  close_date TEXT,
  close_price REAL,
  close_pnl REAL,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_holdings_status ON holdings(status);

-- 5. 持仓调整日志
CREATE TABLE IF NOT EXISTS holding_adjustments (
  id INTEGER PRIMARY KEY,
  holding_id INTEGER REFERENCES holdings(id),
  action TEXT NOT NULL,
  field_changed TEXT,
  old_value TEXT,
  new_value TEXT,
  reason TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 6. 组合快照 (自动生成)
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  total_value REAL,
  cash REAL,
  positions_value REAL,
  n_holdings INTEGER,
  daily_return REAL,
  cumulative_return REAL,
  holdings_detail_json TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(date)
);

-- 7. 回测结果
CREATE TABLE IF NOT EXISTS backtest_runs (
  id INTEGER PRIMARY KEY,
  name TEXT,
  params_json TEXT,
  metrics_json TEXT,
  equity_curve_json TEXT,
  trades_json TEXT,
  yearly_json TEXT,
  monthly_json TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 8. 参数扫描历史
CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY,
  name TEXT,
  scan_param TEXT NOT NULL,
  scan_values_json TEXT NOT NULL,
  base_params_json TEXT,
  results_json TEXT,
  scan_type TEXT DEFAULT '1d',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);


-- 9. ETF 成分股映射 (季度)
CREATE TABLE IF NOT EXISTS etf_constituents (
  code TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  weight REAL,
  quarter TEXT,
  fetched_at TEXT DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (code, stock_code, quarter)
);

-- 10. 个股日线缓存
CREATE TABLE IF NOT EXISTS stock_daily_prices (
  code TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
  fetched_at TEXT DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON stock_daily_prices(date);

-- 初始化 portfolio_config (如果空)
INSERT OR IGNORE INTO portfolio_config (id, initial_capital, current_cash) VALUES (1, 100000, 100000);
"""


def init_db():
    """创建数据库和表"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_SCHEMA)

    # 迁移: 为 daily_prices 增加 amount 列
    try:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN amount REAL")
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.commit()
    conn.close()


@contextmanager
def get_db():
    """获取数据库连接 (上下文管理器)"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
