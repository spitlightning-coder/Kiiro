@echo off
REM Spielt neuen Code + Einstellungen auf den Server (das Gedaechtnis dort bleibt unangetastet).
cd /d "%~dp0"
if not exist server\ip.txt (echo server\ip.txt fehlt - die Server-IP traegt Claude dort ein. & pause & exit /b 1)
set /p IP=<server\ip.txt
set "KEY=%~dp0server\keys\dave_server"
icacls "%KEY%" /inheritance:r /grant:r "%USERNAME%:R" >nul 2>nul
set "SSH=ssh -i "%KEY%" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 root@%IP%"
%SSH% "test -f /opt/dave/.cloud-init-fertig" || (echo Server noch nicht bereit oder nicht erreichbar. In 2 Minuten nochmal probieren. & pause & exit /b 1)

echo Lade neuen Code hoch ...
tar -czf - --exclude=__pycache__ --exclude=.pytest_cache dave prompts tests requirements.txt requirements-dev.txt pytest.ini .env | %SSH% "tar -xzf - -C /opt/dave && chown -R dave:dave /opt/dave && sudo -u dave /opt/dave/.venv/bin/pip install -q --disable-pip-version-check -r /opt/dave/requirements.txt && systemctl restart dave && sleep 15 && systemctl is-active dave" > logs\server_update.log 2>&1
type logs\server_update.log
echo Fertig.
timeout /t 5 >nul
