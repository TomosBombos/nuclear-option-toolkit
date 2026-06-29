@echo off
title Nuke Option - Bot
echo ============================================
echo   NUKE OPTION - BOT
echo ============================================
echo Stopping any old bot + keep-alive babysitter first (prevents double chat messages)...
REM kill the keep-alive babysitter so it can't respawn a second bot...
powershell -NoProfile -Command "$me=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -and ($_.CommandLine -match 'run_keepalive') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
REM ...then any existing bot python.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'no_mapvote_bot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
ping -n 2 127.0.0.1 >nul
echo Starting the bot...  (LEAVE THIS WINDOW OPEN)
echo The bot auto-restarts itself on any error - no separate babysitter needed.
echo.
call "%~dp0..\run.bat"
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
