# 作者：相空
"""
FastAPI 入口 — 行业赛道轮动 Dashboard 后端

启动: uvicorn app.main:app --reload --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import signal, holdings, portfolio, etf, market, backtest


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 DB"""
    init_db()
    yield


app = FastAPI(
    title="ETF Sector Rotation Dashboard",
    description="行业赛道轮动策略 — 信号/持仓/回测 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (开发阶段允许所有来源)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(signal.router)
app.include_router(holdings.router)
app.include_router(portfolio.router)
app.include_router(etf.router)
app.include_router(market.router)
app.include_router(backtest.router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "etf-dashboard-api"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
