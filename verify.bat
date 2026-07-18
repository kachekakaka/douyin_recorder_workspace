@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。P0 将补充便携运行时；当前请安装 Python 3.12 或 3.13。
  pause
  exit /b 1
)

python tools\verify_repository_baseline.py
if errorlevel 1 goto :failed
python -m compileall -q app tests tools
if errorlevel 1 goto :failed

where node >nul 2>nul
if errorlevel 1 goto :passed
for /r "web" %%F in (*.js) do (
  node --check "%%F"
  if errorlevel 1 goto :failed
)

:passed
echo [通过] 当前仓库基线自检完成。
exit /b 0

:failed
echo [失败] 请查看上方错误。
pause
exit /b 1
