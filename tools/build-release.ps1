# One packaging path: the Windows installer replaces the old script ZIP.
[CmdletBinding()]
param([string]$IsccPath, [string]$DotnetPath)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build-installer.ps1') @PSBoundParameters
