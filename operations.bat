@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title douyin_recorder_workspace - 运维入口

set "COMMAND=%~1"
if /I "%COMMAND%"=="diagnostics" goto :diagnostics
if /I "%COMMAND%"=="backup" goto :backup
if /I "%COMMAND%"=="maintenance-plan" goto :maintenance_plan
if /I "%COMMAND%"=="maintenance-apply" goto :maintenance_apply
goto :usage

:diagnostics
call diagnostics.bat
exit /b %ERRORLEVEL%

:backup
call backup.bat
exit /b %ERRORLEVEL%

:maintenance_plan
call maintenance.bat plan
exit /b %ERRORLEVEL%

:maintenance_apply
call maintenance.bat apply "%~2"
exit /b %ERRORLEVEL%

:usage
echo 安全运维命令：
echo   operations.bat diagnostics
echo   operations.bat backup
echo   operations.bat maintenance-plan
echo   operations.bat maintenance-apply I_HAVE_STOPPED_THE_APP
echo.
echo maintenance-apply 仅在应用完全停止后使用；执行前会创建完整运行数据备份。
exit /b 2
