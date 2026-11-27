@echo off
REM 注册 Windows 计划任务：每天 06:00 运行 run_checkin.bat
REM 需要以管理员身份运行本脚本。
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set TASK=ikuuu-checkin
set RUN_TIME=06:00

echo 正在注册计划任务 "%TASK%"（每天 %RUN_TIME%）...
schtasks /Create /TN "%TASK%" /TR "%~dp0run_checkin.bat" /SC DAILY /ST %RUN_TIME% /F
if errorlevel 1 (
    echo.
    echo 注册失败。请右键选择“以管理员身份运行”。
    exit /b 1
)

echo.
echo 已注册。常用命令：
echo   立即测试： schtasks /Run /TN "%TASK%"
echo   查看状态： schtasks /Query /TN "%TASK%"
echo   删除任务： schtasks /Delete /TN "%TASK%" /F
echo.
echo 注意：账号密码请通过系统环境变量提供（IKUUU_EMAIL / IKUUU_PASSWORD），
echo       不要写进本文件或提交到仓库。
exit /b 0
