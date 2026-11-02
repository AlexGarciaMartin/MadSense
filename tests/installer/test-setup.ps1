# Executes the explicitly labelled TEST installer only. Drivers are simulated,
# but file copying, upgrade, shortcuts and Windows uninstall registration are real.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Installer,
    [ValidateSet('english','spanish','french','german','italian','brazilianportuguese')][string]$Language='english')
$ErrorActionPreference='Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Installer=(Resolve-Path -LiteralPath $Installer).Path
$metadata=Get-Content -LiteralPath ($Installer+'.json') -Raw | ConvertFrom-Json
if (!$metadata.testBuild -or [IO.Path]::GetFileName($Installer) -notlike '*-Setup-TEST.exe') { throw 'Solo se puede probar automaticamente el build TEST.' }
if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -ine $metadata.sha256) { throw 'Hash TEST incorrecto.' }
$qa=Join-Path $root ('.build\installer-qa-'+$Language)
New-Item -ItemType Directory -Force -Path $qa | Out-Null
$results=New-Object 'System.Collections.Generic.List[object]'
$initialProcesses=@(Get-Process MadSense -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{Id=$_.Id;Path=$_.Path} })
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MadSense.Installer.Test_is1'
$shortcut=Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\MadSense Installer Test.lnk'
$appLanguageCodes=@{english='en';spanish='es';french='fr';german='de';italian='it';brazilianportuguese='pt-BR'}
$expectedAppLanguage=$appLanguageCodes[$Language]
function Assert([bool]$Condition,[string]$Message) { if(!$Condition){throw $Message} }
function Invoke-TestSetup([string]$Scenario,[string]$Target,[string]$Label) {
    $log=Join-Path $qa ($Label+'.log')
    $arguments=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOLAUNCH=1','/RESTARTEXITCODE=3010',('/LANG='+$Language),
        ('/TESTSCENARIO='+$Scenario),('/DIR="'+$Target+'"'),('/LOG="'+$log+'"'))
    $p=Start-Process -FilePath $Installer -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if(!$p.WaitForExit(120000)){throw "El instalador TEST no termina: $Label (PID $($p.Id)); no se fuerza el cierre."}
    $p.Refresh()
    Assert ((Get-Content -LiteralPath $log -Raw) -match ('Installer language: '+$Language)) 'Setup did not select the requested language.'
    $results.Add([pscustomobject]@{Case=$Label;ExitCode=$p.ExitCode;Log=[IO.Path]::GetFileName($log)})
    return $p.ExitCode
}
function Invoke-TestUninstall([string]$Target,[string]$Label) {
    $full=[IO.Path]::GetFullPath($Target)
    Assert ($full.StartsWith([IO.Path]::GetFullPath($qa)+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) 'El destino de prueba escapa de .build/installer-qa.'
    $exe=Join-Path $full 'uninstall\unins000.exe'
    Assert (Test-Path -LiteralPath $exe) 'Falta el desinstalador TEST.'
    $log=Join-Path $qa ($Label+'.log')
    $p=Start-Process -FilePath $exe -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+$log+'"')) -WindowStyle Hidden -PassThru
    if(!$p.WaitForExit(60000)){throw "El desinstalador TEST no termina: $Label (PID $($p.Id))."}
    $p.Refresh()
    $results.Add([pscustomobject]@{Case=$Label;ExitCode=$p.ExitCode;Log=[IO.Path]::GetFileName($log)})
    return $p.ExitCode
}
try {
    # Exercise both real PowerShell hosts so the fake helper cannot silently mask
    # System32 redirection by a 32-bit Setup process. No drivers are accessed.
    Assert ([Environment]::Is64BitOperatingSystem) 'Las pruebas requieren Windows de 64 bits.'
    $nativeSystem=if ([Environment]::Is64BitProcess) { 'System32' } else { 'Sysnative' }
    foreach ($bits in 32,64) {
        $system=if ($bits -eq 32) { 'SysWOW64' } else { $nativeSystem }
        $hostExe=Join-Path $env:WINDIR ($system+'\WindowsPowerShell\v1.0\powershell.exe')
        $resultFile=Join-Path $qa ('helper-architecture-'+$bits+'.ini')
        if (Test-Path -LiteralPath $resultFile) { Remove-Item -LiteralPath $resultFile -Force }
        $fake=Join-Path $PSScriptRoot 'FakeDriverSetup.ps1'
        $p=Start-Process -FilePath $hostExe -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
            '-File',('"'+$fake+'"'),'-Action','Inspect','-ResultPath',('"'+$resultFile+'"')) -WindowStyle Hidden -PassThru
        if (!$p.WaitForExit(15000)) { throw "La comprobacion de PowerShell $bits no termina (PID $($p.Id))." }
        $p.Refresh()
        $expectedExit=if ($bits -eq 64) { 0 } else { 1 }
        $expectedSuccess=if ($bits -eq 64) { 1 } else { 0 }
        $resultText=Get-Content -LiteralPath $resultFile -Raw -Encoding Unicode
        Assert ($p.ExitCode -eq $expectedExit) "Codigo incorrecto del helper bajo PowerShell $bits."
        Assert ($resultText -match "(?m)^Success=$expectedSuccess`r?$" -and $resultText -match "(?m)^ProcessBits=$bits`r?$") "Resultado INI incorrecto bajo PowerShell $bits."
        if ($bits -eq 32) { Assert ($resultText -match '(?m)^Error=.*PowerShell de 64 bits\.') 'Falta diagnostico del host de 32 bits.' }
        $results.Add([pscustomobject]@{Case=('00-helper-'+$bits+'-bits');ExitCode=$p.ExitCode;Log=[IO.Path]::GetFileName($resultFile)})
    }
    $target=Join-Path $qa 'normal install\MadSense-Installer-Test'
    Assert ((Invoke-TestSetup normal $target '01-install') -eq 0) 'Falla instalacion normal.'
    Assert (Test-Path -LiteralPath (Join-Path $target 'MadSense.exe')) 'No copio la app.'
    Assert (Test-Path -LiteralPath $key) 'No registro desinstalacion en HKCU.'
    Assert (Test-Path -LiteralPath $shortcut) 'No creo acceso en Inicio del usuario.'
    $languageSeed=Join-Path $target 'installer-language.txt'
    Assert (Test-Path -LiteralPath $languageSeed) 'No instalo la seleccion de idioma de la app.'
    Assert ([IO.File]::ReadAllText($languageSeed).Trim() -ceq $expectedAppLanguage) 'Idioma inicial de app distinto al seleccionado en Setup.'
    $preferences=Join-Path $target 'preferences.json'
    $sentinel='{"mode":"Xbox","speaker":"Bajo","language":"fr","installer-test":true}'
    [IO.File]::WriteAllText($preferences,$sentinel)
    Assert ((Invoke-TestSetup normal $target '02-upgrade') -eq 0) 'Falla actualizacion.'
    Assert ([IO.File]::ReadAllText($preferences) -ceq $sentinel) 'Actualizacion sobrescribe ajustes.'
    Assert ([IO.File]::ReadAllText($languageSeed).Trim() -ceq $expectedAppLanguage) 'Actualizacion perdio el idioma seleccionado en Setup.'
    # Invalid JSON fails before any HidHide API call, so this is safe on the live PC.
    $journal=Join-Path $target 'hidden-controller.json'
    [IO.File]::WriteAllText($journal,'invalid-test-journal')
    Assert ((Invoke-TestSetup normal $target '02b-upgrade-recovery-failure') -ne 0) 'Actualizacion no bloqueo una recuperacion fallida.'
    Assert ((Get-Content -LiteralPath (Join-Path $qa '02b-upgrade-recovery-failure.log') -Raw) -match 'Maintenance failed \([23]\):') 'Falta diagnostico de mantenimiento en la actualizacion.'
    Assert ([IO.File]::ReadAllText($preferences) -ceq $sentinel) 'Actualizacion bloqueada altero ajustes.'
    Assert ((Invoke-TestUninstall $target '03-uninstall-recovery-failure') -ne 0) 'Desinstalacion no bloqueo una recuperacion fallida.'
    Assert (Test-Path -LiteralPath $journal) 'Se perdio el diario de recuperacion.'
    Assert (Test-Path -LiteralPath (Join-Path $target 'MadSense.exe')) 'Se borro la app pese a recuperacion fallida.'
    Remove-Item -LiteralPath $journal -Force
    Assert ((Invoke-TestUninstall $target '04-uninstall') -eq 0) 'Falla desinstalacion normal.'
    Assert (!(Test-Path -LiteralPath (Join-Path $target 'MadSense.exe'))) 'No elimino binarios.'
    Assert (!(Test-Path -LiteralPath $languageSeed)) 'Desinstalacion conserva el seed de idioma instalado.'
    Assert ([IO.File]::ReadAllText($preferences) -ceq $sentinel) 'Desinstalacion borro ajustes.'
    Assert (!(Test-Path -LiteralPath $key)) 'Queda registro de desinstalacion.'
    Assert (!(Test-Path -LiteralPath $shortcut)) 'Queda acceso de Inicio.'

    $target=Join-Path $qa 'driver failure\MadSense-Installer-Test'
    Assert ((Invoke-TestSetup driver-failure $target '05-driver-failure') -ne 0) 'Fallo driver anunciado como exito.'
    Assert (!(Test-Path -LiteralPath (Join-Path $target 'MadSense.exe'))) 'Copio app tras fallo de prerrequisitos.'

    $target=Join-Path $qa 'reboot\MadSense-Installer-Test'
    Assert ((Invoke-TestSetup reboot $target '06-reboot') -eq 3010) 'No propaga reinicio requerido.'
    Assert ((Invoke-TestUninstall $target '07-uninstall-reboot') -eq 0) 'Falla limpieza caso reinicio.'

    $target=Join-Path $qa 'registration failure\MadSense-Installer-Test'
    Assert ((Invoke-TestSetup register-failure $target '08-registration-failure') -ne 0) 'Fallo de registro anunciado como exito.'
    Assert ((Get-Content -LiteralPath (Join-Path $qa '08-registration-failure.log') -Raw) -match 'INSTALLATION INCOMPLETE') 'Falta diagnostico de instalacion incompleta.'
    Assert (Test-Path -LiteralPath (Join-Path $target 'uninstall\unins000.exe')) 'Falta salida de desinstalacion tras fallo parcial.'
    Assert ((Invoke-TestUninstall $target '09-uninstall-registration-failure') -eq 0) 'Falla limpieza registro fallido.'
    foreach($process in $initialProcesses) {
        $alive=Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        Assert ($null -ne $alive -and $alive.Path -eq $process.Path) 'Se altero una instancia preexistente de MadSense.'
    }
    $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qa 'results.json') -Encoding UTF8
    Write-Host 'PASS: instalacion, actualizacion, recuperacion fallida, desinstalacion, fallo driver, reinicio y fallo de registro. Desarrollo intacto.'
} catch {
    $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qa 'results.json') -Encoding UTF8
    throw
}
