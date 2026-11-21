@echo off
REM Laedt nur die .env (Keys/Einstellungen) auf den ZAP-Server. Das Gedaechtnis auf dem Server bleibt unberuehrt.
cd /d "%~dp0"
if not exist logs mkdir logs
echo Einstellungen auf den Server laden ...
.venv\Scripts\python.exe -I server\zap_upload.py --ohne-daten > logs\zap_einstellungen.log 2>&1
if errorlevel 1 (
  type logs\zap_einstellungen.log
  echo Upload fehlgeschlagen.
  pause
  exit /b 1
)
type logs\zap_einstellungen.log
echo ZAP_ENV_OK >> logs\zap_einstellungen.log
echo Fertig! Fenster schliesst gleich.
timeout /t 5 >nul
