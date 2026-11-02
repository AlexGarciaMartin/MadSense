# Build in this checkout without overwriting a running development copy.
[CmdletBinding()]
param([string]$IsccPath, [string]$DotnetPath, [switch]$InstallerTestBuild,
      [string]$PublishDirectory, [string]$DependencyDirectory)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root 'tests\installer\test-languages.ps1')
$buildRoot = Join-Path $root '.build'
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

function Get-VerifiedDownload([string]$Uri, [string]$File, [string]$Hash, [switch]$Signed) {
    if (!(Test-Path -LiteralPath $File)) {
        $partial = $File + '.partial'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Hash) {
            Remove-Item -LiteralPath $partial -Force
            throw "Hash incorrecto: $Uri"
        }
        Move-Item -LiteralPath $partial -Destination $File
    }
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Hash) { throw "Cache modificada: $File" }
    if ($Signed -and (Get-AuthenticodeSignature -LiteralPath $File).Status -ne 'Valid') { throw "Firma no valida: $File" }
}

if (!$IsccPath) {
    $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($compiler) { $IsccPath = $compiler.Source }
    else {
        $toolsDir = Join-Path $root '.tools'
        New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
        $package = Join-Path $toolsDir 'Tools.InnoSetup.7.1.0.zip'
        Get-VerifiedDownload 'https://api.nuget.org/v3-flatcontainer/tools.innosetup/7.1.0/tools.innosetup.7.1.0.nupkg' $package 'AAD15C662593C6F5656457A5C001F4591BABD72820CACF63F9706D4AF5D93B75'
        $compilerDir = Join-Path $toolsDir 'InnoSetup-7.1.0'
        if (!(Test-Path -LiteralPath (Join-Path $compilerDir 'tools\ISCC.exe'))) { Expand-Archive -LiteralPath $package -DestinationPath $compilerDir }
        $IsccPath = Join-Path $compilerDir 'tools\ISCC.exe'
    }
}
if (!(Test-Path -LiteralPath $IsccPath -PathType Leaf)) { throw 'No encuentro ISCC.exe.' }
if (!$DotnetPath) {
    $sdk = Get-Command dotnet -ErrorAction SilentlyContinue
    $DotnetPath = if ($sdk) { $sdk.Source } else { Join-Path $root '..\..\work\dotnet\dotnet.exe' }
}
if (!$PublishDirectory) {
    if (!(Test-Path -LiteralPath $DotnetPath)) { throw 'Instala .NET SDK 10 o indica -DotnetPath.' }
    $PublishDirectory = Join-Path $buildRoot ('publish-' + [Guid]::NewGuid().ToString('N'))
    & $DotnetPath publish (Join-Path $root 'src\MadSense.csproj') -c Release -r win-x64 --self-contained true -o $PublishDirectory --nologo
    if ($LASTEXITCODE -ne 0) { throw 'La publicacion ha fallado; no se empaquetaran binarios anteriores.' }
}
$PublishDirectory = (Resolve-Path -LiteralPath $PublishDirectory).Path
foreach ($required in 'MadSense.exe','MadSense.dll','MadSense.deps.json','MadSense.runtimeconfig.json','coreclr.dll','hostfxr.dll','hostpolicy.dll') {
    if (!(Test-Path -LiteralPath (Join-Path $PublishDirectory $required))) { throw "Publicacion incompleta: falta $required" }
}
$protocolPath = Join-Path $PublishDirectory 'MadSense.installer.json'
if (!(Test-Path -LiteralPath $protocolPath)) { throw 'El payload no contiene el protocolo de mantenimiento del instalador. Vuelve a publicar esta rama.' }
$protocol = Get-Content -LiteralPath $protocolPath -Raw | ConvertFrom-Json
$assemblyBytes = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes((Join-Path $PublishDirectory 'MadSense.dll')))
if ($protocol.protocol -ne 1 -or !$assemblyBytes.Contains('MadSense.InstallerProtocol')) {
    throw 'El payload no soporta el protocolo de mantenimiento 1; no se ejecutara un EXE antiguo.'
}
# Exclude all user state and debug artifacts, including from an explicit publish directory.
$stage = Join-Path $buildRoot ('stage-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
Get-ChildItem -LiteralPath $PublishDirectory -Recurse -File | Where-Object {
    $_.Extension -notin '.pdb','.log','.previous','.dmp','.tmp' -and
    $_.Name -notmatch '^(controllers|preferences|diagnostic|hidden-controller)\.json'
} | ForEach-Object {
    $relative = $_.FullName.Substring($PublishDirectory.Length).TrimStart('\')
    $destination = Join-Path $stage $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $destination
}
$manifest = Get-Content -LiteralPath (Join-Path $root 'setup\dependencies.json') -Raw | ConvertFrom-Json
if (!$DependencyDirectory) { $DependencyDirectory = Join-Path $buildRoot 'dependencies' }
New-Item -ItemType Directory -Force -Path $DependencyDirectory | Out-Null
$DependencyDirectory = (Resolve-Path -LiteralPath $DependencyDirectory).Path
$dependencyStage = Join-Path $buildRoot ('drivers-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $dependencyStage | Out-Null
foreach ($driver in $manifest.drivers) {
    Get-VerifiedDownload $driver.url (Join-Path $DependencyDirectory $driver.file) $driver.sha256 -Signed
    Copy-Item -LiteralPath (Join-Path $DependencyDirectory $driver.file) -Destination (Join-Path $dependencyStage $driver.file)
}
$version = ([xml](Get-Content -LiteralPath (Join-Path $root 'src\MadSense.csproj'))).Project.PropertyGroup.Version | Where-Object { $_ } | Select-Object -First 1
$outputDir = Join-Path $root 'dist\installer'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$compilerArgs = @('/Qp', ('/DAppVersion=' + $version), ('/DPublishDirectory=' + $stage),
    ('/DDependencyDirectory=' + $dependencyStage), ('/DOutputDirectory=' + $outputDir))
if ($InstallerTestBuild) { $compilerArgs += '/DInstallerTestBuild' }
$compilerArgs += (Join-Path $root 'setup\MadSense.iss')
& $IsccPath @compilerArgs
if ($LASTEXITCODE -ne 0) { throw 'El compilador del instalador ha fallado.' }
$suffix = if ($InstallerTestBuild) { '-TEST' } else { '' }
$installer = Join-Path $outputDir "MadSense-$version-Setup$suffix.exe"
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($installer))" | Set-Content -LiteralPath ($installer + '.sha256') -Encoding ASCII
$compilerVersion = (Get-Item -LiteralPath $IsccPath).VersionInfo.FileVersion
if (!$compilerVersion -or $compilerVersion -eq '0.0.0.0') {
    # The portable Inno 7 compiler has a placeholder Win32 file version.
    $engineVersion = (& $IsccPath --version 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $engineVersion -match '^\d+\.\d+\.\d+(\.\d+)?$') { $compilerVersion = $engineVersion }
}
$evidence = [ordered]@{
    version = $version; installer = [IO.Path]::GetFileName($installer); sha256 = $hash;
    bytes = (Get-Item -LiteralPath $installer).Length; testBuild = [bool]$InstallerTestBuild;
    sourceCommit = (& git -C $root rev-parse HEAD 2>$null); sourceDirty = [bool](& git -C $root status --porcelain 2>$null);
    compilerVersion = $compilerVersion;
    compilerSha256 = (Get-FileHash -LiteralPath $IsccPath -Algorithm SHA256).Hash.ToLowerInvariant();
    payloadFiles = (Get-ChildItem -LiteralPath $stage -Recurse -File).Count;
    installerLanguages = @('english','spanish','french','german','italian','brazilianportuguese');
    appLanguages = @('en','es','fr','de','it','pt-BR');
    dependencies = @($manifest.drivers | Select-Object id,version,file,sha256);
}
$evidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath ($installer + '.json') -Encoding UTF8
Write-Host "Instalador: $installer"
Write-Host "SHA-256: $hash"
Write-Host "Payload comprobado: $stage"
