@echo off
rem =========================================================================
rem  nanaiamge launcher - resolves a Python interpreter and runs the CLI.
rem
rem  Resolution order:
rem    1. a system python3 / py -3 (3.9+)  -> developers keep their own Python
rem    2. the private runtime bundled inside this skill directory
rem
rem  Isolation: -E -s (ignore PYTHON* env vars and user site-packages), so a
rem  developer's conda/pyenv settings can never leak in, and this tool never
rem  installs anything anywhere.
rem =========================================================================
setlocal
set "HERE=%~dp0"
set "SCRIPT=%HERE%nanaiamge.py"
set "MODE="

python3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 set "MODE=sys3"

if not defined MODE (
    py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if not errorlevel 1 set "MODE=py3"
)

if not defined MODE (
    if exist "%HERE%..\runtime\python.exe" (
        "%HERE%..\runtime\python.exe" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
        if not errorlevel 1 set "MODE=runtime"
    )
)

if "%MODE%"=="" (
    echo   [!!] No Python 3.9+ found ^(neither system Python nor the bundled runtime^).
    echo        Re-install using the full package that ships a bundled runtime.
    exit /b 1
)

if "%MODE%"=="sys3"    python3 -E -s "%SCRIPT%" %*
if "%MODE%"=="py3"     py -3 -E -s "%SCRIPT%" %*
if "%MODE%"=="runtime" "%HERE%..\runtime\python.exe" -E -s "%SCRIPT%" %*
exit /b %errorlevel%
