@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Git for Windows。
  pause
  exit /b 1
)

git diff --quiet -- .
if errorlevel 1 goto :dirty
git diff --cached --quiet -- .
if errorlevel 1 goto :dirty

git pull --ff-only origin main
if errorlevel 1 goto :failed
call verify.bat
exit /b %ERRORLEVEL%

:dirty
echo [错误] 当前目录存在未提交修改，为避免覆盖，本次更新已停止。
git status --short
pause
exit /b 1

:failed
echo [错误] GitHub 更新失败，请查看上方信息。
pause
exit /b 1
