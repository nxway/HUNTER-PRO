$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$PyVer = "3.12.7"
$PyDir = Join-Path $root ".pyembed"
$PyExe = Join-Path $PyDir "python.exe"

try {
    if (-not (Test-Path $PyExe)) {
        Write-Host "Скачиваю портативный Python $PyVer (только в эту папку, не в систему)..."
        New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

        $zipUrl = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip"
        $zipPath = Join-Path $env:TEMP "hunter-python-embed.zip"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $PyDir -Force
        Remove-Item $zipPath -ErrorAction SilentlyContinue

        # В портативной сборке site-packages выключены по умолчанию —
        # включаем, иначе pip и наши зависимости не заработают.
        $pthFile = Get-ChildItem -Path $PyDir -Filter "python3*._pth" | Select-Object -First 1
        if ($pthFile) {
            (Get-Content $pthFile.FullName) -replace '#import site', 'import site' | Set-Content $pthFile.FullName
        }

        Write-Host "Устанавливаю pip..."
        $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
        $getPipPath = Join-Path $env:TEMP "hunter-get-pip.py"
        Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipPath
        & $PyExe $getPipPath --no-warn-script-location
        Remove-Item $getPipPath -ErrorAction SilentlyContinue
    }

    Write-Host "Ставлю зависимости проекта внутрь .pyembed..."
    & $PyExe -m pip install --no-warn-script-location -r (Join-Path $root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "pip install завершился с ошибкой (код $LASTEXITCODE) — смотрите текст выше"
    }

    & $PyExe -m playwright install chromium

    $envFile = Join-Path $root ".env"
    $envExample = Join-Path $root ".env.example"
    if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
        Copy-Item $envExample $envFile
        Write-Host "Создан .env из шаблона — впишите туда свои ключи."
    }

    Write-Host ""
    Write-Host "Готово. Python и все библиотеки — только в папке .pyembed рядом с проектом."
    Write-Host "Ничего не установлено в систему. Впишите ключи в .env и свою почту"
    Write-Host "в USER_AGENT в config.py, затем запускайте .bat-ярлыки как обычно."
}
catch {
    Write-Host ""
    Write-Host "ОШИБКА: $($_.Exception.Message)"
    Write-Host "Полный текст ошибки выше — пришлите его, если непонятно, что делать."
    Read-Host "Нажмите Enter для выхода"
    exit 1
}

Read-Host "Нажмите Enter для выхода"
