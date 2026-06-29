@echo off
REM One-click launcher: ONE bot window + ONE web command centre window.
REM Idempotent - it first stops ANY old bot, web CC, AND the run_keepalive babysitter,
REM so you can never end up with two bots (the bot self-heals on its own, no babysitter needed).
title Nuke Option - launcher
echo ============================================
echo   NUKE OPTION - starting everything
echo ============================================
echo Stopping any old copies first (bot, web CC, and the keep-alive babysitter)...
REM Pass 1: kill the keep-alive babysitter so it can't respawn a bot. Exclude THIS killer (own PID).
powershell -NoProfile -Command "$me=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -and ($_.CommandLine -match 'run_keepalive') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
ping -n 2 127.0.0.1 >nul
REM Pass 2: kill the actual bot + web python processes (incl. any orphaned by pass 1).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'no_mapvote_bot\.py|cc_web\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
ping -n 3 127.0.0.1 >nul
echo Opening the BOT window...
start "Nuke Option - Bot" "%~dp01. Start Bot.bat"
ping -n 4 127.0.0.1 >nul
echo Opening the WEB COMMAND CENTRE window (a browser tab will open too)...
start "Nuke Option - Web Command Centre" "%~dp02. Start Web Command Centre.bat"
echo.
echo Done - ONE bot window and ONE web CC window have opened. Leave them OPEN.
echo You can close THIS window now.
ping -n 7 127.0.0.1 >nul
