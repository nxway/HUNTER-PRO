$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$PyExe = Join-Path $root ".pyembed\python.exe"
if (-not (Test-Path $PyExe)) {
    Write-Host "Окружение не найдено. Сначала запустите HUNTER - установить.bat"
    Read-Host "Нажмите Enter для выхода"
    exit 1
}

$hunterPy = Join-Path $root "hunter.py"

Write-Host "Создаю задание в планировщике Windows: hunter.py run каждую ночь в 02:00..."
schtasks /create /tn "HUNTER-PRO nightly" /tr "`"$PyExe`" `"$hunterPy`" run" /sc daily /st 02:00 /f

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Готово. Задание HUNTER-PRO nightly создано, запуск каждую ночь в 02:00."
    Write-Host "Компьютер спит в это время - ничего страшного, следующей ночью догонит."
    Write-Host "Если хотите наверняка: планировщик заданий Windows -> найдите задание ->"
    Write-Host "Свойства -> вкладка Условия -> Выводить компьютер из ждущего режима."
} else {
    Write-Host ""
    Write-Host "Не удалось создать задание. Запустите этот файл от имени администратора."
}
Read-Host "Нажмите Enter для выхода"
