@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Полностью портативный Python: ничего не ставится в систему — ни в
rem реестр, ни в PATH, ни в Program Files. Всё живёт в папке .pyembed
rem рядом с проектом. Удалить = удалить эту папку, система не тронута.

set "PYVER=3.12.7"
set "PYSHORT=312"
set "PYDIR=%~dp0.pyembed"
set "PYEXE=%PYDIR%\python.exe"

if exist "%PYEXE%" goto :deps

echo Скачиваю портативный Python %PYVER% (только в эту папку, не в систему)...
mkdir "%PYDIR%" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ^
     Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%TEMP%\hunter-python-embed.zip'"
if errorlevel 1 (
    echo Не удалось скачать Python. Проверьте интернет-соединение и повторите.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -Path '%TEMP%\hunter-python-embed.zip' -DestinationPath '%PYDIR%' -Force"
del "%TEMP%\hunter-python-embed.zip" >nul 2>&1

rem В портативной сборке пакеты (site-packages) выключены по умолчанию —
rem включаем, иначе pip и наши зависимости не заработают.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "(Get-Content '%PYDIR%\python%PYSHORT%._pth') -replace '#import site', 'import site' | Set-Content '%PYDIR%\python%PYSHORT%._pth'"

echo Устанавливаю pip внутрь портативного Python...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ^
     Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\hunter-get-pip.py'"
"%PYEXE%" "%TEMP%\hunter-get-pip.py" --no-warn-script-location
del "%TEMP%\hunter-get-pip.py" >nul 2>&1

:deps
echo Ставлю зависимости проекта внутрь .pyembed...
"%PYEXE%" -m pip install --no-warn-script-location -r requirements.txt
if errorlevel 1 (
    echo Не удалось поставить зависимости. Смотрите текст ошибки выше.
    pause
    exit /b 1
)
"%PYEXE%" -m playwright install chromium

if not exist ".env" (
    copy .env.example .env >nul
    echo Создан .env из шаблона — впишите туда свои ключи.
)

echo.
echo Готово. Python и все библиотеки — только в папке .pyembed рядом с
echo проектом, ничего не установлено в систему. Впишите ключи в .env и
echo свою почту в USER_AGENT в config.py, затем запускайте .bat-ярлыки как обычно.
pause
