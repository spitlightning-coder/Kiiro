@echo off
REM Dave im Hintergrund: startet ihn neu, falls er abstuerzt oder sein Code sich geaendert hat.
REM Beenden mit dave_stoppen.bat
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
:loop
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*-u -m dave*' }) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo [%date% %time%] Dave laeuft schon, dieser zweite Dienst beendet sich >> logs\dienst.log
  exit /b 0
)
echo [%date% %time%] Dave startet >> logs\dienst.log
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt >> logs\dienst.log 2>&1
.venv\Scripts\python.exe -u -m dave >> logs\console.log 2>&1
if errorlevel 3 if not errorlevel 4 (
  echo [%date% %time%] Neuer Code erkannt, Neustart in 3 s >> logs\dienst.log
  timeout /t 3 /nobreak >nul
  goto loop
)
echo [%date% %time%] Dave beendet, Neustart in 30 s >> logs\dienst.log
timeout /t 30 /nobreak >nul
goto loop
