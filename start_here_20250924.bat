@echo off
chcp 65001 >nul
title JanQ游戏自动化 - 快速启动

echo ================================================
echo        JanQ游戏自动化 - 快速启动
echo ================================================
echo.

:: 检查管理员权限
net session >nul 2>&1
if %errorLevel% == 0 (
    echo ✅ 管理员权限确认
) else (
    echo ⚠️  请求管理员权限...
    powershell -Command "Start-Process '%~dpnx0' -Verb RunAs"
    exit /b
)

:: 切换到脚本目录
cd /d "%~dp0"

:: 快速检查关键文件
if not exist "main_game_automation.py" (
    echo ❌ 缺少 main_game_automation.py
    pause
    exit /b 1
)

if not exist "actuator.exe" (
    echo ❌ 缺少 actuator.exe
    pause
    exit /b 1
)

echo 🚀 启动游戏自动化...
echo 📁 工作目录: %CD%
echo 📄 Log文件: C:\Program Files (x86)\SEGA\sega_net_MJ\MJ\BepInEx\LogOutput.log
echo.
echo 请确保:
echo 1. 游戏正在运行
echo 2. Log文件正在生成
echo 3. 游戏窗口可见
echo.
echo 按 Ctrl+C 停止程序
echo ================================================

:: 启动游戏自动化
python start_automation.py

echo.
echo 程序已结束
pause
