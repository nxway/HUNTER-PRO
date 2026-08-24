@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".pyembed\python.exe" (
    echo Окружение не найдено. Сначала запустите "HUNTER — установить.bat".
    pause
    exit /b 1
)
echo Открываю панель в браузере: http://127.0.0.1:5000/
echo Это окно можно свернуть, но не закрывать — пока оно открыто, панель работает.
echo Закроете окно — панель выключится.
".pyembed\python.exe" -m webui.app
pause
