@echo off
rem Abre el desinstalador registrado. Tambien esta en Configuracion / Aplicaciones.
set "MADSENSE_SCRIPT=%~dp0uninstall.ps1"
if not exist "%MADSENSE_SCRIPT%" set "MADSENSE_SCRIPT=%~dp0setup\uninstall.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MADSENSE_SCRIPT%" %*
pause
