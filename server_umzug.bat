@echo off
REM Zieht Dave vom PC auf den Server um: Code, Einstellungen (.env) UND Gedaechtnis (data\).
REM Danach laeuft Dave nur noch auf dem Server, der PC-Autostart wird abgeschaltet.
cd /d "%~dp0"
if not exist server\ip.txt (echo server\ip.txt fehlt - die Server-IP traegt Claude dort ein. & pause & exit /b 1)
set /p IP=<server\ip.txt
set "KEY=%~dp0server\keys\dave_server"
icacls "%KEY%" /inheritance:r /grant:r "%USERNAME%:R" >nul 2>nul
set "SSH=ssh -i "%KEY%" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 root@%IP%"
%SSH% "test -f /opt/dave/.cloud-init-fertig" || (echo Server noch nicht bereit oder nicht erreichbar. In 2 Minuten nochmal probieren. & pause & exit /b 1)

echo [1/4] Dave auf dem PC anhalten ...
call "%~dp0dave_stoppen.bat" >nul
call "%~dp0autostart_aus.bat" >nul
timeout /t 3 /nobreak >nul
echo [2/4] Code, Einstellungen und Gedaechtnis hochladen ...
tar -czf - --exclude=__pycache__ --exclude=.pytest_cache dave prompts tests requirements.txt requirements-dev.txt pytest.ini .env data | %SSH% "tar -xzf - -C /opt/dave && chown -R dave:dave /opt/dave"
if errorlevel 1 (echo Hochladen fehlgeschlagen. Dave auf dem PC wieder starten mit autostart_an.bat & pause & exit /b 1)
echo [3/4] Pakete installieren (dauert beim ersten Mal ein paar Minuten) ...
%SSH% "sudo -u dave /opt/dave/.venv/bin/pip install -q --disable-pip-version-check -r /opt/dave/requirements.txt"
if errorlevel 1 (echo Installation fehlgeschlagen. & pause & exit /b 1)
echo [4/4] Dave auf dem Server starten ...
%SSH% "systemctl enable --now dave && sleep 20 && systemctl is-active dave && tail -n 5 /opt/dave/logs/dave.log" > logs\server_umzug.log 2>&1
type logs\server_umzug.log
echo.
echo Fertig! Dave laeuft jetzt auf dem Server (%IP%). Auf dem PC ist er aus.
pause
