@echo off
rem =========================================================================
rem  nana_image2 - one-click rollback (Windows)
rem  Deletes %USERPROFILE%\.codex\skills\nana_image2. Nothing else is
rem  touched: the skill only ever READS the Codex config, so there is
rem  nothing to undo.
rem =========================================================================
setlocal
set "DEST=%USERPROFILE%\.codex\skills\nana_image2"

if exist "%DEST%" (
    rmdir /s /q "%DEST%"
    echo   [ok] removed %DEST%
) else (
    echo   %DEST% was not installed ^(nothing to do^).
)

echo.
echo ============================================================
echo  Rollback complete. Restart Codex and the skill is gone.
echo  Your Codex config.toml was never modified by this skill.
echo ============================================================
pause
