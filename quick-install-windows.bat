@echo off
rem =========================================================================
rem  nanaiamge - one-click install for Codex (Windows)
rem  Copies the skill into %USERPROFILE%\.codex\skills\nanaiamge and
rem  checks Python. No config is modified; uninstall = quick-uninstall-windows.bat
rem =========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "SKILL_NAME=nanaiamge"
set "DEST=%USERPROFILE%\.codex\skills\%SKILL_NAME%"

rem ---- Python (3.9+; the CLI works without the OpenAI SDK) ----
set "PY="
py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 ( set "PY=py -3" ) else (
    python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if not errorlevel 1 ( set "PY=python" )
)
if "!PY!"=="" (
    echo   [!!] Python 3.9+ not found. Install it from https://python.org and re-run.
    pause & exit /b 1
)
echo   [ok] Python found: !PY!

rem ---- sanity ----
if not exist SKILL.md goto broken
if not exist scripts\nanaiamge.py goto broken
if not exist scripts\psd_io.py goto broken
goto copyskill
:broken
echo   [!!] Run this from the unpacked skill folder (SKILL.md must be next to it).
pause & exit /b 1

:copyskill
rem ---- install: plain folder copy, idempotent ----
if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%" >nul 2>&1
copy /y SKILL.md AGENTS.md LICENSE "%DEST%\" >nul
xcopy /e /i /y /q scripts "%DEST%\scripts" >nul
if errorlevel 1 (
    echo   [!!] Copy failed.
    pause & exit /b 1
)
echo   [ok] installed to %DEST%

echo.
echo ============================================================
echo  Done. Restart Codex, then try:
echo    "用 nanaiamge 生成一张 1024x1024 的橙色猫海报"
echo    "把 poster.psd 的背景层导出来，换成夕阳背景再合成 PSD"
echo.
echo  Rollback: double-click quick-uninstall-windows.bat
echo  (deletes the skill folder only; Codex config is never touched)
echo ============================================================
pause
