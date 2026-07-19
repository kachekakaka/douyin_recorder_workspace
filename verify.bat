@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title douyin_recorder_workspace P1A - 完整自检

call scripts\windows\prepare-python.bat dev
if errorlevel 1 goto :failed

"%PY%" -m pip check
if errorlevel 1 goto :failed
"%PY%" tools\verify_repository_baseline.py
if errorlevel 1 goto :failed
"%PY%" tools\verify_source.py
if errorlevel 1 goto :failed
"%PY%" -m compileall -q app tests tools
if errorlevel 1 goto :failed
"%PY%" -m ruff check --no-cache app tests tools
if errorlevel 1 goto :failed
"%PY%" -m pytest -q -p no:cacheprovider --tb=short
if errorlevel 1 goto :failed

set "VERIFY_DIR=%TEMP%\douyin_recorder_verify_%RANDOM%_%RANDOM%"
mkdir "%VERIFY_DIR%" >nul 2>nul
"%PY%" tools\replay_recipient_fixture.py --quiet --json-output "%VERIFY_DIR%\replay.json" --markdown-output "%VERIFY_DIR%\replay.md"
if errorlevel 1 goto :failed
"%PY%" -c "import json,pathlib,sys; a=json.loads(pathlib.Path(r'%VERIFY_DIR%\replay.json').read_text(encoding='utf-8')); b=json.loads(pathlib.Path(r'docs\protocol\P0_SYNTHETIC_REPLAY_REPORT.json').read_text(encoding='utf-8')); sys.exit(0 if a==b else 1)"
if errorlevel 1 goto :failed

where node >nul 2>nul
if errorlevel 1 (
  echo [跳过] 未检测到 Node.js；浏览器运行不依赖 Node，GitHub CI 仍会检查前端语法。
) else (
  for /r "web" %%F in (*.js) do (
    node --check "%%F"
    if errorlevel 1 goto :failed
  )
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [警告] 未找到 FFmpeg；源码测试通过，但 readiness 会保持未就绪。
) else (
  ffmpeg -hide_banner -version > "%VERIFY_DIR%\ffmpeg.txt" 2>&1
  if errorlevel 1 goto :failed
  "%PY%" tools\ffmpeg_supervisor_smoke.py --duration 2 --output-dir "%VERIFY_DIR%\ffmpeg-smoke"
  if errorlevel 1 goto :failed
)
where ffprobe >nul 2>nul
if errorlevel 1 (
  echo [警告] 未找到 ffprobe；P0 源码测试通过，但 readiness 会保持未就绪。
) else (
  ffprobe -hide_banner -version > "%VERIFY_DIR%\ffprobe.txt" 2>&1
  if errorlevel 1 goto :failed
)

where git >nul 2>nul
if errorlevel 1 (
  echo [跳过] 未检测到 Git，无法执行 Bundle 恢复演练。
) else (
  "%PY%" tools\create_recovery_assets.py --output-dir "%VERIFY_DIR%\recovery" --label verify --allow-dirty
  if errorlevel 1 goto :failed
)

if exist "%VERIFY_DIR%" rmdir /s /q "%VERIFY_DIR%"
echo.
echo ===== P1A 完整自检通过 =====
exit /b 0

:failed
if defined VERIFY_DIR if exist "%VERIFY_DIR%" rmdir /s /q "%VERIFY_DIR%"
echo.
echo ===== P1A 自检失败，请查看上方信息 =====
pause
exit /b 1
