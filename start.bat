@echo off
chcp 65001 >nul
title ETF Dashboard

:: ============================================
::  ETF 赛道轮动 Dashboard — 一键启动 (Windows)
::  双击此文件即可，自动检测并启动前后端
:: ============================================

set ROOT=%~dp0
set BACKEND=%ROOT%backend
set FRONTEND=%ROOT%frontend
set BROWSER=

echo.
echo  ╔══════════════════════════════════════╗
echo  ║   📊 ETF 赛道轮动 Dashboard 启动器   ║
echo  ╚══════════════════════════════════════╝
echo.

:: --- 检查后端 (8000) ---
echo  [1/2] 检查后端 (端口 8000)...
netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo         ✓ 后端已在运行
) else (
    echo         ⏳ 启动后端中...
    start "ETF-Backend" /min cmd /c "cd /d %BACKEND% && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
    timeout /t 4 /nobreak >nul
    netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul
    if %errorlevel% equ 0 (
        echo         ✓ 后端启动成功
    ) else (
        echo         ✗ 后端启动异常，请检查 Python 环境
    )
)

:: --- 检查前端 (5173) ---
echo  [2/2] 检查前端 (端口 5173)...
netstat -ano 2>nul | findstr ":5173 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo         ✓ 前端已在运行
) else (
    echo         ⏳ 启动前端中...
    start "ETF-Frontend" /min cmd /c "cd /d %FRONTEND% && npm run dev"
    timeout /t 5 /nobreak >nul
    netstat -ano 2>nul | findstr ":5173 " | findstr "LISTENING" >nul
    if %errorlevel% equ 0 (
        echo         ✓ 前端启动成功
    ) else (
        echo         ✗ 前端启动异常，请检查 Node.js 环境
    )
)

echo.
echo  ═══════════════════════════════════════
echo    ✅ 启动完成！
echo.
echo    前端页面:  http://localhost:5173
echo    后端 API:   http://localhost:8000
echo    API 文档:   http://localhost:8000/docs
echo  ═══════════════════════════════════════
echo.
echo  所有窗口已最小化到后台，关闭窗口请用任务管理器。
echo  按任意键打开前端页面...
pause >nul

:: 打开浏览器
start http://localhost:5173

exit
