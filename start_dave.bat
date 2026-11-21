@echo off
REM Dave: einrichten (beim ersten Mal), Tests, starten. Ausgabe landet auch in logs\.
cd /d "%~dp0"
if not exist logs mkdir logs
if exist logs\crash.log del logs\crash.log
if exist logs\dave.log del logs\dave.log
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version > logs\setup.log 2>&1
if errorlevel 1 (
  echo Python nicht gefunden. Bitte Python 3.10+ von python.org installieren ^(Haken bei "Add to PATH"^). >> logs\setup.log
  type logs\setup.log
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo [setup] venv anlegen... >> logs\setup.log
  %PY% -m venv .venv >> logs\setup.log 2>&1
)
echo [setup] pip install... >> logs\setup.log
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements-dev.txt >> logs\setup.log 2>&1
echo [setup] pip exit %errorlevel% >> logs\setup.log

echo [test] pytest... >> logs\setup.log
.venv\Scripts\python.exe -m pytest -q > logs\tests.log 2>&1
echo [test] pytest exit %errorlevel% >> logs\setup.log
type logs\setup.log
type logs\tests.log

echo.
echo === Dave startet (Fenster offen lassen, Strg+C zum Beenden) ===
.venv\Scripts\python.exe -u -m dave 2>> logs\crash.log
type logs\crash.log
pause
