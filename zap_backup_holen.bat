@echo off
REM Holt die taeglichen Gedaechtnis-Sicherungen von Dave vom ZAP-Server in den Ordner backups.
cd /d "%~dp0"
if not exist logs mkdir logs
echo Sicherungen vom Server holen ...
.venv\Scripts\python.exe -I server\zap_backup_holen.py > logs\zap_backup.log 2>&1
if errorlevel 1 (
  type logs\zap_backup.log
  echo Holen fehlgeschlagen.
  pause
  exit /b 1
)
type logs\zap_backup.log
echo ZAP_BACKUP_OK >> logs\zap_backup.log
timeout /t 5 >nul
