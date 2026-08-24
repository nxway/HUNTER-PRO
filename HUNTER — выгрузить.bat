@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".pyembed\python.exe" (
    echo Окружение не найдено. Сначала запустите "HUNTER — установить.bat".
    pause
    exit /b 1
)
".pyembed\python.exe" hunter.py export
pause
