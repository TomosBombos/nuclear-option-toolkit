@echo off
REM --- Folder tag for the window title --------------------------------------
for %%I in ("%~dp0..\.") do set "NOST_FOLDER=%%~nxI"
title Nuke Option WEBCC - %NOST_FOLDER%
echo ============================================
echo   NUKE OPTION - WEB COMMAND CENTRE
echo   Folder: %NOST_FOLDER%
echo ============================================
echo Starting...  a browser tab will open at http://127.0.0.1:8770
echo (LEAVE THIS WINDOW OPEN)
echo.
REM Set the per-folder data dir BEFORE calling webcc.bat so the shared config's
REM web.port can't win. webcc.bat does the folder-scoped kill + full-path launch.
set "NOST_DATA_DIR=%~dp0..\.nost-data"
call "%~dp0..\webcc.bat"
echo.
echo Stopped. Press any key to close this window.
pause >nul
