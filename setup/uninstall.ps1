# MadSense — desinstalación. Cierra MadSense, quita su autorización en HidHide, borra la carpeta de
# %LOCALAPPDATA%\Programs\MadSense y los accesos directos. Los controladores USBip e HidHide se dejan
# instalados (otros programas pueden usarlos); se quitan desde Configuración > Aplicaciones.
#   uninstall.ps1 -KeepSettings        conserva preferences.json, controllers.json y los registros
#   uninstall.ps1 -Target <carpeta>    si se instaló en otra carpeta
param([switch]$KeepSettings, [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\MadSense'))
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Exe = Join-Path $Target 'MadSense.exe'
$HidHideCli = 'C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe'

Write-Host 'MadSense — desinstalación' -ForegroundColor White
if (Test-Path $Exe) {
    Write-Host '== Cerrando MadSense y liberando el mando' -ForegroundColor Cyan
    try { & $Exe --stop | Out-Null } catch { }
    Start-Sleep -Seconds 3
    Get-Process MadSense -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if (Test-Path $HidHideCli) { try { & $HidHideCli --app-unreg $Exe | Out-Null } catch { } }
}

Write-Host '== Quitando accesos directos y archivos' -ForegroundColor Cyan
foreach ($lnk in @((Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\MadSense.lnk'), (Join-Path ([Environment]::GetFolderPath('Desktop')) 'MadSense.lnk'))) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
}
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'MadSense' -ErrorAction SilentlyContinue
if (Test-Path $Target) {
    if ($KeepSettings) {
        Get-ChildItem $Target -Recurse -File | Where-Object { $_.Name -notin 'preferences.json', 'controllers.json', 'controllers.json.backup' -and $_.Extension -ne '.log' } | Remove-Item -Force
        Get-ChildItem $Target -Recurse -Directory | Sort-Object FullName -Descending | Where-Object { -not (Get-ChildItem $_.FullName -Recurse -File) } | Remove-Item -Force -Recurse
        Write-Host "   Binarios borrados; ajustes y registros conservados en $Target"
    } else {
        Remove-Item $Target -Recurse -Force
        Write-Host "   Borrado $Target"
    }
}
Write-Host 'MadSense desinstalado. USBip e HidHide siguen instalados; quítalos desde Configuración > Aplicaciones si no los usa nada más.' -ForegroundColor Green
