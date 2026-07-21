@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Douyin Recorder v0.1.0
set "PY=%CD%\runtime\python\python.exe"
set "DOUYIN_RECORDER_FFMPEG=%CD%\runtime\ffmpeg\bin\ffmpeg.exe"
set "DOUYIN_RECORDER_FFPROBE=%CD%\runtime\ffmpeg\bin\ffprobe.exe"
set "PATH=%CD%\runtime\ffmpeg\bin;%PATH%"
if not exist "%PY%" goto :missing
if not exist "%DOUYIN_RECORDER_FFMPEG%" goto :missing
if not exist "%DOUYIN_RECORDER_FFPROBE%" goto :missing

"%PY%" -m app.bootstrap
if errorlevel 1 goto :failed
for /f "tokens=1,* delims==" %%A in ('"%PY%" -m tools.start_info') do set "%%A=%%B"

echo 启动地址：%BIND_HOST%:%BIND_PORT%
echo 数据库：%DATABASE_PATH%
echo 录像目录：%RECORDS_PATH%
echo 按 Ctrl+C 停止服务。
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%OPEN_URL%'"
"%PY%" -m app
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%

:missing
echo [错误] 便携运行时不完整，请重新下载并核验 SHA-256。
pause
exit /b 1

:failed
echo [错误] 启动准备失败，请查看上方信息。
pause
exit /b 1
