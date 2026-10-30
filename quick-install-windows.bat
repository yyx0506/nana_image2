@echo off
rem =========================================================================
rem  nana_image2 - one-click install for Codex (Windows)
rem  Copies the skill into %USERPROFILE%\.codex\skills\nana_image2 and resolves
rem  Python: 1) system python3 / py -3 (developers keep their own)
rem           2) the bundled private runtime (nothing to install)
rem  No config is modified; uninstall = quick-uninstall-windows.bat
rem =========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "SKILL_NAME=nana_image2"
set "DEST=%USERPROFILE%\.codex\skills\%SKILL_NAME%"

rem ---- resolve Python: system first, bundled runtime as fallback ----
set "PYCMD="
set "PYNODELAY="
py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 ( set "PYCMD=py -3" )
if "!PYCMD!"=="" (
    python3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if not errorlevel 1 ( set "PYCMD=python3" )
)
if "!PYCMD!"=="" (
    python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if not errorlevel 1 ( set "PYCMD=python" )
)
set "USERUNTIME="
if "!PYCMD!"=="" (
    if exist "%~dp0runtime\python.exe" (
        "%~dp0runtime\python.exe" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYCMD=%~dp0runtime\python.exe"
            set "USERUNTIME=1"
            echo   [ok] No system Python - using the bundled private runtime.
            echo        ^(The runtime lives only inside the skill folder; nothing is installed system-wide^)
        )
    )
)
if "!PYCMD!"=="" (
    echo   [!!] No Python 3.9+ found and this package carries no bundled runtime.
    echo        Re-download the full Windows package ^(nana_image2-windows-x64.zip^)
    echo        or install Python 3.9+ from https://python.org
    pause & exit /b 1
)
if not defined USERUNTIME (
    echo   [ok] using system Python: !PYCMD!
)

rem ---- sanity ----
if not exist SKILL.md goto broken
if not exist scripts\nana_image2.py goto broken
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
if exist "%DEST%\scripts\__pycache__" rmdir /s /q "%DEST%\scripts\__pycache__" >nul 2>&1
if errorlevel 1 (
    echo   [!!] Copy failed.
    pause & exit /b 1
)
if exist "%~dp0runtime\python.exe" (
    xcopy /e /i /y /q runtime "%DEST%\runtime" >nul
    if errorlevel 1 (
        echo   [!!] Copying the bundled runtime failed.
        pause & exit /b 1
    )
)
if exist "%~dp0runtime-x64\python.exe" (
    xcopy /e /i /y /q runtime-x64 "%DEST%\runtime-x64" >nul
)
echo   [ok] installed to %DEST%

rem ---- frozen third-party skills (vendor\) each land as their own skill ----
set "SKILLDIRS=nana_image2"
if exist "%~dp0vendor" (
    for /d %%D in ("%~dp0vendor\*") do (
        if exist "%%D\SKILL.md" (
            set "VNAME=%%~nxD"
            if exist "%USERPROFILE%\.codex\skills\!VNAME!" rmdir /s /q "%USERPROFILE%\.codex\skills\!VNAME!"
            xcopy /e /i /y /q "%%D" "%USERPROFILE%\.codex\skills\!VNAME!" >nul
            set "SKILLDIRS=!SKILLDIRS! !VNAME!"
            echo   [ok] vendored skill installed: !VNAME!
        )
    )
)
set "PKGVER="
for /f "tokens=2 delims==" %%V in ('findstr /b /c:"VERSION = " scripts\nana_image2.py') do set "PKGVER=%%V"
set "PKGVER=!PKGVER:^"=!"
> "%DEST%\.installed-manifest" (
    echo # nana_image2 distribution !PKGVER! - installed skill dirs:
    echo !SKILLDIRS!
)
if exist VENDOR-VERSIONS.txt echo   bundled third-party skills ^(frozen pins^): see VENDOR-VERSIONS.txt

echo.
echo ============================================================
echo  Done. Restart Codex, then try:
echo    "用 nana_image2 生成一张 1024x1024 的橙色猫海报"
echo    "把 poster.psd 的背景层导出来，换成夕阳背景再合成 PSD"
echo.
echo  Rollback: double-click quick-uninstall-windows.bat
echo  (deletes the skill folder only; Codex config is never touched)
echo ============================================================
pause
