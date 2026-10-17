param($Request)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$dry = $env:SANDBOX_DRYRUN -eq '1'

function Invoke-Change($Command, $Arguments) {
    if ($dry) {
        $parts = @($Arguments.Keys | Sort-Object | ForEach-Object {
            '-{0} {1}' -f $_, ($Arguments[$_] | ConvertTo-Json -Compress)
        })
        Write-Output ($Command + ' ' + ($parts -join ' '))
    } else {
        & $Command @Arguments | Out-Null
    }
}

try {
    if ($Request.action -eq 'list') {
        $names = @(Get-VM -Name 'sbx-*' | Where-Object Notes -eq 'sandbox-owned' |
            Select-Object -ExpandProperty Name)
        ConvertTo-Json -InputObject $names -Compress
        return
    }
    $name = [string]$Request.vm
    if ($name -notmatch '^sbx-[0-9]+-[0-9a-f]+$') {
        throw 'Invalid sandbox VM name'
    }
    $root = 'C:\sandbox\vms'
    $disk = Join-Path $root ($name + '.vhdx')
    $seed = Join-Path $root ($name + '.iso')
    switch ($Request.action) {
        'prepare' {
            if (-not $dry) {
                if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw 'VM already exists' }
                if ((Test-Path $disk) -or (Test-Path $seed)) { throw 'Sandbox files already exist' }
            }
            Invoke-Change 'New-Item' @{ItemType='Directory'; Path=$root; Force=$true}
        }
        'create' {
            $cpus = [int]::Parse([string]$Request.cpus)
            $memory = [long]::Parse([string]$Request.memory_mb) * 1MB
            $base = [string]$Request.image
            if (-not $dry) {
                if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw 'VM already exists' }
                if (-not (Test-Path $base)) { throw "Missing base image: $base" }
                if (-not (Test-Path $seed)) { throw "Missing seed ISO: $seed" }
            }
            Invoke-Change 'New-VHD' @{Differencing=$true; ParentPath=$base; Path=$disk}
            Invoke-Change 'New-VM' @{Name=$name; Generation=2; MemoryStartupBytes=$memory;
                VHDPath=$disk; SwitchName='Default Switch'}
            Invoke-Change 'Set-VM' @{Name=$name; Notes='sandbox-owned'}
            Invoke-Change 'Set-VMFirmware' @{VMName=$name; EnableSecureBoot='On';
                SecureBootTemplate='MicrosoftUEFICertificateAuthority'}
            Invoke-Change 'Set-VMProcessor' @{VMName=$name; Count=$cpus}
            Invoke-Change 'Add-VMDvdDrive' @{VMName=$name; Path=$seed}
            Invoke-Change 'Start-VM' @{Name=$name}
        }
        'ip' {
            $ip = @(Get-VMNetworkAdapter -VMName $name | ForEach-Object IPAddresses |
                Where-Object { $_ -match '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' -and
                    $_ -notmatch '^(127\.|169\.254\.|0\.)' } | Select-Object -First 1)
            if ($ip.Count) { ConvertTo-Json -InputObject $ip[0] -Compress }
            else { Write-Output 'null' }
        }
        'destroy' {
            $vm = $null
            if (-not $dry) {
                $vm = Get-VM -Name $name -ErrorAction SilentlyContinue
                if ($vm -and $vm.Notes -ne 'sandbox-owned') { throw 'VM is not sandbox-owned' }
            }
            if ($dry -or $vm) {
                Invoke-Change 'Stop-VM' @{Name=$name; TurnOff=$true; Force=$true}
                Invoke-Change 'Remove-VM' @{Name=$name; Force=$true}
            }
            foreach ($file in @($disk, $seed)) {
                if ($dry -or (Test-Path $file)) {
                    Invoke-Change 'Remove-Item' @{LiteralPath=$file; Force=$true}
                }
            }
        }
        default { throw 'Unknown Hyper-V action' }
    }
} catch {
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
