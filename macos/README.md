# MadSense para macOS — rama de desarrollo

Esta rama añade una aplicación nativa de macOS y un puente HID propio. No es todavía una versión equivalente a Windows: compilar un `.app` no verifica que un juego reciba el mando, que el sistema acepte el dispositivo virtual ni que la vibración funcione en un DualSense físico.

## Componentes

- `app/`: interfaz AppKit y menú de la barra de macOS; arranca el backend incluido, muestra su estado real y ofrece pausa, reanudación, diagnóstico y cierre ordenado.
- `backend/`: proceso .NET que reutiliza el protocolo DualSense del proyecto; el servicio emite estados JSON por stdout y registros por stderr. Acepta `pause`, `resume`, `diagnose` y `quit`, una orden por línea, por stdin.
- `native/`: biblioteca IOKit que publica el HID virtual y recibe informes de salida y de características.
- HIDAPI: acceso al mando físico por IOKit. La apertura exclusiva mantiene la captura durante la vida del identificador; al cerrarlo, macOS libera la captura.

La interfaz tiene textos propios en español o inglés, elegidos según el idioma del sistema. Cerrar la ventana mantiene el servicio activo; **Salir de MadSense** lo detiene. No cambia preferencias de seguridad de macOS ni instala controladores automáticamente.

## Compilar sin un Mac propio

La compilación nativa necesita macOS; un runner de GitHub Actions permite compilar y comprobar ambos paquetes sin un equipo local. Se generan variantes separadas para Apple Silicon e Intel. Los trabajos de CI no tienen un DualSense físico y sus pruebas no equivalen a validar hardware.

En un runner o Mac con macOS 14 o posterior, Xcode Command Line Tools, .NET 10, CMake e HIDAPI:

```sh
brew install cmake hidapi
bash macos/package.sh osx-arm64 /tmp/madsense-arm64
# Para Intel: ejecutar en un runner Intel con HIDAPI para x86_64.
bash macos/package.sh osx-x64 /tmp/madsense-x64
```

Cada directorio de salida debe estar vacío de `MadSense.app`. Para una biblioteca HIDAPI compilada aparte, establecer `MADSENSE_HIDAPI=/ruta/libhidapi.dylib`. El empaquetador comprueba la arquitectura y copia la biblioteca al paquete; el usuario final no necesita Homebrew ni .NET.

El resultado es un `.app`, un ZIP de desarrollo y `SHA256SUMS.txt`. Las firmas ad-hoc comprueban integridad y permiten cargar binarios ARM, pero **no conceden la autorización HID virtual de Apple** ni sustituyen notarización.

## Firma y autorización necesarias

El proceso que crea `IOHIDUserDevice` necesita `com.apple.developer.hid.virtual.device`. El código fuente actual de Apple comprueba esa autorización antes de crear el cliente; ejecutarlo como administrador no la sustituye. En este diseño el proceso es `MadSense.Backend`, no la interfaz AppKit. La firma y el perfil de aprovisionamiento aprobados deben aplicarse al proceso correspondiente y a su distribución.

El paquete de desarrollo omite autorizaciones restringidas que no puede acreditar. Puede abrir la interfaz y ejecutar diagnósticos; si el sistema rechaza crear el dispositivo virtual, debe mostrar el bloqueo y no capturar el mando físico ni anunciar que está listo. No se recomienda desactivar SIP o AMFI como procedimiento de instalación.

La distribución pública requerirá una identidad Developer ID, autorización adecuada, firma de los componentes y notarización. No hay credenciales ni perfiles inventados en esta rama.

## Por qué no copiar USB/IP e HidHide

| Necesidad en Windows | Ruta de macOS | Límite que debe comprobarse |
| --- | --- | --- |
| DualSense USB virtual por usbip-win2 | HID virtual con IOKit | Es un HID virtual, no un dispositivo USB compuesto |
| Ocultar el mando con HidHide | Apertura exclusiva de HIDAPI / `kIOHIDOptionsTypeSeizeDevice` | No es un filtro persistente ni una lista de aplicaciones autorizadas; hay que validar duplicados tras reconexión y suspensión |
| Salida HID: vibración, luces y gatillos | Callbacks de salida y características del dispositivo virtual | Depende del reconocimiento del mando por cada juego y del acceso real al mando físico |
| Audio USB y háptica PCM nativa | Pendiente: dispositivo CoreAudio HAL de cuatro canales | HID virtual no crea un endpoint de audio; no se declara soporte |
| Emulación Xbox/XInput | Pendiente | macOS no ofrece la API XInput de Windows; un HID genérico no demuestra equivalencia |

El cliente `karstx/usbip-macos` ofrece conexión USB/IP mediante `IOUSBHostControllerInterface`, pero declara que las transferencias isócronas no funcionan y requiere autorización adicional de Apple. No resuelve la ruta de audio/háptica de MadSense. `usbipd-mac` exporta dispositivos desde macOS; no importa dispositivos virtuales al sistema.

Para audio virtual Apple recomienda un **Audio Server Driver Plug-in (HAL)**; AudioDriverKit se destina a hardware físico. Una implementación futura puede recibir cuatro canales PCM, enviarlos mediante memoria compartida acotada al backend y reutilizar el codificador Bluetooth existente. Esto sigue necesitando comprobar cómo asocian los juegos ese audio al HID: dos dispositivos separados no son automáticamente un DualSense USB compuesto.

Además, el código público actual de `IOHIDUserDevice` fuerza el transporte a `Virtual` para clientes sin privilegios internos de Apple. Es incorrecto prometer que establecer una propiedad `Transport=USB` cree un mando USB indistinguible.

## Criterios de validación

1. Compilación nativa en Intel y Apple Silicon; carga de bibliotecas y ejecución del servicio sin excepciones.
2. Creación real del HID con firma autorizada y prueba de ida y vuelta de entrada, salida y características. Si el entorno carece de autorización, el resultado es bloqueado/omitido, nunca aprobado.
3. DualSense físico por Bluetooth: entrada, batería, sensores, calibración, luces, vibración y gatillos; verificar que el puente no se alimenta de su propio mando virtual.
4. Confirmar un solo mando utilizable en juegos; probar apagar/encender, pausa, salida, caída del proceso y suspensión/reanudación. Verificar que el dispositivo físico queda liberado.
5. Validar juego por juego, también Steam Input y CrossOver si se quieren anunciar. Registrar sistema, arquitectura, firmware y resultados.
6. Implementar y probar la ruta PCM/altavoz antes de anunciar equivalencia con Windows. La captura del micrófono físico por Bluetooth tampoco está soportada en la versión Windows actual.

## Fuentes técnicas consultadas

- [Apple: HIDVirtualDevice](https://developer.apple.com/documentation/corehid/hidvirtualdevice) y [creación de dispositivos virtuales](https://developer.apple.com/documentation/corehid/creatingvirtualdevices). CoreHID ofrece entrada y callbacks de salida/características; no aporta USB Audio.
- [Apple: autorización HID virtual](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.hid.virtual.device) y [solicitud de autorizaciones DriverKit](https://developer.apple.com/documentation/driverkit/requesting-entitlements-for-driverkit-development).
- [Apple OSS: IOHIDResourceUserClient.cpp](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDResourceUserClient.cpp) y [IOHIDUserDevice.cpp](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDUserDevice.cpp): comprobación de autorización y transporte virtual.
- [HIDAPI: extensiones Darwin](https://github.com/libusb/hidapi/blob/master/mac/hidapi_darwin.h): apertura exclusiva y consulta del modo; llamar a `hid_darwin_set_open_exclusive` después de `hid_init`.
- [Apple: Audio Server Driver Plug-in](https://developer.apple.com/documentation/coreaudio/creating-an-audio-server-driver-plug-in) y [AudioDriverKit](https://developer.apple.com/documentation/audiodriverkit/creating-an-audio-device-driver).
- [usbip-macos](https://github.com/karstx/usbip-macos) y [usbipd-mac](https://github.com/beriberikix/usbipd-mac): alcance de los proyectos y límites declarados.

Las restricciones de juego y de hardware indicadas son riesgos pendientes de prueba, no resultados medidos.
