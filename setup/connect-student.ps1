$ErrorActionPreference = 'Stop'
try {
    $address = ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'address.txt'))).Trim()
    $url = $null
    if (-not [Uri]::TryCreate($address, [UriKind]::Absolute, [ref]$url) -or $url.Scheme -ne 'https' -or $url.Port -ne 8443) {
        throw 'Некорректный адрес сервера. Получите пакет подключения у преподавателя.'
    }
    $certFile = Join-Path $PSScriptRoot 'forma-root.crt'
    $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($certFile)
    if (-not (Test-Path -LiteralPath ('Cert:\CurrentUser\Root\' + $cert.Thumbprint))) {
        Write-Host 'Добавляем доверие к HTTPS-сертификату сервера центра. При запросе Windows подтвердите установку.'
        & certutil.exe -user -addstore Root $certFile
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить сертификат. Обратитесь к преподавателю.' }
    }
    Write-Host ('Открываем мастерскую: ' + $address)
    Start-Process $address
    exit 0
} catch {
    Write-Host ('Ошибка: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
