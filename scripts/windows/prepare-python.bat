@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
set "MODE=%~1"
if not defined MODE set "MODE=runtime"
if /I not "%MODE%"=="runtime" if /I not "%MODE%"=="dev" (
  echo [错误] prepare-python.bat 模式只允许 runtime 或 dev。
  endlocal & exit /b 1
)
set "PY=%CD%\.venv\Scripts\python.exe"
set "LOCK_HASH_FILE="

if exist "%PY%" goto :check_version

where py >nul 2>nul
if not errorlevel 1 (
  py -3.13 -c "import sys; assert sys.version_info[:2] == (3,13)" >nul 2>nul
  if not errorlevel 1 py -3.13 -m venv .venv
  if not exist "%PY%" (
    py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>nul
    if not errorlevel 1 py -3.12 -m venv .venv
  )
)

if exist "%PY%" goto :check_version
where python >nul 2>nul
if errorlevel 1 goto :missing
python -c "import sys; assert (3,12) <= sys.version_info[:2] < (3,14)" >nul 2>nul
if errorlevel 1 goto :wrong_version
python -m venv .venv
if not exist "%PY%" goto :missing

:check_version
"%PY%" -c "import sys; assert (3,12) <= sys.version_info[:2] < (3,14)" >nul 2>nul
if errorlevel 1 goto :venv_wrong_version

if /I "%MODE%"=="dev" (
  set "LOCK=requirements\dev.lock"
  set "HASH_FILES=requirements\runtime.lock requirements\dev.lock"
) else (
  set "LOCK=requirements\runtime.lock"
  set "HASH_FILES=requirements\runtime.lock"
)
set "LOCK_HASH_FILE=%TEMP%\douyin_recorder_lock_%RANDOM%_%RANDOM%.txt"
"%PY%" -c "import hashlib,pathlib; h=hashlib.sha256(); [h.update(pathlib.Path(p).read_bytes()) for p in r'%HASH_FILES%'.split()]; print(h.hexdigest())" > "%LOCK_HASH_FILE%"
if errorlevel 1 goto :failed
set /p LOCK_HASH=<"%LOCK_HASH_FILE%"
del /q "%LOCK_HASH_FILE%" >nul 2>nul
set "LOCK_HASH_FILE="
if not defined LOCK_HASH goto :failed
set "STAMP=.venv\.requirements-%MODE%.sha256"
set "INSTALLED_HASH="
if exist "%STAMP%" set /p INSTALLED_HASH=<"%STAMP%"
if /I "%INSTALLED_HASH%"=="%LOCK_HASH%" goto :ready

"%PY%" -m pip install --disable-pip-version-check -r "%LOCK%"
if errorlevel 1 goto :failed
>"%STAMP%" echo %LOCK_HASH%

:ready
if defined LOCK_HASH_FILE if exist "%LOCK_HASH_FILE%" del /q "%LOCK_HASH_FILE%" >nul 2>nul
endlocal & set "PY=%PY%" & exit /b 0

:missing
echo [错误] 未找到 Python 3.12 或 3.13。请先安装，再重新运行。
endlocal & exit /b 1

:wrong_version
echo [错误] 系统 Python 版本必须为 3.12 或 3.13。
endlocal & exit /b 1

:venv_wrong_version
echo [错误] 现有 .venv 不是 Python 3.12/3.13。请先备份后删除 .venv，再重试。
endlocal & exit /b 1

:failed
if defined LOCK_HASH_FILE if exist "%LOCK_HASH_FILE%" del /q "%LOCK_HASH_FILE%" >nul 2>nul
echo [错误] Python 依赖准备失败。
endlocal & exit /b 1
