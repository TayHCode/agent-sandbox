param($Request)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    if ($Request.action -eq 'list') {
        $names = @(Get-VM -Name 'sbx-*' | Where-Object Notes -eq 'sandbox-owned' |
            Select-Object -ExpandProperty Name)
        ConvertTo-Json -InputObject $names -Compress
        return
    }
    if ($Request.action -eq 'leftovers') {
        $found = @(Get-ChildItem 'C:\sandbox\vms' -Force -ErrorAction SilentlyContinue |
            ForEach-Object { if ($_.Name -match '^(sbx-[0-9]+-[0-9a-f]+)(\.|_|$)') { $Matches[1] } } |
            Sort-Object -Unique | Where-Object { -not (Get-VM -Name $_ -ErrorAction SilentlyContinue) })
        ConvertTo-Json -InputObject $found -Compress
        return
    }
    $name = [string]$Request.vm
    if ($name -notmatch '^sbx-[0-9]+-[0-9a-f]+$') { throw 'Invalid sandbox VM name' }
    $root = 'C:\sandbox\vms'
    $dir = Join-Path $root $name
    $disk = Join-Path $dir 'child.vhdx'
    $vm = Get-VM -Name $name -ErrorAction SilentlyContinue
    if ($vm -and $vm.Notes -ne 'sandbox-owned') { throw 'VM is not sandbox-owned' }
    if ($Request.action -eq 'destroy') {
        if ($vm) {
            Stop-VM -Name $name -TurnOff -Force
            Remove-VM -Name $name -Force
        }
        if (Test-Path -LiteralPath $disk) {
            if ((Get-VHD -Path $disk).Attached) { Dismount-VHD -Path $disk }
        }
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        return
    }
    if ($Request.action -eq 'create') {
        $mutex = New-Object Threading.Mutex($false, 'Global\SandboxWindowsCreate')
        $locked = $false
        try {
            try { $locked = $mutex.WaitOne() } catch [Threading.AbandonedMutexException] { $locked = $true }
            if (Test-Path -LiteralPath $dir) { throw 'Sandbox directory already exists' }
            $base = [string]$Request.image
            if (-not (Test-Path -LiteralPath $base)) { throw "Missing base image: $base" }
            $cpus = [int]::Parse([string]$Request.cpus)
            $memory = [long]::Parse([string]$Request.memory_mb) * 1MB
            New-VM -Name $name -Generation 2 -MemoryStartupBytes $memory -Path $dir -NoVHD -SwitchName 'Default Switch' | Out-Null
            Set-VM -Name $name -Notes 'sandbox-owned'
            New-VHD -Path $disk -ParentPath $base -Differencing | Out-Null
            $mount = Join-Path $dir 'mount'
            New-Item -ItemType Directory -Path $mount | Out-Null
            try {
                Mount-VHD -Path $disk -NoDriveLetter
                $d = Get-VHD -Path $disk | Get-Disk
                $part = Get-Partition -DiskNumber $d.Number | Where-Object Type -eq 'Basic' |
                    Sort-Object Size -Descending | Select-Object -First 1
                Add-PartitionAccessPath -DiskNumber $d.Number -PartitionNumber $part.PartitionNumber -AccessPath ($mount + '\')
                $state = Get-Content -LiteralPath (Join-Path $mount 'Windows\Setup\State\State.ini') -Raw
                $panther = Join-Path $mount 'Windows\Panther'
                New-Item -ItemType Directory -Path $panther -Force | Out-Null
                if ($state -match 'IMAGE_STATE_COMPLETE') {
                    $suffix = $name.Split('-')[-1]
                    $computer = "SBX-$($suffix.ToUpper())"
                    $hive = "sbx$suffix"
                    reg load "HKLM\${hive}_system" (Join-Path $mount 'Windows\System32\config\SYSTEM') | Out-Null
                    if ($LASTEXITCODE) { throw 'Could not load the guest SYSTEM hive' }
                    reg load "HKLM\${hive}_software" (Join-Path $mount 'Windows\System32\config\SOFTWARE') | Out-Null
                    if ($LASTEXITCODE) { reg unload "HKLM\${hive}_system" | Out-Null; throw 'Could not load the guest SOFTWARE hive' }
                    try {
                        $control = "Registry::HKEY_LOCAL_MACHINE\${hive}_system\ControlSet001"
                        Set-ItemProperty -LiteralPath "$control\Control\ComputerName\ComputerName" -Name ComputerName -Value $computer
                        Set-ItemProperty -LiteralPath "$control\Services\Tcpip\Parameters" -Name 'NV Hostname' -Value $computer
                        Set-ItemProperty -LiteralPath "$control\Services\Tcpip\Parameters" -Name Hostname -Value $computer
                        Set-ItemProperty -LiteralPath "Registry::HKEY_LOCAL_MACHINE\${hive}_software\Microsoft\Cryptography" -Name MachineGuid -Value ([guid]::NewGuid().ToString())
                    } finally {
                        [GC]::Collect()
                        [GC]::WaitForPendingFinalizers()
                        foreach ($key in @("${hive}_system", "${hive}_software")) {
                            for ($try = 0; $try -lt 10; $try++) {
                                reg unload "HKLM\$key" | Out-Null
                                if (-not $LASTEXITCODE) { break }
                                Start-Sleep -Milliseconds 500
                            }
                        }
                    }
                    $init = ConvertTo-Json -Compress -InputObject @{ password = [string]$Request.password }
                    [IO.File]::WriteAllText((Join-Path $panther 'sbx-init.json'), $init, (New-Object Text.UTF8Encoding($false)))
                } elseif ($state -match 'IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE') {
                    $escapedLogon = [Security.SecurityElement]::Escape([string]$Request.password)
                    $unattend = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
 <settings pass="specialize">
  <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><ComputerName>SBX-$($name.Split('-')[-1])</ComputerName></component>
 </settings>
 <settings pass="oobeSystem">
  <component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale></component>
  <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
   <OOBE><HideEULAPage>true</HideEULAPage><HideOEMRegistrationScreen>true</HideOEMRegistrationScreen><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><ProtectYourPC>3</ProtectYourPC><SkipMachineOOBE>true</SkipMachineOOBE><SkipUserOOBE>true</SkipUserOOBE></OOBE>
   <AutoLogon><Password><Value>$escapedLogon</Value><PlainText>true</PlainText></Password><Enabled>true</Enabled><LogonCount>1</LogonCount><Username>sandboxadmin</Username></AutoLogon>
   <UserAccounts><LocalAccounts><LocalAccount wcm:action="add"><Password><Value>$escapedLogon</Value><PlainText>true</PlainText></Password><Group>Administrators</Group><Name>sandboxadmin</Name></LocalAccount></LocalAccounts></UserAccounts>
  </component>
 </settings>
</unattend>
"@
                    [IO.File]::WriteAllText((Join-Path $panther 'unattend.xml'), $unattend, (New-Object Text.UTF8Encoding($false)))
                } else {
                    throw 'Base must be generalized and awaiting OOBE, or finished with OOBE'
                }
            } finally {
                if ((Get-VHD -Path $disk).Attached) { Dismount-VHD -Path $disk }
                Remove-Item -LiteralPath $mount -Force
            }
            Add-VMHardDiskDrive -VMName $name -Path $disk
            Set-VMFirmware -VMName $name -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows -FirstBootDevice (Get-VMHardDiskDrive -VMName $name)
            Set-VMProcessor -VMName $name -Count $cpus
            $deadline = (Get-Date).AddMinutes(10)
            while ((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1KB -lt ($memory + 2GB)) {
                if ((Get-Date) -gt $deadline) { throw 'Not enough free host memory to start the VM' }
                Start-Sleep -Seconds 5
            }
            Start-VM -Name $name
        } finally {
            if ($locked) { $mutex.ReleaseMutex() }
            $mutex.Dispose()
        }
        return
    }
    if (-not $vm) { throw 'Sandbox VM does not exist' }
    if ($Request.action -in @('stage', 'unstage', 'put', 'get')) {
        if ([string]$Request.stage -notmatch '^[0-9a-f]{32}$') { throw 'Invalid staging name' }
        $stage = Join-Path $dir ([string]$Request.stage)
        $payload = Join-Path $stage 'payload'
        if ($Request.action -eq 'stage') {
            New-Item -ItemType Directory -Path $stage | Out-Null
            return
        }
        if ($Request.action -eq 'unstage') {
            if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
            return
        }
    }
    $credential = New-Object Management.Automation.PSCredential('sandboxadmin',
        (ConvertTo-SecureString ([string]$Request.password) -AsPlainText -Force))
    if ($Request.action -eq 'ready') {
        $vm = Get-VM -Name $name
        if ($vm.State.ToString() -ne 'Running') { Write-Output '"stopped"'; return }
        if ($vm.Heartbeat.ToString() -notlike 'Ok*') { Write-Output 'false'; return }
        $signalled = $false
        $component = Get-CimInstance -Namespace 'root\virtualization\v2' -ClassName Msvm_ComputerSystem -Filter "ElementName='$name'" |
            Get-CimAssociatedInstance -ResultClassName Msvm_KvpExchangeComponent
        foreach ($item in @($component.GuestExchangeItems)) {
            $fields = @{}
            foreach ($property in ([xml]$item).INSTANCE.PROPERTY) { $fields[$property.GetAttribute('NAME')] = [string]$property.VALUE }
            if ($fields['Name'] -eq 'sbx_ready') { $signalled = $true }
        }
        if (-not $signalled -and $vm.Uptime.TotalSeconds -lt 15) { Write-Output 'false'; return }
        try { $session = New-PSSession -VMName $name -Credential $credential } catch {
            if ((Get-VM -Name $name).State.ToString() -ne 'Running') { Write-Output '"stopped"'; return }
            Write-Output 'false'
            return
        }
    } else { $session = New-PSSession -VMName $name -Credential $credential }
    try {
        switch ($Request.action) {
            'ready' {
                Invoke-Command -Session $session -ScriptBlock {
                    $ErrorActionPreference = 'Stop'
                    foreach ($file in @('C:\Windows\Panther\unattend.xml', 'C:\Windows\Panther\Unattend\unattend.xml')) {
                        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
                    }
                    Remove-Item -LiteralPath 'C:\Windows\Panther\sbx-init.json' -Force -ErrorAction SilentlyContinue
                    $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
                    Set-ItemProperty -LiteralPath $key -Name AutoAdminLogon -Value '0'
                    Remove-ItemProperty -LiteralPath $key -Name DefaultPassword -ErrorAction SilentlyContinue
                    New-Item -ItemType Directory -Path C:\sandbox -Force | Out-Null
                }
                Write-Output 'true'
            }
            'exec' {
                $answer = Invoke-Command -Session $session -ArgumentList $Request -ScriptBlock {
                    param($r)
                    $ErrorActionPreference = 'Stop'
                    $info = New-Object Diagnostics.ProcessStartInfo
                    $info.FileName = [string]$r.executable
                    $info.Arguments = [string]$r.arguments
                    $info.WorkingDirectory = 'C:\sandbox'
                    $info.UseShellExecute = $false
                    $info.RedirectStandardInput = $true
                    $info.RedirectStandardOutput = $true
                    $info.RedirectStandardError = $true
                    $p = New-Object Diagnostics.Process
                    $p.StartInfo = $info
                    $out = New-Object IO.MemoryStream
                    $err = New-Object IO.MemoryStream
                    try {
                        $null = $p.Start()
                        $outTask = $p.StandardOutput.BaseStream.CopyToAsync($out)
                        $errTask = $p.StandardError.BaseStream.CopyToAsync($err)
                        $inputBytes = [Convert]::FromBase64String([string]$r.stdin)
                        $p.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
                        $p.StandardInput.Close()
                        $p.WaitForExit()
                        $null = $outTask.GetAwaiter().GetResult()
                        $null = $errTask.GetAwaiter().GetResult()
                        [pscustomobject]@{returncode=$p.ExitCode; stdout=[Convert]::ToBase64String($out.ToArray()); stderr=[Convert]::ToBase64String($err.ToArray())}
                    } finally { $p.Dispose(); $out.Dispose(); $err.Dispose() }
                }
                $answer | Select-Object returncode,stdout,stderr | ConvertTo-Json -Compress
            }
            'put' {
                $remote = Invoke-Command -Session $session -ArgumentList ([string]$Request.remote) -ScriptBlock {
                    param($path)
                    if (-not [IO.Path]::IsPathRooted($path)) { $path = Join-Path C:\sandbox $path }
                    $parent = Split-Path $path -Parent
                    New-Item -ItemType Directory -Path $parent -Force | Out-Null
                    $path
                }
                Copy-Item -LiteralPath $payload -Destination $remote -ToSession $session -Recurse -Force
            }
            'get' {
                $remote = [string]$Request.remote
                if (-not [IO.Path]::IsPathRooted($remote)) { $remote = Join-Path C:\sandbox $remote }
                Copy-Item -LiteralPath $remote -Destination $payload -FromSession $session -Recurse -Force
            }
            default { throw 'Unknown Hyper-V Windows action' }
        }
    } finally { Remove-PSSession -Session $session }
} catch {
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
