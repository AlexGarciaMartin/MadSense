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
@"
[Result]
Success=$success
NeedsInstall=$needs
RebootRequired=$reboot
RegistrationPending=$pending
Error=$message
DriverSummary=SIMULACION: no se han consultado ni modificado controladores
"@ | Set-Content -LiteralPath $ResultPath -Encoding Unicode
exit $code
