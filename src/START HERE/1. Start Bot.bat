@echo off
REM --- Folder tag for the window title --------------------------------------
for %%I in ("%~dp0..\.") do set "NOST_FOLDER=%%~nxI"
title Nuke Option BOT - %NOST_FOLDER%
echo ============================================
echo   NUKE OPTION - BOT
echo   Folder: %NOST_FOLDER%
echo ============================================
echo Starting the bot...  (LEAVE THIS WINDOW OPEN)
echo The bot auto-restarts itself on any error - no separate babysitter needed.
echo.
REM Set the per-folder data dir BEFORE calling run.bat so the shared installer
REM config can't hijack this bot. run.bat does the folder-scoped work itself and
REM launches python with its full path; we do NOT kill anything folder-blind here.
set "NOST_DATA_DIR=%~dp0..\.nost-data"
call "%~dp0..\run.bat"
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
