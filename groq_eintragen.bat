@echo off
REM Nimmt den Groq-Key aus der Zwischenablage, traegt ihn in die .env ein und bringt alles auf den Server.
cd /d "%~dp0"
if not exist logs mkdir logs
set "GROQ_KEY="
for /f "usebackq delims=" %%k in (`powershell -NoProfile -Command "(Get-Clipboard -Raw).Trim()"`) do set "GROQ_KEY=%%k"
.venv\Scripts\python.exe -I server\groq_eintragen.py
if errorlevel 1 (
  set "GROQ_KEY="
  pause
  exit /b 1
)
set "GROQ_KEY="
powershell -NoProfile -Command "Set-Clipboard -Value $null" >nul 2>nul
echo Zwischenablage geleert.
echo.
call "%~dp0dave_update.bat"
