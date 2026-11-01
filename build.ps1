$ErrorActionPreference = 'Stop'
$sdk = Get-Command dotnet -ErrorAction SilentlyContinue
$sdkPath = if ($sdk) { $sdk.Source } else { Join-Path $PSScriptRoot '..\..\work\dotnet\dotnet.exe' }
if (!(Test-Path -LiteralPath $sdkPath)) { throw 'Instala .NET SDK 10 para compilar.' }
& $sdkPath publish (Join-Path $PSScriptRoot 'src\MadSense.csproj') -c Release -r win-x64 --self-contained true -o (Join-Path $PSScriptRoot 'app') --nologo
if ($LASTEXITCODE -ne 0) { throw 'La compilación ha fallado.' }

