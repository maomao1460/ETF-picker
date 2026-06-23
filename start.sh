#!/bin/bash
# ─────────────────────────────────────────────
# ETF Dashboard 一键启动脚本
# ─────────────────────────────────────────────
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

# 颜色
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

cleanup() {
    echo ""
    echo -e "${CYAN}[ETF] 正在关闭服务...${NC}"
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
    echo -e "${GREEN}[ETF] 已关闭${NC}"
}
trap cleanup EXIT INT TERM

echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     ETF 轮动策略 Dashboard           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""

# 1. 启动后端
echo -e "${CYAN}[1/2] 启动后端 (FastAPI :8000)...${NC}"
cd "$BACKEND_DIR"
/Users/geo/anaconda3/envs/ml_stock_py312/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
sleep 2

# 2. 启动前端
echo -e "${CYAN}[2/2] 启动前端 (Vite :5173)...${NC}"
cd "$FRONTEND_DIR"
npx vite --port 5173 &
FRONTEND_PID=$!
sleep 2

echo ""
echo -e "${GREEN}✅ 启动完成！${NC}"
echo -e "   后端 API:  http://localhost:8000"
echo -e "   前端页面:  http://localhost:5173"
echo -e "   API 文档:  http://localhost:8000/docs"
echo ""
echo -e "${CYAN}按 Ctrl+C 关闭所有服务${NC}"

wait
