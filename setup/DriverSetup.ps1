# The only elevated part of MadSense setup. The caller retains its own user profile.
# Inspect only reads the platform/drivers; no driver or HidHide configuration changes.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Inspect','Install','Register','Unregister')][string]$Action,
    [Parameter(Mandatory=$true)][string]$ResultPath,
    [string]$AppPath,
    [string]$PayloadDirectory,
    [switch]$AllowPendingReboot
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'DriverSetup.Core.psm1') -Force
$result = Invoke-MadDriverSetup -Action $Action -AppPath $AppPath -PayloadDirectory $PayloadDirectory -AllowPendingReboot:$AllowPendingReboot
try { Write-MadDriverResult -Result $result -Path $ResultPath }
catch { Write-Error $_; exit 1 }
if (-not $result.Success) { Write-Host $result.Error; exit 1 }
if ($result.RebootRequired) { exit 3010 }
exit 0
