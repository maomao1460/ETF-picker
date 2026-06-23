# 📊 ETF 轮动策略 Dashboard

> 赛道轮动策略的研究与实盘辅助工具 — 独立于实盘脚本的全功能前后端系统。

## ✨ 功能概览

| 模块 | 说明 |
|------|------|
| 🏠 操作台 | 信号概览 + 持仓盈亏 + 资产净值曲线 |
| 🏆 赛道排名 | 动量评分排名 / 热力图视图 + CSV 导出 |
| 📈 ETF 详情 | 单只 ETF K线走势 + 技术指标 + 历史信号标注 |
| 💼 持仓管理 | 手动录入/调整持仓 + 资金管理 + P&L 日历 |
| 📡 历史信号 | 历史信号列表 + 参数/市场/交易建议详情 |
| 🧪 回测中心 | 自由参数回测 + **参数扫描对比** + 净值曲线 |

## 🛠 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLite / akshare
- **前端**: React 18 / TypeScript / Vite / Recharts
- **定时任务**: macOS launchd / cron_update.py

## 🚀 快速启动

### 1. 安装依赖

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd frontend
npm install
```

### 2. 一键启动

```bash
bash start.sh
```

启动后访问:
- **前端页面**: http://localhost:5173
- **后端 API**: http://localhost:8000
- **API 文档**: http://localhost:8000/docs

### 3. 分别启动

```bash
# 终端 1 — 后端
cd backend
python -m uvicorn app.main:app --reload --port 8000

# 终端 2 — 前端
cd frontend
npx vite --port 5173
```

## 📁 项目结构

```
etf-dashboard/
├── start.sh                    # 一键启动
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── config.py           # 配置常量
│   │   ├── db.py               # SQLite 建表 + 连接
│   │   ├── models.py           # Pydantic 模型
│   │   ├── engine/             # 核心引擎
│   │   │   ├── universe.py     # SECTOR_MAP 赛道定义
│   │   │   ├── data.py         # 行情拉取 + DB 缓存
│   │   │   ├── scoring.py      # 评分引擎
│   │   │   ├── risk.py         # 风控 (MA200 / spike / consec)
│   │   │   ├── indicators.py   # 技术指标
│   │   │   ├── signal.py       # 信号生成
│   │   │   └── backtest.py     # 回测引擎
│   │   └── routers/            # API 路由
│   │       ├── signal.py       # 信号 API
│   │       ├── holdings.py     # 持仓 API
│   │       ├── portfolio.py    # 组合 / 快照 API
│   │       ├── etf.py          # ETF 详情 API
│   │       ├── market.py       # 市场概览 API
│   │       └── backtest.py     # 回测 + 参数扫描 API
│   ├── cron_update.py          # 定时更新脚本
│   └── com.etf-dashboard.cron-update.plist  # launchd 配置
├── frontend/
│   └── src/
│       ├── App.tsx             # 路由 + 侧边栏
│       ├── api/                # API 封装
│       ├── pages/              # 页面组件
│       └── utils/export.ts     # CSV 导出
└── data/                       # SQLite 数据库 + 日志
```

## ⏰ 定时自动更新

每天收盘后自动刷新行情、生成快照和信号:

```bash
# 手动执行
cd backend
python cron_update.py              # 完整更新
python cron_update.py --force      # 强制重拉数据
python cron_update.py --signal-only    # 仅生成信号
python cron_update.py --snapshot-only  # 仅生成快照

# 安装 macOS 定时任务 (每天 17:30)
cp backend/com.etf-dashboard.cron-update.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.etf-dashboard.cron-update.plist

# 卸载定时任务
launchctl unload ~/Library/LaunchAgents/com.etf-dashboard.cron-update.plist
```

## 📝 注意事项

- 数据库文件位于 `data/etf_dashboard.db`，首次启动自动创建
- 行情数据源使用 akshare，自动缓存到 SQLite 避免重复拉取
- 前端与实盘脚本**完全隔离**，数据库仅存储研究用途的信号与持仓记录
- 参数扫描最多支持 10 个值，避免超时
