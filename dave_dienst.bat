@echo off
REM Dave im Hintergrund: startet ihn neu, falls er abstuerzt. Beenden mit dave_stoppen.bat
cd /d "%~dp0"
if not exist logs mkdir logs
if exist logs\stop.flag del logs\stop.flag
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
:loop
echo [%date% %time%] Dave startet >> logs\dienst.log
.venv\Scripts\python.exe -u -m dave >> logs\console.log 2>&1
if exist logs\stop.flag goto ende
echo [%date% %time%] Dave beendet, Neustart in 30 s >> logs\dienst.log
timeout /t 30 /nobreak >nul
if exist logs\stop.flag goto ende
goto loop
:ende
del logs\stop.flag
echo [%date% %time%] Dave gestoppt >> logs\dienst.log
