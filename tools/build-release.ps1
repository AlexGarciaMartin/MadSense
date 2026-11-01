# Empaqueta la versión instalable: publica la app (build.ps1), copia solo lo necesario a
# dist\MadSense-<versión>-win-x64\ y lo comprime en un zip con su SHA-256.
# Requisitos: MadSense cerrada (build.ps1 publica sobre app\) y .NET SDK 10.
#   tools\build-release.ps1              publica y empaqueta
#   tools\build-release.ps1 -SkipPublish empaqueta lo que ya hay en app\
param([switch]$SkipPublish)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$csproj = Join-Path $root 'src\MadSense.csproj'
$version = ([xml](Get-Content $csproj)).Project.PropertyGroup.Version | Where-Object { $_ } | Select-Object -First 1
$version = ([string]$version).Trim() -replace '\.0$', ''
if (-not $version) { throw 'No encuentro <Version> en src\MadSense.csproj.' }

if (-not $SkipPublish) {
    if (Get-Process MadSense -ErrorAction SilentlyContinue) { throw 'Cierra MadSense (o ejecuta app\MadSense.exe --stop) antes de publicar.' }
    & (Join-Path $root 'build.ps1')
}

$name = "MadSense-$version-win-x64"
$dist = Join-Path $root 'dist'
$stage = Join-Path $dist $name
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $stage 'app') | Out-Null

# app\: binarios y runtime, sin registros, ajustes de este PC ni símbolos de depuración.
$excludeNames = @('controllers.json', 'controllers.json.backup', 'preferences.json', 'diagnostic.json', 'hidden-controller.json')
Get-ChildItem (Join-Path $root 'app') -Recurse -File | Where-Object {
    $_.Extension -notin '.pdb', '.log', '.previous' -and $_.Name -notin $excludeNames
} | ForEach-Object {
    $relative = $_.FullName.Substring((Join-Path $root 'app').Length).TrimStart('\')
    $dest = Join-Path $stage "app\$relative"
    New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
    Copy-Item $_.FullName $dest
}

# Instalador y documentación.
Copy-Item (Join-Path $root 'setup') (Join-Path $stage 'setup') -Recurse
Move-Item (Join-Path $stage 'setup\Instalar.cmd') (Join-Path $stage 'Instalar.cmd')
Move-Item (Join-Path $stage 'setup\Desinstalar.cmd') (Join-Path $stage 'Desinstalar.cmd')
foreach ($doc in 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.txt', 'dependency-notices.txt') { Copy-Item (Join-Path $root $doc) $stage }
@"
MADSENSE $version — INSTALACIÓN

1. Descomprime esta carpeta entera donde quieras (por ejemplo en Descargas).
2. Ejecuta Instalar.cmd y acepta el aviso de administrador.
   Instala USBip e HidHide si faltan (de sus proyectos oficiales, con la huella
   comprobada), copia MadSense a tu perfil y crea los accesos directos.
3. Si ha instalado HidHide, reinicia Windows.
4. Empareja el DualSense por Bluetooth en Windows (Configuración > Bluetooth),
   abre MadSense y pulsa PS en el mando. Cuando diga "Listo para jugar", abre el juego.

Steam: para los juegos con soporte DualSense nativo, desactiva Steam Input en sus
propiedades. Cerrar la ventana deja MadSense en la bandeja.
Desinstalar: Desinstalar.cmd. Más detalle en README.md.
"@ | Set-Content (Join-Path $stage 'LEEME.txt') -Encoding UTF8

$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
"$hash  $name.zip" | Set-Content "$zip.sha256" -Encoding ASCII
"{0}`n  {1:N1} MB, SHA-256 {2}" -f $zip, ((Get-Item $zip).Length / 1MB), $hash
