<#
Builds a generalized Windows 11 Enterprise Evaluation base disk from the ISO you download from Microsoft.
Run in an elevated PowerShell on the Hyper-V host.

  .\make-windows-base.ps1 -Iso C:\sandbox\media\Win11_Enterprise_Eval.iso

The output is a read-only dynamic VHDX that the sandbox uses as the parent of every Windows guest.
#>
param(
    [Parameter(Mandatory)][string]$Iso,
    [string]$Output = 'C:\sandbox\base\windows11-eval.vhdx',
    [int]$Index = 0,
    [int]$SizeGB = 64
)
$ErrorActionPreference = 'Stop'
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
