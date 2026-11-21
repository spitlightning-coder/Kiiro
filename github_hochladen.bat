@echo off
REM Laedt den aktuellen Stand von Dave auf GitHub hoch (github.com/spitlightning-coder/Kiiro).
REM Nie dabei: .env (Keys), data\ (Gedaechtnis), logs\, .venv\ - steht alles in .gitignore.
cd /d "%~dp0"
set "GIT=git"
where git >nul 2>nul || set "GIT=%ProgramFiles%\Git\cmd\git.exe"
"%GIT%" --version >nul 2>nul || (echo Git nicht gefunden. & pause & exit /b 1)
if not exist logs mkdir logs
if not exist .git (
  "%GIT%" init -q
  "%GIT%" remote add origin https://github.com/spitlightning-coder/Kiiro.git
)
"%GIT%" config user.name >nul 2>nul || "%GIT%" config user.name "spitlightning-coder"
"%GIT%" config user.email >nul 2>nul || "%GIT%" config user.email "spitlightning-coder@users.noreply.github.com"
echo Hole den Stand von GitHub ...
"%GIT%" fetch -q origin main || (echo Konnte GitHub nicht erreichen. & pause & exit /b 1)
"%GIT%" reset -q --soft origin/main
"%GIT%" add -A
"%GIT%" diff --cached --quiet && (echo Nichts Neues hochzuladen. & timeout /t 4 >nul & exit /b 0)
"%GIT%" diff --cached --name-only | findstr /i /c:".env" | findstr /v /i /c:".env.example" >nul && (echo ABBRUCH: .env wuerde hochgeladen! & pause & exit /b 1)
"%GIT%" commit -q -m "Dave: Stand vom %date% %time:~0,5%"
echo Lade hoch ...
"%GIT%" push -q origin HEAD:main > logs\github.log 2>&1
if errorlevel 1 (
  type logs\github.log
  echo Hochladen fehlgeschlagen.
  pause
  exit /b 1
)
echo Fertig! Alles ist auf GitHub.
"%GIT%" log -1 --stat --format="%%h %%s" >> logs\github.log
timeout /t 5 >nul
