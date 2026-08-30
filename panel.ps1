$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$PyExe = Join-Path $root ".pyembed\python.exe"
if (-not (Test-Path $PyExe)) {
    Write-Host "Окружение не найдено. Сначала запустите HUNTER - установить.bat"
    Read-Host "Нажмите Enter для выхода"
    exit 1
}

Write-Host "Открываю панель в браузере: http://127.0.0.1:5000/"
Write-Host "Это окно можно свернуть, но не закрывать - пока оно открыто, панель работает."
Write-Host "Закроете окно - панель выключится."

$env:PYTHONPATH = $root
& $PyExe -m webui.app
Read-Host "Нажмите Enter для выхода"
