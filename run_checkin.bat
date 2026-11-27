@echo off
REM ikuuu 每日签到 — 本机全自动入口
REM 由 Windows 计划任务调用；也可手动双击运行。
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist logs mkdir logs
set LOG=logs\checkin.log

echo.>> "%LOG%"
echo ============================================================>> "%LOG%"
echo [%DATE% %TIME%] 开始签到>> "%LOG%"

set PY=python
where py >nul 2>nul && set PY=py -3

%PY% checkin.py >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

echo [%DATE% %TIME%] 结束，退出码 %RC%>> "%LOG%"
exit /b %RC%
