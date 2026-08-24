@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Создаю виртуальное окружение в .venv (ничего не ставится в систему)...
python -m venv .venv
if %errorlevel% neq 0 (
    echo Не удалось создать окружение. Проверьте, что Python установлен и добавлен в PATH.
    pause
    exit /b 1
)

echo Ставлю зависимости внутрь .venv...
".venv\Scripts\pip.exe" install -r requirements.txt
".venv\Scripts\python.exe" -m playwright install chromium

if not exist ".env" (
    copy .env.example .env >nul
    echo Создан .env из шаблона — впишите туда свои ключи.
)

echo.
echo Готово. Всё поставлено внутрь .venv, система не тронута.
echo Впишите ключи в .env и свою почту в USER_AGENT в config.py, затем запускайте
echo "HUNTER — собрать.bat" и "HUNTER — выгрузить.bat" как обычно.
pause
