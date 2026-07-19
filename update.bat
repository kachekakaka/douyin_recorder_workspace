@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title douyin_recorder_workspace - GitHub 快进更新

where git >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Git for Windows。
  pause
  exit /b 1
)

for /f "usebackq delims=" %%B in (`git branch --show-current`) do set "CURRENT_BRANCH=%%B"
if /I not "%CURRENT_BRANCH%"=="main" (
  echo [错误] update.bat 只允许在 main 分支运行；当前为 %CURRENT_BRANCH%。
  pause
  exit /b 1
)

git diff --quiet -- .
if errorlevel 1 goto :dirty
git diff --cached --quiet -- .
if errorlevel 1 goto :dirty

echo [1/3] 获取远端 main...
git fetch origin main
if errorlevel 1 goto :failed

git merge-base --is-ancestor HEAD origin/main
if errorlevel 1 (
  echo [错误] 本地 main 含远端没有的提交或历史已分叉，拒绝自动覆盖。
  goto :failed
)

echo [2/3] 仅执行 fast-forward 更新...
git merge --ff-only origin/main
if errorlevel 1 goto :failed

echo [3/3] 执行完整自检...
call verify.bat
exit /b %ERRORLEVEL%

:dirty
echo [错误] 当前目录存在未提交的受 Git 管理修改，为避免覆盖，本次更新已停止。
git status --short
pause
exit /b 1

:failed
echo [错误] GitHub 更新失败，请查看上方信息。
pause
exit /b 1
