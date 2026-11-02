@echo off
rem Compatibilidad: abre el nuevo asistente Setup de Windows.
set "MADSENSE_SCRIPT=%~dp0install.ps1"
if not exist "%MADSENSE_SCRIPT%" set "MADSENSE_SCRIPT=%~dp0setup\install.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MADSENSE_SCRIPT%" %*
if errorlevel 1 (
  echo.
  echo No se ha completado la instalacion. Lee el mensaje de arriba.
  pause
)
