@echo off
rem MadSense: cierra la app, quita su autorizacion en HidHide, borra la carpeta y los accesos directos.
rem Los controladores USBip e HidHide se quitan desde Configuracion ^> Aplicaciones.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\uninstall.ps1"
pause
