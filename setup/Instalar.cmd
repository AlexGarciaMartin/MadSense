@echo off
rem MadSense: instala los controladores que faltan, copia la app y crea los accesos directos.
rem Pide permisos de administrador (los controladores los necesitan). Detalle en setup\install.ps1.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\install.ps1"
if errorlevel 1 (
  echo.
  echo La instalacion ha fallado. Lee el mensaje de arriba; si hace falta, instala USBip e HidHide a mano y vuelve a ejecutar este archivo.
  pause
)
