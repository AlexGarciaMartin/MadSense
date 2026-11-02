# Forward to the per-user Windows uninstaller; never delete directories here.
param([string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\MadSense'))
$ErrorActionPreference = 'Stop'
try {
    $uninstaller = Join-Path ([IO.Path]::GetFullPath($Target)) 'uninstall\unins000.exe'
    if (!(Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw 'No encuentro el desinstalador de Windows. Instala la nueva version sobre esa carpeta y usa Configuracion > Aplicaciones > MadSense.'
    }
    $process = Start-Process -FilePath $uninstaller -Wait -PassThru
    exit $process.ExitCode
} catch { Write-Error $_; exit 1 }
