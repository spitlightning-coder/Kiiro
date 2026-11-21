@echo off
REM Dave startet ab jetzt automatisch beim Windows-Login (unsichtbar) - und gleich jetzt.
cd /d "%~dp0"
REM laufenden Dave und alte Dienste beenden, damit nichts doppelt laeuft
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -like '*dave_dienst.bat*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*-u -m dave*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Dave Discord-Bot.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0dave_unsichtbar.vbs\"'; $s.WorkingDirectory='%~dp0'; $s.Description='Dave Discord-Bot'; $s.Save()"
if errorlevel 1 (
  echo Autostart konnte nicht eingerichtet werden.
  pause
  exit /b 1
)
echo Autostart eingerichtet. Dave startet jetzt im Hintergrund.
wscript.exe "%~dp0dave_unsichtbar.vbs"
timeout /t 4 >nul
