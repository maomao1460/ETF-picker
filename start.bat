@echo off
chcp 65001 >nul
title ETF Dashboard

:: ============================================
::  ETF Dashboard - One-Click Launcher (Windows)
:: ============================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"

echo.
echo   ========================================
echo     ETF Dashboard Launcher
echo   ========================================
echo.

:: --- Check Backend (8000) ---
echo   [1/2] Backend (port 8000)...
netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo         Already running
) else (
    echo         Starting...
    start "ETF-Backend" /min cmd /c "cd /d "%BACKEND%" && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
    timeout /t 4 /nobreak >nul
    netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul
    if %errorlevel% equ 0 (
        echo         Started OK
    ) else (
        echo         Start failed - check Python
    )
)

:: --- Check Frontend (5173) ---
echo   [2/2] Frontend (port 5173)...
netstat -ano 2>nul | findstr ":5173 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo         Already running
) else (
    echo         Starting...
    start "ETF-Frontend" /min cmd /c "cd /d "%FRONTEND%" && npm run dev"
    timeout /t 5 /nobreak >nul
    netstat -ano 2>nul | findstr ":5173 " | findstr "LISTENING" >nul
    if %errorlevel% equ 0 (
        echo         Started OK
    ) else (
        echo         Start failed - check Node.js
    )
)

echo.
echo   ========================================
echo     Done!
echo     Frontend:  http://localhost:5173
echo     Backend:   http://localhost:8000
echo     API Docs:  http://localhost:8000/docs
echo   ========================================
echo.
echo   Press any key to open browser...
pause >nul
start http://localhost:5173
exit