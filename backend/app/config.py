# 作者：相空
"""
配置常量 — ETF Dashboard
"""
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # etf-dashboard/
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "etf_dashboard.db"

# A 股缓存策略
MARKET_CLOSE_HOUR = 16  # 16:00 后视为数据可用
CACHE_EXPIRE_HOUR = 16  # 缓存过期时间：当日 16:00

# akshare 节流
FETCH_DELAY = 0.3       # 每请求间隔秒

# ETF 复权类型
ETF_ADJUST = "qfq"

# CSI300 基准
CSI300_SYMBOL = "sh000300"

# API
API_PREFIX = "/api"

# 本地历史 CSV 数据目录 (邢不行格式)
# 设置为你的数据目录路径即可启用，None 表示跳过直接用 akshare
# 示例: LOCAL_DATA_DIR = "/path/to/stock-etf-trading-data-YYYY-MM-DD"
LOCAL_DATA_DIR = None
