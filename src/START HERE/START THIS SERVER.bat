@echo off
REM ===========================================================================
REM  Nuke Option - START THIS SERVER (per-folder, folder-safe)
REM  Replaces the old START EVERYTHING.bat. Kills ONLY this folder's python,
REM  isolates this server's config via a per-folder .nost-data, and opens ONE
REM  bot window + ONE web command centre window tagged with THIS folder name.
REM  Starting another server's copy of this file will NOT touch this one.
REM ===========================================================================

REM --- Folder tag for window titles (leaf folder name; %~dp0 ends with a \) ---
for %%I in ("%~dp0..\.") do set "NOST_FOLDER=%%~nxI"
title Nuke Option LAUNCHER - %NOST_FOLDER%

REM --- Server ROOT folder (this file lives in START HERE\, so go up one) ------
set "NOST_ROOT=%~dp0..\"

echo ============================================
echo   NUKE OPTION - starting THIS server
echo   Folder: %NOST_FOLDER%
echo ============================================

REM --- Per-server data dir: an EMPTY .nost-data so the SHARED installer config
REM     (~/.nuke-option-toolkit/config.json, written by a 2nd-server install)
REM     can NOT hijack this bot's rcmd port or this webcc's web.port. Empty dir
REM     => _TK_CFG={} => _cfg()/PORT fall through to env (run.bat) then defaults.
set "NOST_DATA_DIR=%NOST_ROOT%.nost-data"
if not exist "%NOST_DATA_DIR%" mkdir "%NOST_DATA_DIR%"

REM --- Kill the keep-alive babysitter for THIS folder only (if any) ----------
REM     Directory-PREFIX match ($d keeps its trailing backslash) so a sibling
REM     folder whose name is a superstring (e.g. 'Nuke Option Server 2') can
REM     NEVER match this server's path.
echo Stopping any old copies for THIS folder only...
powershell -NoProfile -Command "$d=(Resolve-Path '%NOST_ROOT%').Path; if (-not $d.EndsWith([char]92)) { $d=$d+[char]92 }; $me=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -and ($_.CommandLine -match 'run_keepalive') -and ($_.CommandLine -like ('*' + $d + '*')) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM --- Folder-scoped kill: only this folder's bot + webcc python -------------
REM     Same directory-PREFIX match (trailing backslash preserved).
powershell -NoProfile -Command "$d=(Resolve-Path '%NOST_ROOT%').Path; if (-not $d.EndsWith([char]92)) { $d=$d+[char]92 }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'no_mapvote_bot\.py|cc_web\.py' -and $_.CommandLine -like ('*' + $d + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM --- Transitional: kill any LEGACY relative-path webcc bound to THIS port.
REM     Old webcc.bat launched 'python -u cc_web.py' (no folder in CommandLine),
REM     so the folder filter above can't see it. Match by the unique listen
REM     port (8770 here) instead, re-confirming it's cc_web.py before killing.
REM     After the first launch via THIS script every webcc carries the full
REM     path, so this becomes a harmless no-op.
powershell -NoProfile -Command "$p=8770; Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -Expand OwningProcess -Unique | ForEach-Object { $c=(Get-CimInstance Win32_Process -Filter (\"ProcessId=$_\")).CommandLine; if ($c -match 'cc_web\.py') { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }" >nul 2>&1
ping -n 3 127.0.0.1 >nul

REM --- Open ONE bot window. run.bat already launches python with the FULL
REM     %~dp0 path, so it stays folder-killable. NOST_DATA_DIR is inherited. --
echo Opening the BOT window...
start "Nuke Option BOT - %NOST_FOLDER%" cmd /k "cd /d "%NOST_ROOT%" & set "NOST_DATA_DIR=%NOST_DATA_DIR%" & call run.bat"
ping -n 3 127.0.0.1 >nul

REM --- Open ONE webcc window. Launch cc_web.py by its FULL path (NOT the old
REM     relative form) so its CommandLine carries the folder for future kills.
REM     NOCC_PORT=8770 wins only because .nost-data is empty (no web.port). ---
echo Opening the WEB COMMAND CENTRE window (a browser tab will open too)...
start "Nuke Option WEBCC - %NOST_FOLDER%" cmd /k "cd /d "%NOST_ROOT%" & set "NOST_DATA_DIR=%NOST_DATA_DIR%" & set "NOCC_PORT=8770" & start "" http://127.0.0.1:8770 & python -u "%NOST_ROOT%cc_web.py""

echo.
echo Done - ONE bot window and ONE web CC window opened for %NOST_FOLDER%.
echo Leave them OPEN. You can close THIS window now.
ping -n 5 127.0.0.1 >nul
