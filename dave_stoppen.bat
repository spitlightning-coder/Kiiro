@echo off
REM Beendet Dave (auch den unsichtbaren aus dem Autostart) - nur Daves eigenes Python, sonst nichts.
cd /d "%~dp0"
if not exist logs mkdir logs
echo stop > logs\stop.flag
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*-u -m dave*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Dave wurde gestoppt.
timeout /t 3 >nul
