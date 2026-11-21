@echo off
REM Zieht Dave auf den ZAP-Hosting-Server um: Code via GitHub, .env + Gedaechtnis via SFTP.
REM Danach laeuft Dave nur noch auf dem Server; der PC-Autostart wird abgeschaltet.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [1/4] Dave auf dem PC anhalten ...
call "%~dp0dave_stoppen.bat" >nul
call "%~dp0autostart_aus.bat" >nul
timeout /t 3 /nobreak >nul
echo [2/4] Neuesten Code auf GitHub laden (der Server holt ihn sich von dort) ...
call "%~dp0github_hochladen.bat"
echo [3/4] Werkzeug fuer den Upload vorbereiten ...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check paramiko > logs\zap_umzug.log 2>&1
echo [4/4] Einstellungen und Gedaechtnis auf den Server laden ...
.venv\Scripts\python.exe -I server\zap_upload.py >> logs\zap_umzug.log 2>&1
if errorlevel 1 (
  type logs\zap_umzug.log
  echo Upload fehlgeschlagen. Dave auf dem PC wieder starten mit autostart_an.bat
  pause
  exit /b 1
)
type logs\zap_umzug.log
echo.
echo Fertig! Claude startet Dave jetzt auf dem Server.
echo ZAP_UMZUG_OK >> logs\zap_umzug.log
timeout /t 8 >nul
