@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title Douyin Recorder - SQLite 安全维护

set "PY=%CD%\runtime\python\python.exe"
if not exist "%PY%" goto :failed
set "MODE=%~1"
if not defined MODE set "MODE=plan"
set "REPORT_ROOT=%CD%\userdata\maintenance"
if defined DOUYIN_RECORDER_USERDATA_DIR set "REPORT_ROOT=%DOUYIN_RECORDER_USERDATA_DIR%\maintenance"
mkdir "%REPORT_ROOT%" >nul 2>nul
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"`) do set "STAMP=%%T"
if not defined STAMP goto :failed

if /I "%MODE%"=="plan" goto :plan
if /I "%MODE%"=="apply" goto :apply
goto :usage

:plan
set "REPORT=%REPORT_ROOT%\maintenance-plan-%STAMP%.json"
"%PY%" tools\database_maintenance.py --root "%CD%" --output "%REPORT%"
if errorlevel 1 goto :failed
echo [通过] 只读维护计划已生成，数据库未被修改：
echo %REPORT%
type "%REPORT%"
exit /b 0

:apply
if /I not "%~2"=="I_HAVE_STOPPED_THE_APP" goto :confirm
set "BACKUP_ROOT=%CD%\backups\maintenance\%STAMP%"
set "REPORT=%REPORT_ROOT%\maintenance-apply-%STAMP%.json"
mkdir "%BACKUP_ROOT%" >nul 2>nul
"%PY%" tools\database_maintenance.py --root "%CD%" --apply --confirm-stopped --backup-dir "%BACKUP_ROOT%" --output "%REPORT%"
if errorlevel 1 goto :failed
echo [通过] 数据库维护已完成，完整备份位于：
echo %BACKUP_ROOT%
echo 维护报告：%REPORT%
type "%REPORT%"
exit /b 0

:confirm
echo [拒绝] apply 模式要求先停止应用，并输入明确确认词。
echo 用法：maintenance.bat apply I_HAVE_STOPPED_THE_APP
exit /b 2

:usage
echo 用法：
echo   maintenance.bat plan
echo   maintenance.bat apply I_HAVE_STOPPED_THE_APP
exit /b 2

:failed
echo [失败] 数据库维护未完成；不要删除已有备份或数据库。
exit /b 1
