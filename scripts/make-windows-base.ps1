<#
Builds a generalized Windows 11 Enterprise Evaluation base disk from the ISO you download from Microsoft.
Run in an elevated PowerShell on the Hyper-V host.

  .\make-windows-base.ps1 -Iso C:\sandbox\media\Win11_Enterprise_Eval.iso

The output is a read-only dynamic VHDX that the sandbox uses as the parent of every Windows guest.

Second step, the default base: boot that generalized base once, let specialize and OOBE finish, and keep the
result so guests skip both (about 130 s of every create):

  .\make-windows-base.ps1 -PostOobeFrom C:\sandbox\base\windows11-eval.vhdx

That writes windows11-eval-oobe-v2.vhdx. The account's password is random and thrown away; each guest gets its
own password and computer name at create time (sandbox/hyperv_windows.ps1). After the guest sets that password it
writes sbx_ready to the Hyper-V key-value pool, which the host reads to know PowerShell Direct will accept the login.
Bump the -vN suffix when the base changes.
#>
[CmdletBinding(DefaultParameterSetName = 'Iso')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Iso')][string]$Iso,
    [Parameter(Mandatory, ParameterSetName = 'PostOobe')][string]$PostOobeFrom,
    [string]$Output,
    [int]$Index = 0,
    [int]$SizeGB = 64
)
$ErrorActionPreference = 'Stop'
if (-not $Output) {
    $Output = if ($PostOobeFrom) { 'C:\sandbox\base\windows11-eval-oobe-v2.vhdx' } else { 'C:\sandbox\base\windows11-eval.vhdx' }
}
if ($PostOobeFrom) {
    if (-not (Test-Path -LiteralPath $PostOobeFrom)) { throw "Base not found: $PostOobeFrom" }
    if (Test-Path -LiteralPath $Output) { throw "Refusing to overwrite $Output" }
    $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $build = "asb-oobe-build-$id"
    $dir = Join-Path (Split-Path $Output) $build
    $disk = Join-Path $dir 'child.vhdx'
    $logon = 'Aa9!' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $firstBoot = @'
$file = 'C:\Windows\Panther\sbx-init.json'
if (-not (Test-Path -LiteralPath $file)) { exit 0 }
$config = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
Set-LocalUser -Name sandboxadmin -Password (ConvertTo-SecureString $config.password -AsPlainText -Force)
Remove-Item -LiteralPath $file -Force
New-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Virtual Machine\Guest' -Name sbx_ready -Value ([DateTime]::UtcNow.ToString('o')) -PropertyType String -Force | Out-Null
'@
    try {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        New-VM -Name $build -Generation 2 -MemoryStartupBytes 4GB -Path $dir -NoVHD -SwitchName 'Default Switch' | Out-Null
        Set-VM -Name $build -AutomaticCheckpointsEnabled $false
        New-VHD -Path $disk -ParentPath $PostOobeFrom -Differencing | Out-Null
        $mount = Join-Path $dir 'mount'
        New-Item -ItemType Directory -Path $mount | Out-Null
        try {
            Mount-VHD -Path $disk -NoDriveLetter
            $d = Get-VHD -Path $disk | Get-Disk
            $part = Get-Partition -DiskNumber $d.Number | Where-Object Type -eq 'Basic' | Sort-Object Size -Descending | Select-Object -First 1
            Add-PartitionAccessPath -DiskNumber $d.Number -PartitionNumber $part.PartitionNumber -AccessPath ($mount + '\')
            $escaped = [Security.SecurityElement]::Escape($logon)
            $unattend = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
 <settings pass="specialize">
  <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><ComputerName>SBX-BASE</ComputerName></component>
 </settings>
 <settings pass="oobeSystem">
  <component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale></component>
  <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
   <OOBE><HideEULAPage>true</HideEULAPage><HideOEMRegistrationScreen>true</HideOEMRegistrationScreen><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><ProtectYourPC>3</ProtectYourPC><SkipMachineOOBE>true</SkipMachineOOBE><SkipUserOOBE>true</SkipUserOOBE></OOBE>
   <UserAccounts><LocalAccounts><LocalAccount wcm:action="add"><Password><Value>$escaped</Value><PlainText>true</PlainText></Password><Group>Administrators</Group><Name>sandboxadmin</Name></LocalAccount></LocalAccounts></UserAccounts>
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
        Add-VMHardDiskDrive -VMName $build -Path $disk
        Set-VMFirmware -VMName $build -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows -FirstBootDevice (Get-VMHardDiskDrive -VMName $build)
        Set-VMProcessor -VMName $build -Count 2
        Start-VM -Name $build
        $credential = New-Object Management.Automation.PSCredential('sandboxadmin', (ConvertTo-SecureString $logon -AsPlainText -Force))
        $deadline = (Get-Date).AddMinutes(15)
        $session = $null
        while (-not $session) {
            if ((Get-Date) -gt $deadline) { throw 'Build guest never accepted PowerShell Direct' }
            try { $session = New-PSSession -VMName $build -Credential $credential -ErrorAction Stop } catch { Start-Sleep -Seconds 5 }
        }
        try {
            Invoke-Command -Session $session -ArgumentList $firstBoot, $logon -ScriptBlock {
                param($firstBoot, $logon)
                $ErrorActionPreference = 'Stop'
                $script = 'C:\ProgramData\sandbox\firstboot.ps1'
                New-Item -ItemType Directory -Path (Split-Path $script) -Force | Out-Null
                Set-Content -LiteralPath $script -Value $firstBoot -Encoding ASCII
                $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $script"
                $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
                Register-ScheduledTask -TaskName SandboxFirstBoot -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal | Out-Null
                foreach ($file in @('C:\Windows\Panther\unattend.xml', 'C:\Windows\Panther\Unattend\unattend.xml')) {
                    if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
                }
                $random = 'Aa9!' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
                Set-LocalUser -Name sandboxadmin -Password (ConvertTo-SecureString $random -AsPlainText -Force)
                Start-Sleep -Seconds 60
                $state = Get-Content -LiteralPath 'C:\Windows\Setup\State\State.ini' -Raw
                if ($state -notmatch 'IMAGE_STATE_COMPLETE') { throw "Guest did not finish OOBE: $state" }
            }
        } finally {
            Remove-PSSession -Session $session
        }
        Stop-VM -Name $build
        $deadline = (Get-Date).AddMinutes(5)
        while ((Get-VM -Name $build).State -ne 'Off') {
            if ((Get-Date) -gt $deadline) { throw 'Build guest did not shut down' }
            Start-Sleep -Seconds 3
        }
        Convert-VHD -Path (Get-VMHardDiskDrive -VMName $build).Path -DestinationPath $Output -VHDType Dynamic
        Set-ItemProperty -LiteralPath $Output -Name IsReadOnly -Value $true
        Write-Output $Output
    } finally {
        if (Get-VM -Name $build -ErrorAction SilentlyContinue) {
            Stop-VM -Name $build -TurnOff -Force -ErrorAction SilentlyContinue
            Remove-VM -Name $build -Force
        }
        if (Test-Path -LiteralPath $disk) {
            if ((Get-VHD -Path $disk).Attached) { Dismount-VHD -Path $disk }
        }
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
    }
    return
}
if (-not (Test-Path -LiteralPath $Iso)) { throw "ISO not found: $Iso" }
if (Test-Path -LiteralPath $Output) { throw "Refusing to overwrite $Output" }
New-Item -ItemType Directory -Path (Split-Path $Output) -Force | Out-Null

$mounted = $false
$attached = $false
try {
    Mount-DiskImage -ImagePath $Iso | Out-Null
    $mounted = $true
    $drive = (Get-DiskImage -ImagePath $Iso | Get-Volume).DriveLetter
    $wim = "${drive}:\sources\install.wim"
    if (-not (Test-Path $wim)) { $wim = "${drive}:\sources\install.esd" }
    $images = Get-WindowsImage -ImagePath $wim
    if ($Index -eq 0) {
        $pick = $images | Where-Object ImageName -eq 'Windows 11 Enterprise Evaluation' | Select-Object -First 1
        if (-not $pick) { $pick = $images | Where-Object ImageName -like '*Enterprise*' | Select-Object -First 1 }
        if (-not $pick) { throw 'No Enterprise image in the ISO; pass -Index from: ' + ($images.ImageName -join ', ') }
        $Index = $pick.ImageIndex
    }

    New-VHD -Path $Output -SizeBytes ($SizeGB * 1GB) -Dynamic | Out-Null
    Mount-VHD -Path $Output | Out-Null
    $attached = $true
    $disk = Get-VHD -Path $Output | Get-Disk
    Initialize-Disk -Number $disk.Number -PartitionStyle GPT
    $esp = New-Partition -DiskNumber $disk.Number -Size 260MB -GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -AssignDriveLetter
    Format-Volume -Partition $esp -FileSystem FAT32 -NewFileSystemLabel System -Confirm:$false | Out-Null
    $os = New-Partition -DiskNumber $disk.Number -UseMaximumSize -AssignDriveLetter
    Format-Volume -Partition $os -FileSystem NTFS -NewFileSystemLabel Windows -Confirm:$false | Out-Null

    $windowsRoot = "$($os.DriveLetter):\"
    Expand-WindowsImage -ImagePath $wim -Index $Index -ApplyPath $windowsRoot | Out-Null
    & bcdboot "${windowsRoot}Windows" /s "$($esp.DriveLetter):" /f UEFI
    if ($LASTEXITCODE) { throw "bcdboot failed ($LASTEXITCODE)" }

    $state = Get-Content -LiteralPath "${windowsRoot}Windows\Setup\State\State.ini" -Raw
    if ($state -notmatch 'IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE') { throw 'Applied image is not generalized' }
} finally {
    if ($attached) { Dismount-VHD -Path $Output }
    if ($mounted) { Dismount-DiskImage -ImagePath $Iso | Out-Null }
}
Set-ItemProperty -LiteralPath $Output -Name IsReadOnly -Value $true
Write-Output $Output
