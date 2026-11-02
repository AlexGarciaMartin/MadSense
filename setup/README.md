# Instalador de Windows de MadSense

Trabajo de la rama `feature/windows_installer`. La versión de la app se mantiene en `0.4.0`. Este código genera `MadSense-0.4.0-Setup.exe`; no publica una release ni sustituye automáticamente el ZIP público anterior.

## Diseño y alcance

- Asistente Inno Setup por usuario, con logo, paleta y tipografía de la web convertidos a imágenes. No instala fuentes.
- Bienvenida con acción directa **Instalar** y opción de acceso al escritorio; no hay una segunda página de resumen. El lateral reúne el logo y «ENCIENDE. JUEGA. SIN CABLE.», con espacio entre líneas y sin bandas decorativas.
- Las cabeceras intermedias y del desinstalador miden el texto con la fuente actual y ajustan la separación entre título, descripción y contenido. No conservan la altura original de 14 píxeles al ampliar la letra. Las posiciones se calculan desde las medidas iniciales para no desplazarse al volver atrás.
- Windows 10 22H2 (build 19045) o Windows 11, x64 nativo Intel/AMD. Se rechazan x86 y ARM64.
- El propio Setup y su desinstalador se compilan como x64 mediante `SetupArchitecture=x64`. No basta con instalar archivos en modo de 64 bits: un Setup de 32 bits puede redirigir `System32` y ejecutar PowerShell de 32 bits. El helper comprueba la arquitectura del proceso y rechaza ese caso antes de cambiar controladores.
- Aplicación y datos del usuario en `%LOCALAPPDATA%\Programs\MadSense`. La app actual escribe sus preferencias y registros junto al EXE; no se mueve a `Program Files`.
- El asistente conserva el usuario original. Solo eleva el helper de instalación/reparación de los controladores, espera su resultado y no eleva MadSense al finalizar.
- USBip 0.9.8.1 e HidHide 1.5.230 incluidos como instaladores oficiales sin modificar. Versiones, descargas y SHA-256 se fijan en `dependencies.json`; también se verifica Authenticode antes de ejecutarlos.
- USBip utiliza su selección compacta y el redistribuible VC++ correspondiente. Cambiar cualquiera de los dos controladores requiere reinicio antes del primer arranque.
- Desinstalación registrada en Aplicaciones instaladas. Conserva preferencias, registros y controladores compartidos; primero comprueba el cierre y recuperación de esta instalación.

Una copia de desarrollo en otra carpeta no es el destino de la actualización. El instalador no usa el antiguo `MadSense.exe --stop` global ni mata todos los procesos por nombre. Si no puede identificar o cerrar de forma segura la copia objetivo, pide cerrarla desde la bandeja.

## Compilación

Desde la raíz del repositorio, con .NET SDK 10:

```powershell
.\tools\build-installer.ps1
```

El resultado aparece en `dist\installer\`, con `.sha256` y `.json`. La publicación se genera en una carpeta nueva de `.build/`, sin sobrescribir `app/`. El script admite `-IsccPath`, `-DotnetPath`, `-PublishDirectory` y `-DependencyDirectory`; el README principal describe cada opción.

Si falta `ISCC.exe`, el fallback portable es `Tools.InnoSetup 7.1.0` de NuGet, verificado por su hash fijado y extraído en `.tools/`. Un compilador externo indicado con `-IsccPath` no se descarga automáticamente; su versión queda registrada en el JSON del artefacto. La firma del EXE de MadSense es un paso independiente y no se añade al compilar.

Los textos completos de USBip, HidHide e Inno Setup se conservan en `licenses/`. Anton y su OFL están en `assets/`, junto al arte usado por el asistente. Regeneración de imágenes: `tools/generate-installer-art.ps1`.

## Contrato del helper de controladores

`DriverSetup.ps1` se ejecuta con Windows PowerShell **de 64 bits** y carga `DriverSetup.Core.psm1` junto a `dependencies.json`. No abre MadSense ni crea accesos directos. Parámetros:

| Parámetro | Contrato |
|---|---|
| `-Action Inspect` | Lectura de plataforma, versiones, servicios/dispositivos y disponibilidad de los controladores. No instala ni cambia HidHide. |
| `-Action Install` | Requiere administrador. Instala/repara los controladores necesarios y conserva el aviso de reinicio incluso ante un fallo parcial posterior. |
| `-Action Register` | Autoriza únicamente el `MadSense.exe` indicado y consulta después la lista para verificarlo. |
| `-Action Unregister` | Retira únicamente esa autorización y verifica el resultado; no elimina los controladores. |
| `-ResultPath` | Ruta absoluta a un archivo `.ini`. Obligatoria. |
| `-AppPath` | Ruta absoluta a `MadSense.exe`, requerida para registro/desregistro. |
| `-PayloadDirectory` | Carpeta de instaladores incluidos; si se invoca el helper directamente sin ellos, puede descargar desde las URLs fijadas. |
| `-AllowPendingReboot` | Permite que `Register` declare pendiente la autorización si HidHide está instalado pero aún necesita el reinicio. Nunca convierte un error operativo de CLI en éxito. |

Resultado INI **UTF-16 LE**, sección `[Result]`:

```ini
[Result]
Success=1
RebootRequired=0
NeedsInstall=0
RegistrationPending=0
Error=
DriverSummary=USBip: listo; HidHide: listo
```

Los booleanos se escriben como `0`/`1`; `Error` y `DriverSummary` son texto de una sola línea. Salida de proceso: `0` éxito, `3010` éxito con reinicio requerido, `1` fallo. Hay que comprobar **el código y `Success`**. Un fallo también puede llevar `RebootRequired=1` si ya cambió una dependencia.

No se da un driver por válido solo por existir un EXE: se comprueba versión, servicio y funcionamiento del dispositivo o CLI. Una versión posterior averiada no se sustituye por una anterior. Los paquetes se copian a una carpeta temporal protegida, se verifica hash/firma allí y se ejecutan desde esa copia. Se conservan las autorizaciones y los dispositivos de otras aplicaciones.

`RegistrationPending=1` significa que la autorización no se ha confirmado todavía: el asistente pide reiniciar y la app vuelve a comprobarla al arrancar. No debe mostrarse «lista para jugar» durante esa espera.

## Contrato de mantenimiento de la aplicación

Los comandos de mantenimiento se procesan antes de crear la interfaz, el puente o los registros normales:

```text
MadSense.exe --prepare-update --install-path "RUTA_ABSOLUTA\MadSense.exe" [--result-path "RUTA_ABSOLUTA\resultado.txt"]
MadSense.exe --prepare-uninstall --install-path "RUTA_ABSOLUTA\MadSense.exe" [--result-path "RUTA_ABSOLUTA\resultado.txt"]
MadSense.exe --installer-check-running
```

La copia nueva extraída temporalmente puede preparar una instalación antigua. `--install-path` identifica la copia que se va a modificar; no tiene por qué ser el propio ejecutable del helper. Se comprueba la ruta del proceso, se solicita cierre ordenado solo a esa copia y se espera de forma acotada, sin `Kill`. Una copia antigua sin el protocolo seguro requiere cierre manual.

Antes de permitir el borrado se restaura el estado guardado en `hidden-controller.json`. Si falla, el diario se conserva y la desinstalación se bloquea. No se modifica HidHide si otra instancia está utilizando el mando y hay una recuperación pendiente.

| Código | Significado |
|---|---|
| `0` | Preparado, o ninguna otra instancia abierta para `--installer-check-running`. |
| `2` | Ocupado/no se puede cerrar o verificar de forma segura; en la consulta, hay otra instancia. |
| `3` | Fallo de recuperación u otra operación de mantenimiento. |
| `4` | Argumentos o ruta de resultado inválidos. |

`--result-path` escribe un mensaje UTF-8. No contiene un permiso para forzar el cierre o eliminar el diario. No se usa el antiguo evento global de parada durante el mantenimiento.

## Pruebas automáticas

Las primeras dos órdenes son pruebas aisladas de lógica; no instalan controladores ni necesitan un mando:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\installer\test-drivers.ps1
dotnet test .\tests\MadSense.Installer.Tests\MadSense.Installer.Tests.csproj -c Release
```

Para probar el asistente completo sin modificar los drivers:

```powershell
.\tools\build-installer.ps1 -InstallerTestBuild
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\installer\test-setup.ps1 -Installer .\dist\installer\MadSense-0.4.0-Setup-TEST.exe
```

El build `TEST` usa identidad, nombre y carpeta distintos, simula el helper de controladores y no abre la app al terminar. La prueba **sí copia y elimina sus propios archivos, crea su acceso de Inicio y registra su propio desinstalador en HKCU**. Verifica hash y metadatos del EXE de prueba antes de lanzarlo; rechaza el EXE normal.

La simulación mantiene la exigencia de PowerShell de 64 bits. Antes de los escenarios del asistente, `test-setup.ps1` ejecuta el helper falso en hosts reales de 32 y 64 bits y comprueba sus códigos y el INI: el primero debe fallar, el segundo debe aceptar la inspección. Durante la instalación `TEST`, una redirección accidental a PowerShell de 32 bits también impide anunciar éxito.

Los escenarios cubren instalación, actualización conservando preferencias, desinstalación, error de driver, petición de reinicio, error de autorización y bloqueo cuando no se puede restaurar el diario. Evidencias del asistente en `.build/installer-qa/`. El build de prueba admite `/TESTSCENARIO=normal`, `driver-failure`, `reboot` o `register-failure`; esos escenarios no pertenecen al producto normal.

## Matriz manual antes de una release

La instalación real en el PC de desarrollo ya ha confirmado la carpeta por usuario, la autorización exacta del EXE en HidHide y el arranque con DualSense por Bluetooth, USBip y reenvío de audio activos con dependencias existentes.

El 2 de noviembre de 2026 se desinstalaron MadSense, USBip e HidHide y se reinició Windows 11 x64 (build 26200). Antes de reinstalar se comprobó que no quedaban la carpeta de la app, los registros de desinstalación ni los servicios y paquetes de esos drivers. El Setup de producción instaló ambos componentes, autorizó su EXE en HidHide, registró la app en el perfil del usuario y pidió reiniciar sin abrir MadSense. El helper devolvió `Success=1`, `NeedsInstall=0` y `RebootRequired=1`; la autorización devolvió `Success=1` y `RegistrationPending=0`. El código final del proceso Setup no quedó capturado, por lo que no se da por verificado en esta ejecución. **Sigue pendiente el primer arranque tras este segundo reinicio.** Las evidencias locales están en `.build/fresh-driver-qa-20261102-032438/` y no se publican porque contienen rutas e identificadores del equipo.

Durante esa prueba se corrigió el texto final con `/NORESTART`: debe indicar que se reinicie antes de abrir la app, aunque Inno oculte los controles de reinicio. Se verificó visualmente el build `TEST` en el escenario `reboot`, con y sin `/NORESTART`: texto correcto en ambos casos, «reiniciar más tarde» seleccionado cuando aparecen las opciones y salida `3010` en ambas ejecuciones. La copia `TEST` se desinstaló después. Esta reinstalación con drivers ausentes no equivale a un Windows virgen, ni sustituye la prueba con una cuenta estándar que use credenciales de otro administrador o la comprobación física de los efectos del mando en un juego.

También se reprodujo y corrigió el recorte inferior de los títulos intermedios. Con el build `TEST` se revisaron la bienvenida, el error simulado de comprobación, la navegación atrás/adelante y el error final de autorización; después se desinstaló esa copia. La comprobación visual corresponde a la escala actual del PC; la matriz de otras escalas sigue pendiente.

Completar los casos pendientes en Windows limpio o una VM con instantánea, fuera del PC de desarrollo. Las pruebas de Bluetooth y sensaciones del mando requieren hardware real accesible a ese entorno; una VM sin acceso al adaptador no valida esos pasos. Registrar versión/build de Windows, cuenta, escala de pantalla, códigos, logs y resultado de cada fila. La tabla es un plan de validación, **no una afirmación de que ya esté superado**.

| Caso | Comprobación esperada |
|---|---|
| Windows limpio x64, ambos drivers ausentes | Instala los paquetes verificados, copia la app para el usuario, registra desinstalador y pide reiniciar. No abre MadSense antes del reinicio. |
| Tras ese reinicio | Abre desde Inicio sin elevación; empareja/conecta el DualSense, comprueba un único mando visible para el juego y modos DualSense/Xbox. |
| Solo falta USBip; HidHide ya está operativo | Instala USBip compacto y pide reinicio aunque el paquete devuelva `0`. |
| Solo falta HidHide | Instala, pide reinicio y no anuncia una autorización confirmada antes de que el driver responda. |
| Usuario estándar con credenciales de otro administrador en UAC | Solo el helper se eleva. App, acceso de Inicio y desinstalador permanecen en el perfil original; MadSense no arranca elevada. |
| Cancelación de UAC | El asistente informa del paso pendiente/fallo; no anuncia instalación completa ni abre la app. Permite cancelar o reintentar. |
| Reinicio pendiente / driver instalado pero aún no operativo | Lo indica; no da por listo el mando por encontrar un EXE y no arranca la app antes del reinicio requerido. |
| Actualización de la versión nueva abierta | Solicita cierre seguro a la ruta objetivo y conserva preferencias. No mata copias ajenas. |
| Actualización de la antigua `v0.4` abierta | Solicita cierre desde la bandeja si no dispone de protocolo seguro. Tras cerrarla, continúa conservando ajustes. |
| Otra copia de desarrollo abierta en otra carpeta | No la cierra, no usa `--stop` global y no lanza una segunda app al terminar. |
| HidHide devuelve error al autorizar | No muestra éxito falso; conserva diagnóstico y vía de reparación/desinstalación. |
| Fallo de recuperación al desinstalar | Se bloquea antes de borrar la app o `hidden-controller.json`. Reintentar solo después de resolver la causa. |
| Cancelar la desinstalación | No detiene la app ni retira su autorización. |
| Desinstalación completa | Se elimina la app y su registro/accesos. Se conservan preferencias y los drivers compartidos, sin retirar otras autorizaciones. |
| ARM64, x86 o Windows anterior al mínimo | Rechazo claro antes de instalar dependencias. |
| Interfaz a 100 %, 150 % y 200 % | Textos y botones completos, contraste legible y logo nítido; comprobar también error y reinicio. |

No desactives protecciones de Windows para hacer pasar una instalación. Si una firma no puede validarse, un driver no carga o existe un conflicto, conserva el error y averigua la causa antes de declarar compatible esa configuración.
