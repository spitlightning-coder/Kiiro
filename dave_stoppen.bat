@echo off
REM Beendet Dave komplett (Hintergrund-Dienst und Dave selbst) - nur Daves eigene Prozesse, sonst nichts.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -like '*dave_dienst.bat*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*-u -m dave*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Dave wurde gestoppt.
timeout /t 3 >nul
