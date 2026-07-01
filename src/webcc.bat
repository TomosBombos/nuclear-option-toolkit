@echo off
cd /d "%~dp0"

REM --- Folder tag for the window title (leaf folder name) --------------------
for %%I in ("%~dp0.") do set "NOST_FOLDER=%%~nxI"
title Nuke Option WEBCC - %NOST_FOLDER%

REM --- Per-folder toolkit data dir. EMPTY .nost-data => _TK_CFG={} so the
REM     shared installer config's web.port (which BEATS env at cc_web.py L39)
REM     drops out and NOCC_PORT below actually takes effect. Respect a value a
REM     launcher already set; else default to this folder's .nost-data.
if not defined NOST_DATA_DIR set "NOST_DATA_DIR=%~dp0.nost-data"
if not exist "%NOST_DATA_DIR%" mkdir "%NOST_DATA_DIR%"

REM --- Pin THIS server's webcc port (only wins once .nost-data is empty) -----
set "NOCC_PORT=8770"

echo ============================================
echo   Nuke Option - Web Command Centre
echo   Folder: %NOST_FOLDER%
echo   http://127.0.0.1:8770
echo ============================================
echo Stopping any old command-centre instances for THIS folder only...
REM Folder-scoped kill: only cc_web python running from THIS folder's path.
REM Directory-PREFIX match ($d keeps its trailing backslash) so a sibling
REM folder whose name is a superstring (e.g. 'Nuke Option Server 2') can NEVER
REM match this server's path.
powershell -NoProfile -Command "$d='%~dp0'; if (-not $d.EndsWith([char]92)) { $d=$d+[char]92 }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*cc_web.py*' -and $_.CommandLine -like ('*' + $d + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
REM Transitional: also clear any LEGACY relative-path webcc bound to THIS port
REM (has no folder in its CommandLine); re-confirm cc_web.py before killing.
powershell -NoProfile -Command "$p=8770; Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -Expand OwningProcess -Unique | ForEach-Object { $c=(Get-CimInstance Win32_Process -Filter (\"ProcessId=$_\")).CommandLine; if ($c -match 'cc_web\.py') { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }" >nul 2>&1
ping -n 2 127.0.0.1 >nul
echo Opening your browser... (Ctrl+F5 to hard-refresh if it looks stale)
start "" http://127.0.0.1:8770
REM Launch cc_web by its FULL path so the folder shows in CommandLine and
REM future folder-scoped kills can target it.
python -u "%~dp0cc_web.py"
echo.
echo Server stopped.
pause
