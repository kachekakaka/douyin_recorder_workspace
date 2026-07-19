@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title douyin_recorder_workspace P1A

call scripts\windows\prepare-python.bat runtime
if errorlevel 1 goto :failed

"%PY%" -m app.bootstrap
if errorlevel 1 goto :failed
for /f "tokens=1,* delims==" %%A in ('"%PY%" -m tools.start_info') do set "%%A=%%B"

where ffmpeg >nul 2>nul
if errorlevel 1 echo [警告] 未在 PATH 找到 FFmpeg；网页 readiness 会显示未就绪。
where ffprobe >nul 2>nul
if errorlevel 1 echo [警告] 未在 PATH 找到 ffprobe；网页 readiness 会显示未就绪。

echo 启动地址：%BIND_HOST%:%BIND_PORT%
echo 数据库：%DATABASE_PATH%
echo 录像目录：%RECORDS_PATH%
echo 按 Ctrl+C 停止服务。
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%OPEN_URL%'"
"%PY%" -m app
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%

:failed
echo [错误] 启动准备失败，请查看上方信息。
pause
exit /b 1
