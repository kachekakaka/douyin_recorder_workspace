@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Douyin Recorder v0.1.0 - 运行数据备份
set "PY=%CD%\runtime\python\python.exe"
set "DOUYIN_RECORDER_FFMPEG=%CD%\runtime\ffmpeg\bin\ffmpeg.exe"
set "DOUYIN_RECORDER_FFPROBE=%CD%\runtime\ffmpeg\bin\ffprobe.exe"
if not exist "%PY%" goto :failed
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"`) do set "STAMP=%%T"
set "BACKUP_ROOT=%CD%\backups\%STAMP%"
mkdir "%BACKUP_ROOT%" >nul 2>nul
"%PY%" tools\backup_runtime.py --output-dir "%BACKUP_ROOT%"
if errorlevel 1 goto :failed
echo [通过] 私人运行数据备份已创建：%BACKUP_ROOT%
echo 注意：该备份可能包含配置和数据库，不得公开上传。
exit /b 0

:failed
echo [失败] 运行数据备份未完成。
pause
exit /b 1
