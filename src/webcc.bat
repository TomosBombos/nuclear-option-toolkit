@echo off
cd /d "%~dp0"
echo ============================================
echo   Nuke Option - Web Command Centre
echo   http://127.0.0.1:8770
echo ============================================
echo Stopping any old command-centre instances (avoids stale-map duplicates)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*cc_web.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo Opening your browser... (Ctrl+F5 to hard-refresh if it looks stale)
start "" http://127.0.0.1:8770
python -u cc_web.py
echo.
echo Server stopped.
pause
