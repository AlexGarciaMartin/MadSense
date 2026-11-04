# Hardware check of the mod receiver: stops the stable app, runs this branch's build in --soak mode
# (which logs the pad's TX counters every second), sends the demo mod packets, prints what the log
# recorded, stops the soak and restarts the stable app. Needs the pad connected.
#   tools\mod-soak-check.ps1 -StableExe "...\MadSense\app\MadSense.exe"
param(
    [string]$StableExe = 'C:\Users\Alex\Documents\Codex\2026-10-31\que-tal-vamos-a-tirarnos-d\outputs\MadSense\app\MadSense.exe',
    [string]$DotnetRoot = 'C:\Users\Alex\Documents\Codex\2026-10-31\que-tal-vamos-a-tirarnos-d\work\dotnet'
)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root 'src\bin\Release\net10.0-windows'
$exe = Join-Path $bin 'MadSense.exe'
$log = Join-Path $bin 'madsense.log'
if (Test-Path $StableExe) { & $StableExe --stop; Start-Sleep -Seconds 3 }
Get-Process MadSense -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item $log -ErrorAction SilentlyContinue
$env:DOTNET_ROOT = $DotnetRoot
$soak = Start-Process -FilePath $exe -ArgumentList '--soak' -WorkingDirectory $bin -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 10
"== log after connect"; Get-Content $log -Tail 2 -ErrorAction SilentlyContinue
"== mod packets"
& (Join-Path $PSScriptRoot 'mod-client.ps1') -HoldMs 1500 | Where-Object { $_ -match '^(==|<-)' }
Start-Sleep -Seconds 2
"== log lines about mods and TX counters"
Get-Content $log -ErrorAction SilentlyContinue | Where-Object { $_ -match 'mod|TX|Mod' } | Select-Object -Last 6
& $exe --stop
Start-Sleep -Seconds 3
if (-not $soak.HasExited) { $soak | Stop-Process -Force }
if (Test-Path $StableExe) { Start-Process -FilePath $StableExe -WorkingDirectory (Split-Path -Parent $StableExe) | Out-Null; Start-Sleep -Seconds 6; "== stable app"; Get-Content (Join-Path (Split-Path -Parent $StableExe) 'madsense.log') -Tail 1 }
