@echo off
title Nuke Option - Web Command Centre
echo ============================================
echo   NUKE OPTION - WEB COMMAND CENTRE
echo ============================================
echo Starting...  a browser tab will open at http://127.0.0.1:8770
echo (LEAVE THIS WINDOW OPEN)
echo.
call "%~dp0..\webcc.bat"
echo.
echo Stopped. Press any key to close this window.
pause >nul
