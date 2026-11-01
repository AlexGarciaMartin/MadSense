# MadSense — instalación en un PC nuevo.
#
# Qué hace, en orden:
#   1. Comprueba Windows 10/11 x64 y se eleva a administrador (los controladores lo necesitan).
#   2. USBip (usbip-win2 0.9.8.1): si falta, lo descarga de su GitHub oficial, comprueba el SHA-256
#      y lo instala en silencio. Es el controlador que presenta el DualSense virtual por USB.
#   3. HidHide: si falta, lo instala con winget (o lo descarga de su GitHub oficial con SHA-256
#      comprobado). Es el controlador que oculta el mando físico al juego. Suele pedir reinicio.
#   4. Copia MadSense a %LOCALAPPDATA%\Programs\MadSense (conserva ajustes y registros previos).
#   5. Autoriza MadSense.exe en HidHide y crea accesos directos en Inicio y Escritorio.
# No toca Steam, no instala .NET (MadSense lo lleva dentro) y no modifica otros programas.
#
#   Instalar.cmd                      instalación normal
#   install.ps1 -NoDesktopShortcut    sin acceso directo en el escritorio
#   install.ps1 -NoLaunch             no abrir MadSense al terminar
#   install.ps1 -SkipDrivers          no tocar USBip ni HidHide (ya instalados a mano); no pide administrador
#   install.ps1 -Target <carpeta>     instalar en otra carpeta (por defecto %LOCALAPPDATA%\Programs\MadSense)
param([switch]$NoDesktopShortcut, [switch]$NoLaunch, [switch]$SkipDrivers, [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\MadSense'))
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Version = '0.4'
$Source = Join-Path (Split-Path -Parent $PSScriptRoot) 'app'
$HidHideCli = 'C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe'
$UsbipExe = 'C:\Program Files\USBip\usbip.exe'
# Fuentes oficiales y huellas SHA-256 de los instaladores exactos que se probaron con MadSense 0.4.
# (La etiqueta de usbip-win2 lleva punto tras la v: v.0.9.8.1.)
$Usbip = @{ Url = 'https://github.com/vadimgrn/usbip-win2/releases/download/v.0.9.8.1/USBip-0.9.8.1-x64.exe'; Sha256 = '38CAD6D4432B52D5BB9409D9AD03B72FDFFC4ADA4CD3A48FBECA1A2752A8518A'; File = 'USBip-0.9.8.1-x64.exe' }
$HidHide = @{ Url = 'https://github.com/nefarius/HidHide/releases/download/v1.5.230.0/HidHide_1.5.230_x64.exe'; Sha256 = 'F4BBBCB82E6258641B887C74BC81C4C5F66E4AA811808DFC304347687B7605F6'; File = 'HidHide_1.5.230_x64.exe'; WingetId = 'Nefarius.HidHide' }

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   $text" -ForegroundColor Yellow }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Download($spec) {
    $path = Join-Path $env:TEMP $spec.File
    Write-Host "   Descargando $($spec.Url)"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $spec.Url -OutFile $path -UseBasicParsing
    $hash = (Get-FileHash $path -Algorithm SHA256).Hash
    if ($hash -ne $spec.Sha256) { Remove-Item $path -Force; throw "La huella SHA-256 de $($spec.File) no coincide con la esperada. Descarga cancelada por seguridad." }
    Ok "Descargado y verificado: $($spec.File)"
    return $path
}

function Test-RebootPending {
    (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
    (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
    ($null -ne (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue))
}

function New-Shortcut($path, $targetExe, $description) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $targetExe
    $shortcut.WorkingDirectory = Split-Path -Parent $targetExe
    $shortcut.IconLocation = "$targetExe,0"
    $shortcut.Description = $description
    $shortcut.Save()
}

# ── 0. Elevación y comprobaciones ───────────────────────────────────────────────────────────
if (-not $SkipDrivers -and -not (Test-Admin)) {
    Write-Host "MadSense necesita permisos de administrador para instalar los controladores. Pidiendo elevación…"
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$PSCommandPath`"", '-Target', "`"$Target`"")
    if ($NoDesktopShortcut) { $args += '-NoDesktopShortcut' }
    if ($NoLaunch) { $args += '-NoLaunch' }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $args
    exit 0
}

Write-Host "MadSense $Version — instalación" -ForegroundColor White
if (-not [Environment]::Is64BitOperatingSystem) { throw 'MadSense necesita Windows de 64 bits.' }
if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'MadSense necesita Windows 10 o 11.' }
if (-not (Test-Path (Join-Path $Source 'MadSense.exe'))) { throw "No encuentro la carpeta app junto al instalador ($Source). Descomprime el paquete entero antes de instalar." }
$rebootNeeded = $false

# ── 1. USBip (usbip-win2) ──────────────────────────────────────────────────────────────────
Step 'USBip (DualSense virtual por USB)'
if ($SkipDrivers) {
    $state = if (Test-Path $UsbipExe) { 'está instalado.' } else { 'NO está instalado: MadSense no podrá crear el mando virtual.' }
    Warn "Omitido por -SkipDrivers; USBip $state"
} elseif (Test-Path $UsbipExe) {
    Ok "Ya instalado: $UsbipExe"
} else {
    $installer = Get-Download $Usbip
    $p = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "El instalador de USBip devolvió el código $($p.ExitCode)." }
    if (-not (Test-Path $UsbipExe)) { throw 'USBip no quedó instalado donde se esperaba. Instálalo a mano desde https://github.com/vadimgrn/usbip-win2/releases y vuelve a ejecutar este instalador.' }
    Ok 'USBip 0.9.8.1 instalado.'
}

# ── 2. HidHide ────────────────────────────────────────────────────────────────────────────
Step 'HidHide (oculta el mando físico al juego)'
if ($SkipDrivers) {
    $state = if (Test-Path $HidHideCli) { 'está instalado.' } else { 'NO está instalado: el juego vería el mando dos veces.' }
    Warn "Omitido por -SkipDrivers; HidHide $state"
} elseif (Test-Path $HidHideCli) {
    Ok "Ya instalado: $HidHideCli"
} else {
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host '   Instalando con winget…'
        & winget install --id $HidHide.WingetId --exact --silent --accept-package-agreements --accept-source-agreements | Out-Host
        $installed = Test-Path $HidHideCli
    }
    if (-not $installed) {
        $installer = Get-Download $HidHide
        $p = Start-Process -FilePath $installer -ArgumentList '/exenoui', '/qn', '/norestart' -Wait -PassThru
        if ($p.ExitCode -notin 0, 3010) { throw "El instalador de HidHide devolvió el código $($p.ExitCode)." }
        $installed = Test-Path $HidHideCli
    }
    if (-not $installed) { throw 'HidHide no quedó instalado donde se esperaba. Instálalo a mano desde https://github.com/nefarius/HidHide/releases y vuelve a ejecutar este instalador.' }
    $rebootNeeded = $true
    Ok 'HidHide instalado. Windows tendrá que reiniciarse antes de usar MadSense.'
}

# ── 3. Copiar MadSense ────────────────────────────────────────────────────────────────────
Step "Copiar MadSense a $Target"
$existing = Join-Path $Target 'MadSense.exe'
if (Test-Path $existing) {
    Write-Host '   Cerrando la versión anterior…'
    try { & $existing --stop | Out-Null } catch { }
    Start-Sleep -Seconds 3
    Get-Process MadSense -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force $Target | Out-Null
# Se sobrescriben los binarios; preferences.json, controllers.json y los registros del usuario se conservan.
Get-ChildItem $Source -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($Source.Length).TrimStart('\')
    $dest = Join-Path $Target $relative
    New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
    Copy-Item $_.FullName $dest -Force
}
Ok "Copiados $((Get-ChildItem $Source -Recurse -File).Count) archivos."

# ── 4. HidHide: permitir que MadSense vea el mando ────────────────────────────────────────
Step 'Autorizar MadSense en HidHide'
if (Test-Path $HidHideCli) {
    & $HidHideCli --app-reg $existing | Out-Null
    Ok 'MadSense.exe está en la lista de aplicaciones permitidas de HidHide.'
} else {
    Warn 'HidHide no está disponible todavía; MadSense se autorizará solo la primera vez que arranque.'
}

# ── 5. Accesos directos ───────────────────────────────────────────────────────────────────
Step 'Accesos directos'
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\MadSense.lnk'
New-Shortcut $startMenu $existing 'MadSense: DualSense por Bluetooth con hápticos, gatillos y altavoz'
Ok "Menú Inicio: $startMenu"
if (-not $NoDesktopShortcut) {
    $desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MadSense.lnk'
    New-Shortcut $desktop $existing 'MadSense: DualSense por Bluetooth con hápticos, gatillos y altavoz'
    Ok "Escritorio: $desktop"
}

# ── Resumen ───────────────────────────────────────────────────────────────────────────────
Write-Host ""
if ($rebootNeeded -or (Test-RebootPending)) {
    Write-Host 'Instalación terminada. Reinicia Windows para que HidHide quede activo y después abre MadSense desde el menú Inicio.' -ForegroundColor Yellow
} else {
    Write-Host 'Instalación terminada.' -ForegroundColor Green
    Write-Host 'Empareja el DualSense por Bluetooth en Windows si no lo está, abre MadSense y pulsa PS en el mando.'
    if (-not $NoLaunch) { Start-Process -FilePath $existing -WorkingDirectory $Target }
}
Write-Host 'Para desinstalar: Desinstalar.cmd (los controladores USBip e HidHide se quitan desde Configuración > Aplicaciones).'
