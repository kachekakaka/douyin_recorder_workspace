@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title Douyin Recorder - 脱敏诊断报告

set "PY=%CD%\runtime\python\python.exe"
if not exist "%PY%" goto :failed
set "REPORT_ROOT=%CD%\userdata\diagnostics"
if defined DOUYIN_RECORDER_USERDATA_DIR set "REPORT_ROOT=%DOUYIN_RECORDER_USERDATA_DIR%\diagnostics"
mkdir "%REPORT_ROOT%" >nul 2>nul
for /f "usebackq delims=" %%T in (`"%PY%" -c "from datetime import datetime; print(datetime.now().strftime('%%Y%%m%%d-%%H%%M%%S'))"`) do set "STAMP=%%T"
if not defined STAMP goto :failed
set "REPORT=%REPORT_ROOT%\diagnostics-%STAMP%.json"

"%PY%" tools\diagnostics_report.py --root "%CD%" --output "%REPORT%"
if errorlevel 1 goto :failed

echo [通过] 脱敏诊断报告已生成：
echo %REPORT%
type "%REPORT%"
exit /b 0

:failed
echo [失败] 脱敏诊断报告未生成。
exit /b 1
