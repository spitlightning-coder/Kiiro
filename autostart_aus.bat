@echo off
REM Nimmt Dave wieder aus dem Autostart (stoppt ihn nicht - dafuer dave_stoppen.bat).
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Dave Discord-Bot.lnk')"
echo Autostart entfernt.
timeout /t 3 >nul
