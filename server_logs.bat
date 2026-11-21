@echo off
REM Holt die aktuellen Logs vom Server nach logs\server_dave.log (fuer Claude zum Nachsehen).
cd /d "%~dp0"
if not exist server\ip.txt (echo server\ip.txt fehlt - die Server-IP traegt Claude dort ein. & pause & exit /b 1)
set /p IP=<server\ip.txt
set "KEY=%~dp0server\keys\dave_server"
icacls "%KEY%" /inheritance:r /grant:r "%USERNAME%:R" >nul 2>nul
set "SSH=ssh -i "%KEY%" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 root@%IP%"
%SSH% "test -f /opt/dave/.cloud-init-fertig" || (echo Server noch nicht bereit oder nicht erreichbar. In 2 Minuten nochmal probieren. & pause & exit /b 1)

%SSH% "tail -n 300 /opt/dave/logs/dave.log; echo ---; systemctl status dave --no-pager | head -n 15" > logs\server_dave.log 2>&1
echo Logs geholt: logs\server_dave.log
timeout /t 3 >nul
