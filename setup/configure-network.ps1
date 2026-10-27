param([Parameter(Mandatory=$true)][int]$InterfaceIndex,
    [Parameter(Mandatory=$true)][ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')][string]$Address,
    [ValidateSet(8010,8443)][int]$Port = 8443)
$ErrorActionPreference = 'Stop'
try {
    $owned = @(Get-NetIPAddress -InterfaceIndex $InterfaceIndex -AddressFamily IPv4 -ErrorAction Stop | Where-Object IPAddress -eq $Address)
    if ($owned.Count -eq 0) { throw 'Выбранный адрес больше не принадлежит адаптеру.' }
    $networkProfiles = @(Get-NetConnectionProfile -InterfaceIndex $InterfaceIndex -ErrorAction Stop)
    foreach ($networkProfile in $networkProfiles) {
        if ($networkProfile.NetworkCategory -eq 'Public') {
            Set-NetConnectionProfile -InterfaceIndex $InterfaceIndex -NetworkCategory Private -ErrorAction Stop
        }
    }
    $name = 'FORMA-Classroom-' + $Port
    $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule -Name $name -DisplayName ('FORMA classroom TCP ' + $Port) -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -LocalAddress $Address -RemoteAddress LocalSubnet -Profile Private,Domain | Out-Null
    } else {
        Set-NetFirewallRule -Name $name -Enabled True -Direction Inbound -Action Allow -Profile Private,Domain | Out-Null
        $rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -LocalAddress $Address -RemoteAddress LocalSubnet | Out-Null
        $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $Port | Out-Null
    }
    exit 0
} catch {
    exit 1
}
