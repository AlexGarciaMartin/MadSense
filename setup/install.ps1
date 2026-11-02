# Compatibility entry point. Setup owns installation and driver elevation.
[CmdletBinding()]
param([string]$InstallerPath, [switch]$NoLaunch, [switch]$NoDesktopShortcut)
$ErrorActionPreference = 'Stop'
try {
    if (!$InstallerPath) {
        $packageRoot = Split-Path -Parent $PSScriptRoot
        $candidates = @(Get-ChildItem -LiteralPath $packageRoot -Filter 'MadSense-*-Setup.exe' -File)
        if ($candidates.Count -eq 0 -and (Test-Path -LiteralPath (Join-Path $packageRoot 'dist\installer'))) {
            $candidates = @(Get-ChildItem -LiteralPath (Join-Path $packageRoot 'dist\installer') -Filter 'MadSense-*-Setup.exe' -File)
        }
        if ($candidates.Count -ne 1) { throw 'Descarga el nuevo Setup de MadSense desde la pagina de versiones. El ZIP antiguo ya no es el formato de instalacion.' }
        $InstallerPath = $candidates[0].FullName
    }
    $InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
    $setupArgs = @('/NORESTART')
    if ($NoLaunch) { $setupArgs += '/NOLAUNCH=1' }
    if ($NoDesktopShortcut) { $setupArgs += '/MERGETASKS=!desktopicon' }
    $process = Start-Process -FilePath $InstallerPath -ArgumentList $setupArgs -Wait -PassThru
    exit $process.ExitCode
} catch { Write-Error $_; exit 1 }
