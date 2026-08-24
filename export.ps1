$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$PyExe = Join-Path $root ".pyembed\python.exe"
if (-not (Test-Path $PyExe)) {
    Write-Host "Окружение не найдено. Сначала запустите HUNTER - установить.bat"
    Read-Host "Нажмите Enter для выхода"
    exit 1
}

$env:PYTHONPATH = $root
& $PyExe (Join-Path $root "hunter.py") export
Read-Host "Нажмите Enter для выхода"
