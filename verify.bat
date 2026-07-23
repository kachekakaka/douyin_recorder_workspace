@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title douyin_recorder_workspace - 稳定性完整自检

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
"%PY%" tools\replay_recipient_fixture_to_db.py --output "%VERIFY_DIR%\recipient-database-replay.json"
if errorlevel 1 goto :failed
"%PY%" -c "import json,pathlib,sys; r=json.loads(pathlib.Path(r'%VERIFY_DIR%\recipient-database-replay.json').read_text(encoding='utf-8')); ok=r.get('schema_version')==6 and r.get('contract_live_verified') is False and r.get('summary')=={'target_messages':7,'unique_event_count':6,'duplicate_frame_count':1,'late_event_count':1,'interval_count':7}; s=json.dumps(r,sort_keys=True); ok=ok and all(x not in s for x in ('raw_payload_json','extra_json','unknown_fields_json','frame_base64')); sys.exit(0 if ok else 1)"
if errorlevel 1 goto :failed
"%PY%" tools\backup_restore_smoke.py --output-dir "%VERIFY_DIR%\backup-restore" --json-output "%VERIFY_DIR%\backup-restore.json"
if errorlevel 1 goto :failed
"%PY%" tools\diagnostics_report.py --root "%CD%" --output "%VERIFY_DIR%\diagnostics-report.json"
if errorlevel 1 goto :failed
"%PY%" -c "import json,pathlib,sys; s=json.loads(pathlib.Path(r'%VERIFY_DIR%\backup-restore.json').read_text(encoding='utf-8')); d=json.loads(pathlib.Path(r'%VERIFY_DIR%\diagnostics-report.json').read_text(encoding='utf-8')); ok=s.get('passed') is True and s.get('database',{}).get('restored_schema_version')==6 and d.get('protocol_contract',{}).get('live_verified') is False; sys.exit(0 if ok else 1)"
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
  "%PY%" tools\recording_session_smoke.py --duration 2 --output-dir "%VERIFY_DIR%\recording-session-smoke"
  if errorlevel 1 goto :failed
  "%PY%" tools\postprocess_smoke.py --duration 2 --output-dir "%VERIFY_DIR%\postprocess-smoke"
  if errorlevel 1 goto :failed
)
where ffprobe >nul 2>nul
if errorlevel 1 (
  echo [警告] 未找到 ffprobe；源码测试通过，但 readiness 会保持未就绪。
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
echo ===== 稳定性完整自检通过 =====
exit /b 0

:failed
if defined VERIFY_DIR if exist "%VERIFY_DIR%" rmdir /s /q "%VERIFY_DIR%"
echo.
echo ===== 稳定性自检失败，请查看上方信息 =====
pause
exit /b 1
