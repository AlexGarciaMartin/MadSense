# Only included by an explicit InstallerTestBuild. Never shipped in production.
param([string]$Action,[string]$ResultPath,[string]$AppPath,[string]$PayloadDirectory,
    [switch]$AllowPendingReboot,[string]$Scenario='normal')
$ErrorActionPreference='Stop'
$needs=0; $reboot=0; $pending=0; $success=1; $message=''; $code=0
if ($Scenario -eq 'driver-failure') {
    if ($Action -eq 'Inspect') { $needs=1 }
    if ($Action -eq 'Install') { $success=0; $message='Prueba: el controlador ha rechazado la instalacion.'; $code=1 }
}
if ($Scenario -eq 'reboot') {
    if ($Action -eq 'Inspect') { $needs=1 }
    if ($Action -eq 'Install') { $reboot=1; $code=3010 }
    if ($Action -eq 'Register' -and $AllowPendingReboot) { $reboot=1; $pending=1; $code=3010 }
}
if ($Scenario -eq 'register-failure' -and $Action -eq 'Register') {
    $success=0; $message='Prueba: HidHide no ha autorizado MadSense.'; $code=7
}
# Preserve the real helper's process-architecture requirement. Otherwise a
# redirected System32 path can make the TEST build pass while production fails.
if (-not [Environment]::Is64BitProcess) {
    $needs=0; $reboot=0; $pending=0; $success=0; $code=1
    $message='Ejecuta el instalador con PowerShell de 64 bits.'
}
@"
[Result]
Success=$success
NeedsInstall=$needs
RebootRequired=$reboot
RegistrationPending=$pending
Error=$message
DriverSummary=SIMULACION: no se han consultado ni modificado controladores
ProcessBits=$([IntPtr]::Size * 8)
"@ | Set-Content -LiteralPath $ResultPath -Encoding Unicode
exit $code
