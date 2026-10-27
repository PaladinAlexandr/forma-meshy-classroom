param([switch]$CheckOnly, [switch]$NoBrowser, [switch]$LocalHttp)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
Import-Module (Join-Path $PSScriptRoot 'Setup.Common.psm1') -Force
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env.classroom'
$script:DockerExe = $null
$script:ClassroomPort = 8443
$composeFile = 'compose.classroom.yaml'
if ($LocalHttp) { $script:ClassroomPort = 8010; $composeFile = 'compose.classroom-http.yaml' }
$script:ComposeArgs = @('compose', '--env-file', $envPath, '-f', (Join-Path $projectRoot $composeFile))

function Find-Docker {
    $command = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe')
    )) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    throw 'Docker Desktop не найден. Установите его, затем повторно запустите START-FORMA.cmd.'
}

function Get-DockerOS {
    $process = New-Object Diagnostics.Process
    $process.StartInfo.FileName = $script:DockerExe
    $process.StartInfo.Arguments = 'info --format "{{.OSType}}"'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        [void]$process.Start()
        if (-not $process.WaitForExit(5000)) { $process.Kill(); return '' }
        $output = $process.StandardOutput.ReadToEnd().Trim()
        if ($process.ExitCode -eq 0) { return $output }
        return ''
    } finally { $process.Dispose() }
}

function Wait-Docker {
    $os = Get-DockerOS
    if ($os -eq 'windows') { throw 'Переключите Docker Desktop в режим Linux containers и повторите запуск.' }
    if ($os -eq 'linux') { return }
    Write-Host 'Запускаем Docker Desktop и ждём готовности движка...'
    $desktop = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($desktop) { Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null }
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 3
        $os = Get-DockerOS
        if ($os -eq 'linux') { return }
        if ($os -eq 'windows') { throw 'В Docker включены Windows containers. Выберите Linux containers и повторите запуск.' }
    } while ((Get-Date) -lt $deadline)
    throw 'Docker не стал готов за 3 минуты. Откройте Docker Desktop, завершите его первоначальную настройку и повторите запуск.'
}

function Invoke-FormaCompose {
    param([string[]]$Arguments)
    & $script:DockerExe @script:ComposeArgs @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Docker Compose не выполнил команду ' + $Arguments[0] + '. Посмотрите сообщение выше.') }
}

function Get-SetupStatus {
    $output = @(& $script:DockerExe @script:ComposeArgs exec -T web python manage.py setup_status)
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить состояние приложения. Проверьте журнал сервиса web.' }
    try { return (($output -join "`n") | ConvertFrom-Json) }
    catch { throw 'Приложение вернуло неожиданный ответ при проверке установки.' }
}

function Select-LanAddress {
    param([string]$Previous)
    $addresses = @(
        foreach ($config in @(Get-NetIPConfiguration -ErrorAction Stop)) {
            if ($config.InterfaceAlias -match 'vEthernet|Docker|WSL|Loopback') { continue }
            foreach ($ip in @($config.IPv4Address)) {
                if ($ip -and (Test-FormaLanAddress $ip.IPAddress)) {
                    [pscustomobject]@{ Address=$ip.IPAddress; InterfaceIndex=$config.InterfaceIndex;
                        Alias=$config.InterfaceAlias; HasGateway=($null -ne $config.IPv4DefaultGateway) }
                }
            }
        }
    )
    $addresses = @($addresses | Sort-Object -Property @{Expression='HasGateway';Descending=$true},Alias,Address)
    if ($addresses.Count -eq 0) { throw 'Не найден локальный IP. Подключите компьютер к сети центра и повторите запуск.' }
    $existing = @($addresses | Where-Object Address -eq $Previous)
    if ($existing.Count -gt 0) { return $existing[0] }
    if ($addresses.Count -eq 1) { return $addresses[0] }
    Write-Host 'Выберите сеть, к которой подключены компьютеры учеников:'
    for ($i=0; $i -lt $addresses.Count; $i++) {
        Write-Host ('  {0}. {1} — {2}' -f ($i+1), $addresses[$i].Alias, $addresses[$i].Address)
    }
    do {
        $choice = Read-Host 'Номер сети [1]'
        if ([string]::IsNullOrWhiteSpace($choice)) { $choice = '1' }
        $number = 0
        $valid = [int]::TryParse($choice, [ref]$number) -and $number -ge 1 -and $number -le $addresses.Count
    } until ($valid)
    return $addresses[$number-1]
}

function Read-ApiKey {
    Write-Host 'Введите API-ключ Meshy для настоящей генерации.'
    Write-Host 'Нажмите Enter без ключа, чтобы пока использовать учебный режим.'
    do {
        $secure = Read-Host 'API-ключ (ввод скрыт)' -AsSecureString
        $pointer = [IntPtr]::Zero
        try {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
        } finally {
            if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
            $secure.Dispose()
        }
        if ($key.Contains('"') -or $key -match "['\r\n\x00]") {
            Write-Host 'Скопируйте только ключ, без кавычек и переносов строк.' -ForegroundColor Yellow
        } else { return $key }
    } while ($true)
}

function Enable-ClassroomNetwork {
    param($Selected)
    # The main wizard stays under the original user so the CA is installed into
    # that user's trust store. Only this narrow helper requests administrator rights.
    Write-Host 'Настраиваем доступ ученикам. Windows может запросить права администратора.'
    Write-Host ('Выбранная сеть центра будет частной; порт ' + $script:ClassroomPort + ' разрешён только для локальной подсети.')
    $helper = Join-Path $PSScriptRoot 'configure-network.ps1'
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $helper + '" -InterfaceIndex ' +
        [int]$Selected.InterfaceIndex + ' -Address ' + $Selected.Address + ' -Port ' + $script:ClassroomPort
    try {
        $process = Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments `
            -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw 'Не удалось изменить настройки сети.' }
        return $true
    } catch {
        Write-Host 'Автоматическая настройка сети не завершена. Приложение запускается, но доступ учеников может потребовать помощи администратора.' -ForegroundColor Yellow
        return $false
    }
}

function Install-ClassroomCertificate {
    $certPath = Join-Path $projectRoot 'forma-root.crt'
    $copied = $false
    for ($attempt=0; $attempt -lt 12; $attempt++) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $output = @(& $script:DockerExe @script:ComposeArgs cp 'proxy:/data/caddy/pki/authorities/local/root.crt' $certPath 2>&1)
            $copyExitCode = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        if ($copyExitCode -eq 0 -and (Test-Path -LiteralPath $certPath)) { $copied = $true; break }
        Start-Sleep -Seconds 3
    }
    if (-not $copied) { throw 'Сертификат HTTPS не создан. Проверьте журнал: .\forma.cmd logs --tail=100 proxy' }
    $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($certPath)
    if (-not (Test-Path -LiteralPath ('Cert:\CurrentUser\Root\' + $cert.Thumbprint))) {
        Write-Host 'Добавляем доверие к HTTPS этого сервера. Если Windows запросит подтверждение, проверьте сертификат сервера центра.'
        & certutil.exe -user -addstore Root $certPath | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить доверие к HTTPS. Повторите запуск или обратитесь к администратору.' }
    }
    return $certPath
}

function New-StudentPackage {
    param([string]$Url, [string]$Certificate)
    $folder = Join-Path $projectRoot 'student-access'
    [void](New-Item -ItemType Directory -Path $folder -Force)
    Copy-Item -LiteralPath $Certificate -Destination (Join-Path $folder 'forma-root.crt') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'connect-student.ps1') -Destination (Join-Path $folder 'connect-student.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'CONNECT-STUDENT.cmd') -Destination (Join-Path $folder 'CONNECT-STUDENT.cmd') -Force
    [IO.File]::WriteAllText((Join-Path $folder 'address.txt'), $Url, (New-Object Text.UTF8Encoding($false)))
    $instructions = 'Распакуйте архив целиком и запустите CONNECT-STUDENT.cmd под Windows-пользователем ученика. Подтвердите доверие сертификату центра, если Windows спросит. Затем войдите с ученическим логином и паролем, выданными преподавателем. Docker детям не нужен. Получайте этот пакет только у вашего центра.'
    [IO.File]::WriteAllText((Join-Path $folder 'READ-ME.txt'), $instructions, (New-Object Text.UTF8Encoding($true)))
    $archive = Join-Path $projectRoot 'FORMA-student-access.zip'
    $publicFiles = @(foreach ($name in @('forma-root.crt','connect-student.ps1','CONNECT-STUDENT.cmd','address.txt','READ-ME.txt')) {
        Join-Path $folder $name
    })
    # Include only known public files, even if someone placed other data in this folder.
    Compress-Archive -LiteralPath $publicFiles -DestinationPath $archive -Force
    return $archive
}

$mutex = $null
$mutexHeld = $false
try {
    Set-Location -LiteralPath $projectRoot
    Write-Host 'FORMA — запуск мастерской' -ForegroundColor Green
    $script:DockerExe = Find-Docker
    if ($CheckOnly) {
        Write-Host ('Docker CLI: ' + $script:DockerExe)
        Write-Host ('Linux engine: ' + ((Get-DockerOS) -eq 'linux'))
        Write-Host ('Настройки существуют: ' + (Test-Path -LiteralPath $envPath))
        Write-Host 'Режим проверки: настройки, сеть и контейнеры не изменялись.'
        exit 0
    }
    $mutex = New-Object Threading.Mutex($false, 'Local\FORMA-Classroom-Setup')
    $mutexHeld = $mutex.WaitOne(0)
    if (-not $mutexHeld) { throw 'Установка FORMA уже выполняется в другом окне.' }
    Wait-Docker
    & $script:DockerExe compose version --short
    if ($LASTEXITCODE -ne 0) { throw 'Не найден Docker Compose. Обновите Docker Desktop.' }

    $values = Get-FormaEnv $envPath
    $selected = Select-LanAddress $values['CLASSROOM_HOST']
    Write-Host ('Сеть центра: ' + $selected.Alias + ' / ' + $selected.Address)
    if (-not (Test-Path -LiteralPath $envPath)) {
        New-FormaEnv -Path $envPath -Address $selected.Address
        Write-Host 'Случайные секреты созданы автоматически. Настройки сохранены в .env.classroom.'
    } elseif ($values['CLASSROOM_HOST'] -ne $selected.Address) {
        Set-FormaEnvValue -Path $envPath -Name CLASSROOM_HOST -Value $selected.Address
        Write-Host 'Локальный IP обновлён; пароли и API-ключ сохранены.'
    }
    $values = Get-FormaEnv $envPath
    Assert-FormaEnv $values
    Sync-FormaProcessEnv $values
    Invoke-FormaCompose -Arguments @('config','--quiet')
    if ($LocalHttp) { Invoke-FormaCompose -Arguments @('stop','proxy') }
    Write-Host 'Собираем и запускаем сервисы. Первый запуск может занять несколько минут.'
    Invoke-FormaCompose -Arguments @('up','-d','--build','--wait','--wait-timeout','600')

    $status = Get-SetupStatus
    if (-not $status.admin_exists) {
        Write-Host 'Создайте администратора: укажите логин и пароль. При вводе пароля символы не отображаются.' -ForegroundColor Green
        Invoke-FormaCompose -Arguments @('exec','web','python','manage.py','createsuperuser')
        $status = Get-SetupStatus
        if (-not $status.admin_exists) { throw 'Администратор не создан. Повторите запуск и завершите создание аккаунта.' }
    } else { Write-Host 'Администратор уже существует: его аккаунт и пароль сохранены.' }

    if ($status.mode -ne 'live' -or -not $status.api_configured) {
        if ($status.generation_tasks -gt 0) {
            Write-Host 'Есть незавершённые генерации. Текущий режим сохранён; после их завершения повторите запуск для подключения ключа.' -ForegroundColor Yellow
        } else {
            $key = Read-ApiKey
            if (-not [string]::IsNullOrWhiteSpace($key)) {
                Set-FormaEnvValue -Path $envPath -Name MESHY_API_KEY -Value $key
                Set-FormaEnvValue -Path $envPath -Name MESHY_MODE -Value 'live'
                Write-Host 'Ключ сохранён. Одобренные заявки будут использовать кредиты Meshy.'
            } elseif ($status.mode -eq 'live' -and -not $status.api_configured) {
                Set-FormaEnvValue -Path $envPath -Name MESHY_MODE -Value 'demo'
            }
            $key = $null
            $values = Get-FormaEnv $envPath
            Sync-FormaProcessEnv $values
            Invoke-FormaCompose -Arguments @('up','-d','--wait','--wait-timeout','600')
            $status = Get-SetupStatus
        }
    }

    $networkReady = Enable-ClassroomNetwork $selected
    $studentArchive = $null
    if ($LocalHttp) {
        $url = 'http://' + $selected.Address + ':8010'
        Write-Host 'Локальный HTTP: сертификаты не нужны. Пароли и фотографии передаются без шифрования.' -ForegroundColor Yellow
    } else {
        $certificate = Install-ClassroomCertificate
        $url = 'https://' + $selected.Address + ':8443'
        $studentArchive = New-StudentPackage -Url $url -Certificate $certificate
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $ready = $false
    $lastHealthError = ''
    Write-Host ('Проверяем подключение напрямую, без системного прокси: ' + $url)
    for ($attempt=0; $attempt -lt 8; $attempt++) {
        $healthResponse = $null
        $reader = $null
        try {
            # School proxy/PAC settings must not route private LAN traffic externally.
            # Keep normal Windows certificate validation; never bypass TLS checks.
            $request = [Net.HttpWebRequest]::Create($url + '/health/')
            $request.Proxy = $null
            $request.Timeout = 10000
            $request.ReadWriteTimeout = 10000
            $request.AllowAutoRedirect = $false
            $request.KeepAlive = $false
            $healthResponse = $request.GetResponse()
            $reader = New-Object IO.StreamReader($healthResponse.GetResponseStream())
            $body = $reader.ReadToEnd()
            if ([int]$healthResponse.StatusCode -eq 200 -and ($body | ConvertFrom-Json).status -eq 'ok') {
                $ready = $true
                break
            }
            $lastHealthError = 'Ответ /health/ не содержит status=ok.'
        } catch {
            $messages = @()
            $cause = $_.Exception
            while ($cause) { $messages += $cause.Message; $cause = $cause.InnerException }
            $lastHealthError = $messages -join ' -> '
        } finally {
            if ($reader) { $reader.Dispose() }
            if ($healthResponse) { $healthResponse.Dispose() }
        }
        if ($attempt -lt 7) { Start-Sleep -Seconds 3 }
    }
    if (-not $ready) {
        Write-Host ('Причина проверки подключения: ' + $lastHealthError) -ForegroundColor Yellow
        Write-Host ('Откройте вручную в браузере: ' + $url + '/login/')
        Write-Host ('Диагностика контейнеров: docker compose --env-file .env.classroom -f ' + $composeFile + ' ps')
        Write-Host ('Журнал: docker compose --env-file .env.classroom -f ' + $composeFile + ' logs --tail=60 proxy web')
        if ($LocalHttp) {
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                Write-Host 'Публикация порта Docker:'
                & $script:DockerExe @script:ComposeArgs port web 8000 | Out-Host
                Write-Host 'Проверка через localhost:'
                try {
                    $localRequest = [Net.HttpWebRequest]::Create('http://127.0.0.1:8010/health/')
                    $localRequest.Proxy = $null
                    $localRequest.Timeout = 5000
                    $localResponse = $localRequest.GetResponse()
                    Write-Host ('localhost HTTP: ' + [int]$localResponse.StatusCode)
                    $localResponse.Dispose()
                } catch { Write-Host $_.Exception.GetBaseException().Message }
                Write-Host 'Последние сообщения web:'
                & $script:DockerExe @script:ComposeArgs logs --tail=20 web | Out-Host
            } finally { $ErrorActionPreference = $previousPreference }
        }
        throw ('Контейнеры запущены, но проверка подключения не прошла: ' + $url + '. Данные сохранены; причина указана выше.')
    }

    Write-Host ''
    Write-Host ('Мастерская запущена: ' + $url) -ForegroundColor Green
    Write-Host ('Администрирование: ' + $url + '/admin/')
    Write-Host ('Режим генерации: ' + $status.mode + '; параллельных задач: ' + $status.max_parallel)
    if ($LocalHttp) {
        Write-Host 'Ученики открывают этот HTTP-адрес в браузере. Архив и сертификат не нужны.'
    } else {
        Write-Host ('Архив для учеников: ' + $studentArchive)
        Write-Host 'На ученических ПК распакуйте этот архив и запустите CONNECT-STUDENT.cmd.'
    }
    if (-not $networkReady) { Write-Host 'Для сетевого доступа ещё нужно завершить настройку брандмауэра с администратором Windows.' -ForegroundColor Yellow }
    if (-not $NoBrowser) { Start-Process ($url + '/login/') }
    if (-not $networkReady) { exit 2 }
    exit 0
} catch {
    Write-Host ''
    Write-Host ('Остановка настройки: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Созданные данные не удалены. После исправления проблемы повторите запуск выбранного START-FORMA файла.'
    exit 1
} finally {
    if ($mutexHeld -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
