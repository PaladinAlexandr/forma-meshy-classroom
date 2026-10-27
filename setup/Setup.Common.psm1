Set-StrictMode -Version 2

function Test-FormaLanAddress {
    param([string]$Address)
    if ($Address -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { return $false }
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    $bytes = $parsed.GetAddressBytes()
    if ($bytes.Length -ne 4) { return $false }
    return ($bytes[0] -eq 10 -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31))
}

function New-FormaSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Get-FormaEnv {
    param([Parameter(Mandatory=$true)][string]$Path)
    $values = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [IO.File]::ReadAllLines($Path)) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
                $name = $Matches[1]
                $value = $Matches[2].Trim()
                if ($value.Length -ge 2 -and (($value.StartsWith("'") -and $value.EndsWith("'")) -or
                    ($value.StartsWith('"') -and $value.EndsWith('"')))) {
                    $value = $value.Substring(1, $value.Length - 2)
                } else { $value = ($value -replace '\s+#.*$', '').Trim() }
                $values[$name] = $value
            }
        }
    }
    return $values
}

function Set-FormaEnvValue {
    param([Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern('^[A-Z_][A-Z0-9_]*$')][string]$Name,
        [AllowEmptyString()][string]$Value)
    if ($Value -match "['\r\n\x00]") { throw 'Значение содержит недопустимые кавычки или перенос строки.' }
    $lines = @()
    if (Test-Path -LiteralPath $Path) { $lines = @([IO.File]::ReadAllLines($Path)) }
    $replacement = $Name + "='" + $Value + "'"
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=')) {
            if (-not $found) { $replacement; $found = $true }
        } else { $line }
    }
    if (-not $found) { $updated = @($updated) + $replacement }
    [IO.File]::WriteAllLines($Path, [string[]]@($updated), (New-Object Text.UTF8Encoding($false)))
}

function New-FormaEnv {
    param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][string]$Address)
    if (Test-Path -LiteralPath $Path) { throw 'Существующий файл настроек не будет перезаписан.' }
    if (-not (Test-FormaLanAddress $Address)) { throw 'Нужен локальный IPv4-адрес сети центра.' }
    $lines = @(
        '# Created by START-FORMA. Keep this file private.',
        "CLASSROOM_HOST=$Address",
        ('DJANGO_SECRET_KEY=' + (New-FormaSecret)),
        ('POSTGRES_PASSWORD=' + (New-FormaSecret)),
        'APP_TIME_ZONE=Asia/Yekaterinburg',
        'MESHY_MODE=demo', 'MESHY_API_KEY=',
        'MESHY_AI_MODEL=meshy-7.1', 'MESHY_ASSET_HOSTS=assets.meshy.ai'
    )
    [IO.File]::WriteAllLines($Path, $lines, (New-Object Text.UTF8Encoding($false)))
}

function Assert-FormaEnv {
    param([hashtable]$Values)
    foreach ($name in @('CLASSROOM_HOST','DJANGO_SECRET_KEY','POSTGRES_PASSWORD','MESHY_MODE')) {
        if (-not $Values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($Values[$name])) {
            throw "В .env.classroom отсутствует обязательное значение $name."
        }
    }
    if (-not (Test-FormaLanAddress $Values['CLASSROOM_HOST'])) { throw 'CLASSROOM_HOST должен быть локальным IPv4-адресом.' }
    if ($Values['DJANGO_SECRET_KEY'].Length -lt 40 -or $Values['DJANGO_SECRET_KEY'] -match '^(local-|REPLACE_|ЗАМЕНИТЕ|ПЕРВАЯ_)') {
        throw 'В .env.classroom требуется настоящий случайный DJANGO_SECRET_KEY. Скрипт не меняет существующий секрет автоматически.'
    }
    if ($Values['POSTGRES_PASSWORD'] -match '^(REPLACE_|ЗАМЕНИТЕ|ВТОРАЯ_)') {
        throw 'В .env.classroom остался пример пароля PostgreSQL. Укажите первоначально выбранный пароль базы.'
    }
    if ($Values['MESHY_MODE'] -notin @('demo','live')) { throw 'MESHY_MODE должен быть demo или live.' }
}

function Sync-FormaProcessEnv {
    param([hashtable]$Values)
    # Compose process variables have precedence over the env file. Keep them consistent.
    foreach ($name in @('CLASSROOM_HOST','DJANGO_SECRET_KEY','POSTGRES_PASSWORD','APP_TIME_ZONE',
        'MESHY_MODE','MESHY_API_KEY','MESHY_AI_MODEL','MESHY_IMAGE_MODEL','MESHY_ASSET_HOSTS')) {
        [Environment]::SetEnvironmentVariable($name, $Values[$name], 'Process')
    }
}

Export-ModuleMember -Function Test-FormaLanAddress,New-FormaSecret,Get-FormaEnv,Set-FormaEnvValue,New-FormaEnv,Assert-FormaEnv,Sync-FormaProcessEnv
