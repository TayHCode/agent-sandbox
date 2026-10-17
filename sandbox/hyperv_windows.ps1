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
            if (Get-VM -Name 'sbx-*') { throw 'Another sandbox exists; run gc first' }
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
                if ($state -notmatch 'IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE') {
                    throw 'Base must be generalized and awaiting OOBE'
                }
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
                $panther = Join-Path $mount 'Windows\Panther'
                New-Item -ItemType Directory -Path $panther -Force | Out-Null
                [IO.File]::WriteAllText((Join-Path $panther 'unattend.xml'), $unattend, (New-Object Text.UTF8Encoding($false)))
            } finally {
                if ((Get-VHD -Path $disk).Attached) { Dismount-VHD -Path $disk }
                Remove-Item -LiteralPath $mount -Force
            }
            Add-VMHardDiskDrive -VMName $name -Path $disk
            Set-VMFirmware -VMName $name -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows
            Set-VMProcessor -VMName $name -Count $cpus
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
        try { $session = New-PSSession -VMName $name -Credential $credential } catch {
            if ((Get-VM -Name $name).State.ToString() -ne 'Running') { throw 'Sandbox stopped before readiness' }
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
