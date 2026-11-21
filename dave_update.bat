@echo off
REM Bringt alles auf den Server: Code nach GitHub, Einstellungen (.env) per SFTP. Danach Dave im ZAP-Panel neu starten.
cd /d "%~dp0"
echo === 1/2 Code auf GitHub ===
call "%~dp0github_hochladen.bat" || exit /b 1
echo === 2/2 Einstellungen auf den Server ===
call "%~dp0zap_einstellungen.bat" || exit /b 1
