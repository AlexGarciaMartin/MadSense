# Prueba de instalación en Windows limpio

Estado: **plan preparado, no ejecutado**. Esta carpeta no contiene un launcher que instale software en el equipo desde el que se abre el repositorio. Falta disponer de un Windows desechable operativo; las pruebas anteriores utilizaron los controladores ya instalados en el PC de desarrollo.

## Entorno y alcance

Usar una VM x64 con instantánea limpia o Windows Sandbox. Windows Sandbox permite conservar el estado durante un reinicio interno en versiones compatibles, pero cerrar la sesión de Sandbox elimina su contenido. Habilitar Sandbox en el host y reiniciar el host son pasos distintos de probar MadSense dentro del huésped; este plan no los ejecuta.

La primera prueba cubre la instalación de USBip e HidHide desde cero, la petición de reinicio, el arranque de MadSense y su desinstalación. No acredita Bluetooth, un único mando visible en un juego, hápticos, gatillos, altavoz, latencia ni sensaciones físicas. Esas comprobaciones requieren un DualSense y acceso real al adaptador; Sandbox no ofrece en este plan un paso de USB/Bluetooth validado.

Fuentes de Microsoft: [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/), [configuración `.wsb`](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file) y [preguntas frecuentes](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-faq).

## Preparación del paquete

Compilar la rama y copiar a una carpeta de entrada dedicada únicamente:

- `MadSense-<version>-Setup.exe` de producción, nunca el build `TEST`.
- Sus archivos `.exe.json` y `.exe.sha256`, generados en la misma compilación.
- Este plan y, cuando exista, el harness que lo implemente.

Para Sandbox, mapear esa carpeta como `C:\MadSensePayload` con `ReadOnly=true`. Si se exportan resultados automáticamente, mapear otra carpeta vacía y exclusiva como `C:\MadSenseResults`; nunca el repositorio completo, el perfil del usuario ni carpetas con credenciales. Los logs completos se guardan dentro del huésped. La evidencia pública debe usar el resumen sanitizado descrito abajo.

## Guardia obligatoria antes de cualquier instalación

Un futuro harness debe utilizar `Baseline` como acción predeterminada; `Install` tiene que seleccionarse explícitamente. Debe rechazar la operación si no se cumplen **todas** estas condiciones:

1. Windows x64 nativo, PowerShell de 64 bits y build igual o superior a 19045.
2. Modelo y fabricante de VM reconocidos mediante `Win32_ComputerSystem`, por ejemplo `Microsoft Corporation` / `Virtual Machine`. **`HypervisorPresent=true` no demuestra que sea un huésped**: también puede aparecer en el PC físico.
3. Marcador de habilitación creado deliberadamente **dentro del huésped** en `C:\MadSenseCleanTest\ALLOW-ISOLATED-TEST.json`, con propósito `madsense-clean-install`, identificador de ejecución, vencimiento y hashes del `MachineGuid` y SID del usuario actual. No distribuir ese marcador con el paquete ni generarlo automáticamente al invocar `Install`.
4. Sin MadSense en ejecución, sin su carpeta de instalación por usuario y sin entradas de desinstalación/arranque de MadSense previas.
5. Sin servicios, dispositivos, entradas de desinstalación ni carpetas de programa previos de USBip e HidHide. Una mera ausencia de sus EXE no basta.
6. Metadatos de producción (`testBuild=false`), hash SHA-256 coincidente y commit de origen identificado. Copiar el EXE a una carpeta local del huésped y verificar otra vez el hash antes de ejecutarlo.

El marcador y la detección de VM evitan un lanzamiento accidental; no sustituyen una frontera de seguridad frente a código malicioso. Si falta una señal, el resultado es **bloqueado**, no una opción para ignorar la comprobación.

## Fase 1: línea base de solo lectura

Registrar dentro del huésped:

- Build/arquitectura de Windows, arquitectura del proceso PowerShell y tipo de entorno.
- SHA-256 del Setup, commit de origen e identificador de ejecución.
- Hora del último arranque de Windows y cuenta de prueba mediante identificadores hash locales.
- Ausencia de las instalaciones, servicios y procesos anteriores.
- Estado de reinicio pendiente del sistema. Si el Windows limpio ya pide reiniciar, resolverlo antes de atribuir un nuevo reinicio a MadSense.

Guardar `baseline.json` y no continuar si alguna comprobación es desconocida o falla. Antes de instalar, repetir las comprobaciones de limpieza para impedir que una línea base antigua habilite un equipo que ya ha cambiado. No borrar ni desinstalar software existente para hacer pasar esta guardia: restaurar la instantánea o crear un huésped nuevo.

## Fase 2: instalación real

Solo después de la guardia y dentro del huésped, ejecutar la copia verificada del Setup con estos argumentos:

```text
/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOLAUNCH=1 /RESTARTEXITCODE=3010 /LOG="C:\MadSenseCleanTest\setup.log"
```

No añadir `/DIR`: se comprueba el destino normal `%LOCALAPPDATA%\Programs\MadSense`. No elevar todo el asistente automáticamente; los permisos de controladores siguen el flujo de UAC del producto. Si UAC impide continuar sin intervención, registrar ese límite y no declarar éxito.

Esperar un máximo de 15 minutos. Si expira, registrar el estado y conservar el proceso y los logs para diagnóstico, sin forzar el cierre de instaladores de drivers. No reintentar sobre el mismo huésped como si continuara limpio.

Con ambos drivers inicialmente ausentes, se espera salida **3010**, archivos/servicios de ambos paquetes presentes, app copiada, desinstalación registrada en HKCU y ninguna instancia de MadSense abierta. Una salida `0` no demuestra que se haya propagado correctamente el reinicio. Cualquier otro código o requisito ausente es un fallo de esta fase.

Guardar `install-result.json`, el log local y la hora del arranque. El harness no debe reiniciar el host ni reiniciar automáticamente el huésped. La fase siguiente comienza tras un reinicio deliberado del huésped; no cerrar la ventana de Sandbox para simularlo.

## Fase 3: después del reinicio del huésped

Verificar de nuevo la identidad del huésped/cuenta y que el último arranque sea posterior al registrado antes de instalar. Si el entorno no permite demostrarlo, marcar esta fase como no validada.

Comprobaciones medibles:

1. Instalación y registro de desinstalación siguen en el perfil original; el acceso de Inicio apunta al nuevo EXE.
2. `usbip2_ude` e `HidHide` están instalados y operativos; el dispositivo USBip no informa errores de PnP. El helper instalado, con `-Action Inspect` desde PowerShell de 64 bits, devuelve `Success=1` y `NeedsInstall=0`. Conservar su código y resultado INI.
3. El payload incluye `MadSense.exe`, `MadSense.dll`, sus archivos `.deps.json`/`.runtimeconfig.json` y el runtime autocontenido (`coreclr.dll`, `hostfxr.dll`, `hostpolicy.dll`). No instalar .NET por separado para ocultar una dependencia omitida.
4. `MadSense.exe --installer-check-running` termina con `0` antes de abrir la aplicación. Este comando prueba carga del runtime, **no** el arranque completo de la interfaz ni el puente.
5. Abrir la copia instalada sin elevación, comprobar que permanece viva y que muestra el estado de espera cuando no hay mando. Verificar autorización de su ruta exacta en HidHide y ausencia de errores de inicio. No interpretar «sin mando» como un fallo de instalación.
6. Cerrar mediante el mantenimiento de esa ruta exacta o «Salir y liberar el mando». No usar una parada global ni matar procesos por nombre.
7. Desinstalar mediante el desinstalador registrado; comprobar salida `0`, retirada del EXE/registro/acceso y de su autorización en HidHide. Los controladores compartidos deben permanecer instalados.

La cuenta estándar con credenciales de otro administrador en UAC constituye otra ejecución de la matriz. Un Sandbox que opere solo como administrador no valida ese caso.

## Resultado sanitizado

Exportar un resumen por fase con `schemaVersion`, `runId`, `phase`, `status` (`not_run`, `blocked`, `failed` o `passed`), `windowsBuild`, `environmentKind`, `installerSha256`, `sourceCommit`, códigos de salida y comprobaciones booleanas. Una comprobación no realizada debe ser `null`, nunca `true`.

No incluir nombres de usuario/equipo, SID o MachineGuid sin hash, rutas del perfil, MAC, dispositivos Bluetooth identificables, credenciales ni logs completos. Mantener siempre `physicalControllerValidated=false` en esta prueba de instalación.

Las capturas y resultados deben identificar la compilación probada. No sustituir las evidencias funcionales de un artefacto anterior ni etiquetar este plan como una ejecución completada. Una vez exista el entorno aislado, implementar y ejecutar la automatización acotada sobre estas tres fases; hasta entonces, no hay un resultado de Windows limpio que publicar.
