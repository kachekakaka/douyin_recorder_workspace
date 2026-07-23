@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Douyin Recorder v0.1.0 - 便携包自检
set "PY=%CD%\runtime\python\python.exe"
set "DOUYIN_RECORDER_FFMPEG=%CD%\runtime\ffmpeg\bin\ffmpeg.exe"
set "DOUYIN_RECORDER_FFPROBE=%CD%\runtime\ffmpeg\bin\ffprobe.exe"
set "PATH=%CD%\runtime\ffmpeg\bin;%PATH%"
set "VERIFY_DIR=%TEMP%\douyin_recorder_portable_%RANDOM%_%RANDOM%"
set "DOUYIN_RECORDER_CONFIG_DIR=%VERIFY_DIR%\config"
set "DOUYIN_RECORDER_USERDATA_DIR=%VERIFY_DIR%\userdata"
set "DOUYIN_RECORDER_RECORDS_DIR=%VERIFY_DIR%\records"
set "DOUYIN_RECORDER_DATABASE_PATH=%VERIFY_DIR%\userdata\verify.db"
set "DOUYIN_RECORDER_HOST=127.0.0.1"
set "DOUYIN_RECORDER_PORT=33991"
mkdir "%VERIFY_DIR%" >nul 2>nul

if not exist "%PY%" goto :failed
if not exist "%DOUYIN_RECORDER_FFMPEG%" goto :failed
if not exist "%DOUYIN_RECORDER_FFPROBE%" goto :failed
for %%F in (operations.bat diagnostics.bat maintenance.bat backup.bat) do if not exist "%%F" goto :failed
for %%F in (tools\diagnostics_report.py tools\database_integrity_check.py tools\database_maintenance.py) do if not exist "%%F" goto :failed
"%PY%" tools\release_package.py verify --package-root "%CD%"
if errorlevel 1 goto :failed
"%PY%" -c "import app,fastapi,httpx,uvicorn,websockets,google.protobuf; assert app.__version__=='0.1.0'"
if errorlevel 1 goto :failed
"%PY%" -m app.bootstrap --json
if errorlevel 1 goto :failed
"%PY%" tools\ffmpeg_supervisor_smoke.py --duration 1 --output-dir "%VERIFY_DIR%\ffmpeg-smoke"
if errorlevel 1 goto :failed
"%PY%" tools\recording_session_smoke.py --duration 1 --output-dir "%VERIFY_DIR%\recording-smoke"
if errorlevel 1 goto :failed
"%PY%" tools\postprocess_smoke.py --duration 1 --output-dir "%VERIFY_DIR%\postprocess-smoke"
if errorlevel 1 goto :failed
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\health-smoke.ps1 -PackageRoot "%CD%" -WorkRoot "%VERIFY_DIR%\health"
if errorlevel 1 goto :failed

set "DOUYIN_RECORDER_CONFIG_DIR=%VERIFY_DIR%\health\config"
set "DOUYIN_RECORDER_USERDATA_DIR=%VERIFY_DIR%\health\userdata"
set "DOUYIN_RECORDER_RECORDS_DIR=%VERIFY_DIR%\health\records"
set "DOUYIN_RECORDER_DATABASE_PATH=%VERIFY_DIR%\health\userdata\health.db"
call operations.bat diagnostics
if errorlevel 1 goto :failed
call operations.bat maintenance-plan
if errorlevel 1 goto :failed

if exist "%VERIFY_DIR%" rmdir /s /q "%VERIFY_DIR%"
echo [通过] Windows 便携包完整自检通过。
exit /b 0

:failed
if exist "%VERIFY_DIR%" rmdir /s /q "%VERIFY_DIR%"
echo [失败] Windows 便携包自检失败。
exit /b 1
