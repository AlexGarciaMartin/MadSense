# Opens the games screen through the UI preview harness (no controller bridge), waits for the
# library scan and the first PCGamingWiki answers, captures it and closes it.
#   tools\games-preview.ps1 -Out evidence\ui-games.png [-Language es] [-WaitSeconds 30]
param([string]$Out, [string]$Language = 'es', [int]$WaitSeconds = 30, [string]$DotnetRoot = 'C:\Users\Alex\Documents\Codex\2026-10-31\que-tal-vamos-a-tirarnos-d\work\dotnet')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root 'tests\MadSense.UiPreview\bin\Release\net10.0-windows\MadSense.UiPreview.exe'
$data = Join-Path $env:TEMP 'MadSense-GamesPreview'
$env:DOTNET_ROOT = $DotnetRoot
$preview = Start-Process -FilePath $exe -ArgumentList @('--data-dir', "`"$data`"", '--language', $Language, '--view', 'games') -PassThru
Start-Sleep -Seconds $WaitSeconds
& (Join-Path $PSScriptRoot 'ui-check.ps1') -Out $Out -ProcessName 'MadSense.UiPreview'
Start-Sleep -Seconds 1
if (-not $preview.HasExited) { $preview.CloseMainWindow() | Out-Null; Start-Sleep -Seconds 2 }
if (-not $preview.HasExited) { $preview | Stop-Process -Force }
