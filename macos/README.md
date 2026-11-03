# MadSense para macOS — rama de desarrollo

Esta rama añade una aplicación nativa de macOS y un puente HID propio. No es todavía una versión equivalente a Windows: compilar un `.app` no verifica que un juego reciba el mando, que el sistema acepte el dispositivo virtual ni que la vibración funcione en un DualSense físico.

La [evaluación de compatibilidad, alternativas y latencia](COMPATIBILITY.md) compara USB nativo, integración dentro de Wine y el puente global. La autorización de Apple afecta a publicar HID virtual; no es un requisito general para usar el DualSense USB físico. El puente global no se considera la única ruta ni una reproducción completa del USB de Windows.

## Componentes

- `app/`: interfaz AppKit y menú de la barra de macOS; arranca el backend incluido, muestra su estado real y ofrece pausa, reanudación, diagnóstico y cierre ordenado.
- `backend/`: proceso .NET que reutiliza el protocolo DualSense del proyecto; el servicio emite estados JSON por stdout y registros por stderr. Acepta `pause`, `resume`, `speaker high|low|off`, `diagnose` y `quit`, una orden por línea, por stdin.
- `native/`: biblioteca IOKit que publica el HID virtual y recibe informes de salida y de características.
- `audio/`: dispositivo CoreAudio HAL experimental de cuatro canales y biblioteca de lectura. Se distribuye aparte y no se instala automáticamente; consulta [su documentación](audio/README.md).
- HIDAPI: acceso al mando físico por IOKit. La apertura exclusiva mantiene la captura durante la vida del identificador; al cerrarlo, macOS libera la captura.

La interfaz ofrece inglés, español, francés, alemán, italiano y portugués de Brasil. Detecta el idioma del sistema y permite cambiarlo desde el menú de la barra; los mensajes técnicos del backend se conservan en inglés. Cerrar la ventana mantiene el servicio activo; **Salir de MadSense** lo detiene. No cambia preferencias de seguridad de macOS ni instala controladores automáticamente.

**LOGS** incluye un inventario nativo bajo demanda de HID, CoreAudio y GameController, además del diagnóstico del servicio. Se ejecuta en un proceso auxiliar de solo lectura con plazo y salida acotados; no captura el mando ni inicia efectos o grabación. **Guardar diagnóstico…** conserva el JSON original, errores, fecha de captura y sesión del backend. Una interfaz enumerada no se presenta como función validada; las capturas anteriores conservan su fecha al actualizar.

El volumen del altavoz ofrece alto, bajo y apagado, con los mismos valores que Windows. Puede elegirse mientras se espera al mando y se conserva entre ejecuciones en `~/Library/Application Support/MadSense/preferences.json`. La interfaz muestra el valor confirmado por el servicio. Silenciar el altavoz conserva los canales de háptica; requiere que la ruta de audio opcional esté disponible para producir sonido.

El menú permite activar el inicio de sesión mediante una acción explícita; macOS puede exigir confirmarlo en sus ajustes. La batería y la carga proceden del informe físico, con aviso visible al 15 % sin cargar. Los indicadores de háptica, gatillos, audio y vibración reflejan escrituras HID completadas, no mediciones del movimiento de los actuadores. Si el backend se recupera de un fallo estando en pausa, arranca con `--start-paused` antes de buscar mandos.

## Identidad visual compartida

La interfaz Mac debe conservar la identidad de Windows definida en [MainWindow.cs](../src/MainWindow.cs), el [logo vectorial](../src/assets/madsense.svg) y los [catálogos originales de los seis idiomas](../src/Localization/). Los textos de marca se reutilizan por sus claves `Ui.HeaderTagline`, `Ui.Hero*` y `Ui.Badge*`, sin inventar lemas ni volver a traducirlos; el empaquetado debe conservar los archivos `ui.*.json` en `Contents/Resources/WindowsUI`. Las referencias visuales vigentes son [ui-v04-final.png](../evidence/ui-v04-final.png) y [ui-v04-speaker.png](../evidence/ui-v04-speaker.png).

Se mantienen la cabecera condensada de gran tamaño, los rótulos monoespaciados, el mando ilustrado sobre la diagonal naranja con trama, la franja `LINK` y los botones rectangulares con borde y sombra firme. La paleta original es tinta `#181815`, papel `#F0EBDC`, naranja `#F95428`, lima `#DCF131` y texto secundario `#636055`. El código Windows usa Impact, Consolas y Segoe UI; cualquier sustitución necesaria en Mac debe conservar su función visual.

La validación visual debe revisar espera, pausa y bloqueo en los seis idiomas, comprobando textos completos, contraste y controles accesibles. Estas comprobaciones no se consideran superadas antes de inspeccionar las capturas. La identidad compartida no permite anunciar conexión sin confirmación del backend, ofrecer un modo Xbox activo ni afirmar que el HID Mac sea USB compuesto; los límites y errores reales siguen visibles.

## Compilar sin un Mac propio

La compilación nativa necesita macOS; un runner de GitHub Actions permite compilar y comprobar ambos paquetes sin un equipo local. Se generan variantes separadas para Apple Silicon e Intel. Los trabajos de CI no tienen un DualSense físico y sus pruebas no equivalen a validar hardware.

En un runner o Mac con macOS 15 o posterior, Xcode Command Line Tools, .NET 10, CMake, Python 3 e HIDAPI:

```sh
brew install cmake hidapi
bash macos/package.sh osx-arm64 /tmp/madsense-arm64
# Para Intel: ejecutar en un runner Intel con HIDAPI para x86_64.
bash macos/package.sh osx-x64 /tmp/madsense-x64
```

Cada directorio de salida debe estar vacío de `MadSense.app`. Para una biblioteca HIDAPI compilada aparte, establecer `MADSENSE_HIDAPI=/ruta/libhidapi.dylib`. El empaquetador comprueba la arquitectura y copia la biblioteca al paquete; el usuario final no necesita Homebrew, Python ni .NET.

El mínimo del paquete es **macOS 15.0**: coincide con la [matriz vigente de soporte de .NET 10](https://github.com/dotnet/core/blob/main/release-notes/10.0/supported-os.md) y con el mínimo del binario HIDAPI para Apple Silicon que entrega Homebrew en el runner Sequoia. macOS 14 dejó de figurar entre los sistemas admitidos por .NET 10 en septiembre de 2026. Las pruebas actuales se ejecutan en macOS 15, tanto Intel como Apple Silicon; no son una certificación de todas las versiones posteriores.

Antes de crear los ZIP, `verify_deployment.py` lee los encabezados Mach-O de **todos** los ejecutables y bibliotecas distribuidos, incluidos el runtime .NET, HIDAPI y el plugin HAL opcional. Examina la arquitectura seleccionada en binarios universales y los comandos `LC_BUILD_VERSION` o `LC_VERSION_MIN_MACOSX`. Si falta esa arquitectura, falta información del mínimo o una dependencia exige una versión posterior a 15.0, falla el empaquetado. `deployment.json` registra cada binario, su mínimo y el resultado; también se conserva cuando la comprobación falla.

El resultado es un `.app`, su ZIP de desarrollo, un ZIP opcional `MadSenseAudio-<arquitectura>-development.zip` con el dispositivo CoreAudio, y `SHA256SUMS.txt` con los hashes de ambos paquetes. La biblioteca consumidora de audio se incluye dentro del `.app`; el plugin HAL se entrega aparte. Las firmas ad-hoc comprueban integridad y permiten cargar binarios ARM, pero **no conceden la autorización HID virtual de Apple** ni sustituyen notarización.

El runtime autónomo está en `Contents/Resources/backend`: los ensamblados `.dll` administrados no son código Mach-O y no deben ponerse en `Contents/MacOS`. Se firman y verifican individualmente los binarios Mach-O, conservando las autorizaciones del SDK para el JIT, y después se sella el paquete completo. Mantener el runtime junto conserva las búsquedas relativas de .NET; sus binarios nativos también quedan sellados como recursos. Es una distribución de desarrollo, pendiente de revisar con la firma y notarización definitivas.

## Firma y autorización necesarias

El proceso que crea `IOHIDUserDevice` necesita `com.apple.developer.hid.virtual.device`. El código fuente actual de Apple comprueba esa autorización antes de crear el cliente; ejecutarlo como administrador no la sustituye. En este diseño el proceso es `MadSense.Backend`, no la interfaz AppKit. La firma y el perfil de aprovisionamiento aprobados deben aplicarse al proceso correspondiente y a su distribución.

El paquete de desarrollo omite autorizaciones restringidas que no puede acreditar. Puede abrir la interfaz y ejecutar diagnósticos. Al conectar, el backend abre brevemente el mando físico en modo exclusivo, lee sus metadatos y calibración, prepara el adaptador y después crea el dispositivo virtual. Si el sistema rechaza esa creación, libera el mando físico y muestra el bloqueo; no anuncia que está listo ni publica previamente un Sony ficticio. No se recomienda desactivar SIP o AMFI como procedimiento de instalación.

Un fallo de creación, biblioteca o ABI del HID virtual detiene los reintentos automáticos para no capturar repetidamente un mando utilizable. Tras corregir la configuración, **Reintentar puente** permite intentarlo de nuevo. Las desconexiones físicas transitorias sí mantienen la reconexión automática.

La distribución pública requerirá una identidad Developer ID, autorización adecuada, firma de los componentes y notarización. No hay credenciales ni perfiles inventados en esta rama.

## Por qué no copiar USB/IP e HidHide

| Necesidad en Windows | Ruta de macOS | Límite que debe comprobarse |
| --- | --- | --- |
| DualSense USB virtual por usbip-win2 | HID virtual con IOKit | Es un HID virtual, no un dispositivo USB compuesto |
| Ocultar el mando con HidHide | Apertura exclusiva de HIDAPI / `kIOHIDOptionsTypeSeizeDevice` | No es un filtro persistente ni una lista de aplicaciones autorizadas; hay que validar duplicados tras reconexión y suspensión |
| Salida HID: vibración, luces y gatillos | Callbacks de salida y características del dispositivo virtual | Depende del reconocimiento del mando por cada juego y del acceso real al mando físico |
| Audio USB y háptica PCM nativa | Dispositivo CoreAudio HAL de cuatro canales, opcional y experimental | Endpoint independiente: asociación con juegos y funcionamiento con hardware sin verificar |
| Emulación Xbox/XInput | Pendiente | macOS no ofrece la API XInput de Windows; un HID genérico no demuestra equivalencia |

El cliente `karstx/usbip-macos` ofrece conexión USB/IP mediante `IOUSBHostControllerInterface`, pero declara que las transferencias isócronas no funcionan y requiere autorización adicional de Apple. No resuelve la ruta de audio/háptica de MadSense. `usbipd-mac` exporta dispositivos desde macOS; no importa dispositivos virtuales al sistema.

Para audio virtual Apple recomienda un **Audio Server Driver Plug-in (HAL)**; AudioDriverKit se destina a hardware físico. La implementación experimental recibe cuatro canales PCM y los entrega al backend mediante propiedades personalizadas de CoreAudio. Su cola de audio está acotada y la lectura requiere una concesión activa a un solo proceso. No utiliza memoria compartida pública. El backend reutiliza el codificador Bluetooth existente. Esto sigue necesitando comprobar cómo asocian los juegos ese audio al HID: dos dispositivos separados no son automáticamente un DualSense USB compuesto.

La aplicación solo muestra el endpoint CoreAudio y el reenvío PCM como activos cuando la sesión ha abierto la concesión de lectura. La presencia del plugin, de una biblioteca o de código compilado no basta para activar esas capacidades. El ZIP opcional incluye instrucciones de instalación y validación para un Mac de desarrollo; abrir MadSense no copia archivos a `/Library/Audio` ni reinicia CoreAudio.

Además, el código público actual de `IOHIDUserDevice` fuerza el transporte a `Virtual` para clientes sin privilegios internos de Apple. Es incorrecto prometer que establecer una propiedad `Transport=USB` cree un mando USB indistinguible.

## Criterios de validación

1. Compilación nativa en Intel y Apple Silicon; carga de bibliotecas y ejecución del servicio sin excepciones.
2. Creación real del HID con firma autorizada y prueba de ida y vuelta de entrada, salida y características. Si el entorno carece de autorización, el resultado es bloqueado/omitido, nunca aprobado.
3. DualSense físico por Bluetooth: entrada, batería, sensores, calibración, luces, vibración y gatillos; verificar que el puente no se alimenta de su propio mando virtual.
4. Confirmar un solo mando utilizable en juegos; probar apagar/encender, pausa, salida, caída del proceso y suspensión/reanudación. Verificar que el dispositivo físico queda liberado.
5. Validar juego por juego, también Steam Input y CrossOver si se quieren anunciar. Registrar sistema, arquitectura, firmware y resultados.
6. Probar la ruta experimental PCM/altavoz con el plugin instalado y después con mando físico antes de anunciar equivalencia con Windows. La captura del micrófono físico por Bluetooth tampoco está soportada en la versión Windows actual.

## Fuentes técnicas consultadas

- [Apple: HIDVirtualDevice](https://developer.apple.com/documentation/corehid/hidvirtualdevice) y [creación de dispositivos virtuales](https://developer.apple.com/documentation/corehid/creatingvirtualdevices). CoreHID ofrece entrada y callbacks de salida/características; no aporta USB Audio.
- [Apple: autorización HID virtual](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.hid.virtual.device) y [solicitud de autorizaciones DriverKit](https://developer.apple.com/documentation/driverkit/requesting-entitlements-for-driverkit-development).
- [Apple OSS: IOHIDResourceUserClient.cpp](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDResourceUserClient.cpp) y [IOHIDUserDevice.cpp](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDUserDevice.cpp): comprobación de autorización y transporte virtual.
- [HIDAPI: extensiones Darwin](https://github.com/libusb/hidapi/blob/master/mac/hidapi_darwin.h): apertura exclusiva y consulta del modo; llamar a `hid_darwin_set_open_exclusive` después de `hid_init`.
- [Apple: Audio Server Driver Plug-in](https://developer.apple.com/documentation/coreaudio/creating-an-audio-server-driver-plug-in) y [AudioDriverKit](https://developer.apple.com/documentation/audiodriverkit/creating-an-audio-device-driver).
- [usbip-macos](https://github.com/karstx/usbip-macos) y [usbipd-mac](https://github.com/beriberikix/usbipd-mac): alcance de los proyectos y límites declarados.
- [Apple: contenido y firma de bundles](https://developer.apple.com/library/archive/technotes/tn2206/), [DTS sobre ensamblados .NET](https://developer.apple.com/forums/thread/688182) y [Microsoft: publicar aplicaciones .NET para macOS](https://learn.microsoft.com/en-us/dotnet/core/deploying/macos).

Las restricciones de juego y de hardware indicadas son riesgos pendientes de prueba, no resultados medidos.
