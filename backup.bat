@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title douyin_recorder_workspace - 源码与运行数据备份

call scripts\windows\prepare-python.bat dev
if errorlevel 1 goto :failed

where git >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Git for Windows，无法创建完整 Git Bundle。
  goto :failed
)

for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"`) do set "STAMP=%%T"
set "BACKUP_ROOT=%CD%\backups\%STAMP%"
mkdir "%BACKUP_ROOT%" >nul 2>nul

"%PY%" tools\create_recovery_assets.py --output-dir "%BACKUP_ROOT%\source" --label "%STAMP%"
if errorlevel 1 goto :failed
"%PY%" tools\backup_runtime.py --output-dir "%BACKUP_ROOT%\runtime"
if errorlevel 1 goto :failed

powershell -NoProfile -Command "Get-ChildItem -File -Recurse '%BACKUP_ROOT%' | Get-FileHash -Algorithm SHA256 | ForEach-Object { '{0}  {1}' -f $_.Hash.ToLower(), $_.Path.Substring('%BACKUP_ROOT%'.Length + 1).Replace('\','/') } | Set-Content -Encoding ASCII '%BACKUP_ROOT%\SHA256SUMS.txt'"
if errorlevel 1 goto :failed

echo [通过] 备份已创建：%BACKUP_ROOT%
echo 注意：运行数据备份可能包含私人配置，不得上传公开仓库。
exit /b 0

:failed
echo [失败] 备份未完成，请查看上方信息。
pause
exit /b 1
