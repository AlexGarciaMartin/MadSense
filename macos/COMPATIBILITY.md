# DualSense en macOS: arquitectura y evidencia

Investigación del 3 de noviembre de 2026. Los juegos nativos y Wine/CrossOver tienen la misma prioridad. Esta evaluación separa documentación de APIs, código de otros proyectos, pruebas de nuestras máquinas virtuales y pruebas físicas, que aún no tenemos.

## Decisión de arquitectura

MadSense necesita rutas complementarias. El puente global HID + HAL es una vía experimental para Bluetooth, pero no reproduce un dispositivo USB compuesto y no debe ser la única estrategia.

1. **USB físico y juegos nativos:** conservar las interfaces de Apple. Observar capacidades y facilitar diagnósticos sin capturar el mando, sustituir su controlador de audio ni introducir una conversión Bluetooth. Un juego que ya funciona con el mando USB no necesita pasar por MadSense.
2. **USB físico y Wine:** investigar la asociación entre HID y audio dentro de Wine. Ambos pertenecen al mismo mando, pero un juego Windows puede no encontrarlos si sus identificadores de contenedor no coinciden. Esta integración no necesita crear un HID global de macOS.
3. **Bluetooth y Wine:** un adaptador dentro de Wine puede presentar entrada y audio con semántica USB a los juegos de ese proceso. Hay código libre que demuestra la arquitectura; su implementación actual no cubre todo el audio de MadSense.
4. **Bluetooth y juegos nativos:** conservar primero la conexión física y las funciones que el juego ya utilice mediante GameController. Para los casos que necesiten traducción, el puente global existente sigue siendo experimental. Requiere autorización para publicar HID, reconocimiento del dispositivo por el juego y asociación de su audio. Son tres comprobaciones independientes. La firma con la autorización HID concedida solo resuelve la primera.

No se incorpora automáticamente un controlador, una modificación de CrossOver ni una biblioteca descargada de otro proyecto. Los experimentos y adaptaciones de fuentes se mantienen separados de la aplicación normal.

## Qué expone el DualSense USB físico

Apple documenta conexión de mandos PlayStation por USB y Bluetooth. `GCDualSenseGamepad`, disponible desde macOS 11.3, expone el perfil específico, superficies del panel táctil y gatillos adaptativos. Movimiento, luz y háptica se consultan por dispositivo; que una API exista no obliga a un juego a utilizarla. En particular, `CHHapticEngine.capabilitiesForHardware()` describe el hardware anfitrión: no sustituye la consulta de `controller.haptics?.supportedLocalities`.

Fuentes: [conexión de mandos Apple](https://support.apple.com/en-us/111100), [GCDualSenseGamepad](https://developer.apple.com/documentation/gamecontroller/gcdualsensegamepad), [GCDeviceHaptics](https://developer.apple.com/documentation/gamecontroller/gcdevicehaptics), [ejemplos oficiales de GameController](https://github.com/apple/game-porting-toolkit/tree/main/game-porting-skills/skills/using-game-controller).

La ruta PCM es distinta de la vibración convencional. Chiaki-ng documenta háptica USB en Mac desde la versión 1.8.1 y utiliza una salida de **cuatro canales totales**, con los dos traseros destinados a háptica. No son cuatro actuadores hápticos. Su configuración y código muestran que disponer del endpoint y dirigirle el flujo adecuado es esencial; no prueban que todos los juegos macOS lo abran correctamente. [Release y configuración de Chiaki-ng](https://github.com/chiaki-ng/chiaki-ng/releases/tag/v1.8.1).

La documentación de Sony depende del contexto: su página general enumera limitaciones de funciones en Mac, mientras que Remote Play describe háptica DualSense por USB en Mac. Es incorrecto convertir cualquiera de esas páginas en una afirmación universal sobre todos los juegos. [Compatibilidad general de Sony](https://www.playstation.com/es-es/support/hardware/pair-dualsense-controller-bluetooth/), [Remote Play para Mac](https://remoteplay.dl.playstation.net/remoteplay/lang/en/1100002.html).

| Función | Ruta USB nativa | Evidencia que falta en MadSense |
| --- | --- | --- |
| Botones, ejes y panel táctil | GameController / HID del mando físico | Prueba física y reconocimiento en cada juego |
| Movimiento, batería, luces y gatillos | Perfiles Apple y protocolos HID según aplicación | Lecturas y efectos reales, firmware y concurrencia |
| Vibración convencional | API del juego o informes HID | Actuación real y ausencia de escritores competidores |
| Háptica PCM | Audio USB multicanal, usado por proyectos como Chiaki-ng | Formato/canales reales, asociación y reproducción |
| Altavoz, auriculares y micrófono | Interfaces que realmente enumere ese mando y sistema | No inferir funcionamiento por el nombre del dispositivo |
| Compatibilidad con juegos Windows | Traducción de Wine, incluida identidad de HID/audio | Versión exacta de Wine/CrossOver y pruebas por juego |

El inventario de `research/usb_native_probe.swift` lee IORegistry, propiedades CoreAudio y perfiles GameController. Omite números de serie, direcciones Bluetooth y UID completos. No abre ni captura HID, no escribe efectos, no graba, no inicia audio y no cambia el dispositivo predeterminado. Los canales, formatos y latencias declaradas por CoreAudio son propiedades, **no medidas de latencia física**. La correlación por ubicación USB es una heurística explícita; un UID desconocido no se interpreta como incompatibilidad.

## Wine: una alternativa concreta sin HID global

Se examinó [MacGameFix, revisión 233d4daa8c5c06d2a2b85dee8d720bdc2dc7f65d](https://github.com/MathiasKowoll/MacGameFix/tree/233d4daa8c5c06d2a2b85dee8d720bdc2dc7f65d/engine). Sus tres primeros parches asignan el mismo `DEVPKEY_Device_ContainerId` a un HID USB y a su endpoint CoreAudio a partir de la ubicación USB. El tercero obtiene ese valor del controlador para endpoints activos en vez de conservar un valor obsoleto del registro. La ruta WOW64 también debe transportar `VT_CLSID` correctamente.

Los parches se pueden aplicar a fuentes públicas de Wine 11.17; no es necesario adquirir fuentes privadas para estudiar esa asociación. Aplicación del parche, compilación, carga en Wine y compatibilidad con un juego son etapas distintas. La serie posterior añade enumeración de endpoints mediante SetupAPI y tratamiento de XAudio2: los tres primeros parches no constituyen una solución universal. Las modificaciones de CPU/ntdll que contiene ese proyecto son ajenas al mando y quedan fuera de este trabajo.

El [kit opcional de fuentes Wine USB](wine-usb/README.md) conserva esos tres parches con su licencia, fija el commit público de Wine y verifica hashes de entrada. Prepara únicamente un directorio nuevo. Su prueba compila las dos funciones de asociación extraídas de los archivos parcheados; no compila Wine completo ni acredita un juego funcional.

Su adaptación Bluetooth traduce informes dentro de Wine, concentra las escrituras en un hilo y crea una salida de audio propia para el proceso. El código toma únicamente canales 3/4, los reduce a PCM estéreo de 3 kHz y transmite bloques de 64 frames estéreo (128 muestras) mediante informes `0x32`/`0x33`: un bloque representa aproximadamente 21,33 ms. No transmite canales 1/2 como altavoz Opus. MadSense reutiliza bloques de 512 frames a 48 kHz —10,67 ms—, 32 frames hápticos y un bloque Opus de 480 frames. Cambiar de una implementación a otra sin conservar estas funciones sería una regresión.

Los valores de rendimiento y juegos jugados que publica MacGameFix son resultados de sus autores. No son medidas nuestras ni un límite universal de paquetes Bluetooth. Algunos cambios de su cola comprimen muestras cuando se acumula audio; eso también cambia su temporización. Antes de adaptar esa política hay que medir continuidad, carga, jitter, calidad y salida física. [Descripción y límites del motor](https://github.com/MathiasKowoll/MacGameFix/blob/233d4daa8c5c06d2a2b85dee8d720bdc2dc7f65d/engine/README.md).

## Qué exige firma y qué no

La cuenta de pago no es un requisito general para leer un mando USB ni desarrollar una aplicación Mac local. Tampoco concede por sí sola la autorización de HID virtual. Para nuestra publicación global, Apple exige `com.apple.developer.hid.virtual.device`, con firma y aprovisionamiento que la acrediten. CoreHID es una API más reciente, pero mantiene esa exigencia. Ser administrador y escribir la clave en una firma ad-hoc no equivalen a una autorización válida. [Guía oficial de Apple DTS sobre HID](https://developer.apple.com/forums/thread/843327), [autorización HID](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.hid.virtual.device).

| Alternativa | Alcance real | Decisión |
| --- | --- | --- |
| USB físico / GameController / HIDAPI | Acceso al hardware existente; permisos normales según operación | Ruta preferida para conservar compatibilidad nativa |
| Adaptación interna de Wine | Cambia lo que ve el juego Windows dentro de Wine | Investigar como ruta independiente del HID global |
| IOHIDUserDevice / CoreHID | Publica un nuevo dispositivo HID en macOS | Autorización restringida; no crea audio USB compuesto |
| Broker firmado de terceros | El cliente puede ser libre de cuenta Apple; depende de la API del broker | Karabiner ofrece teclado/ratón, no un gamepad arbitrario |
| libvirtualhid de LizardByte | Broker con gamepad y condiciones propias | Licencia y activación comercial; no es equivalente libre sin coste |
| USB/IP mediante IOUSBHostControllerInterface | Emulación USB con otra autorización gestionada | El cliente usbip-macos declara transferencias isócronas pendientes; no resuelve nuestro audio |
| Adaptador físico abierto | Puede presentar un USB compuesto real al ordenador | Otra vía de investigación; requiere hardware y pruebas Mac |

Fuentes: [Karabiner DriverKit](https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice), [libvirtualhid](https://github.com/LizardByte/libvirtualhid), [usbip-macos](https://github.com/karstx/usbip-macos), [Apple sobre autorización del controlador USB](https://developer.apple.com/forums/thread/802495), [DS5Dongle](https://github.com/awalol/DS5Dongle). No se usa desactivación de SIP/AMFI como procedimiento de instalación.

### Comprobación remota de permisos y acceso nativo

La [ejecución HID 41561331892](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41561331892), revisión `e948643`, compiló dos programas independientes, IOHID y CoreHID, en macOS 15/26 y ARM/Intel. Cada uno probó un descriptor mínimo de joystick de 39 bytes y el descriptor actual DualSense de 321 bytes, con usuario normal y root. Hubo 32 negativas de creación sin autorización y 32 terminaciones por firma restringida declarada ad-hoc; ningún dispositivo publicado y ningún timeout. El resultado verde del workflow significa que el experimento se ejecutó, no que el puente funcionara. El error de `IOServiceOpen` aislado no identifica por sí solo la causa; se conserva junto al resultado de las APIs y los registros de AMFI.

**Condición del entorno:** `csrutil status` indicó SIP desactivado previamente en los cuatro runners del proveedor. El experimento no modificó SIP, AMFI ni otras preferencias de seguridad. Las denegaciones ocurrieron en ese entorno; no presentamos las máquinas virtuales como Macs de usuario con configuración de fábrica.

El [inventario USB 41560981214](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41560981214), revisión `9af5ef9`, compiló y ejecutó el programa sin autorizaciones restringidas en las mismas cuatro combinaciones. Superó sus autopruebas y enumeró sin errores. No había DualSense conectado: `physical_dualsense_found=false` y `observed_hardware_behavior=false` en los cuatro resultados.

## Latencia: qué medir antes de optimizar

Conservar USB físico elimina las etapas que añadiría nuestro puente. Eso no demuestra por sí solo que USB tenga menor latencia total que Bluetooth en todos los juegos: también influyen la frecuencia de informes, el juego, el sistema y el firmware.

La ruta Bluetooth de MadSense incluye captura PCM, cola HAL, IPC autenticada, planificación del lector, composición/codificación, cola de escritura HID, transporte y respuesta del mando. Una medida de una de esas etapas no puede anunciarse como latencia total. El banco de `research/README-audio.md` mide la edad desde el callback de origen hasta el retorno del lector, continuidad por muestra, pérdidas, tiempo de llamadas y CPU del proceso. Compara tres pares de 30 segundos: planificación normal/espera solicitada de 5 ms y QoS interactiva/1 ms. Publica también el desfase respecto al tiempo de presentación programado, que puede ser negativo y no es la edad del callback.

Se mantiene un único escritor de efectos por mando. SDL abre HID de forma compartida en Mac; varios escritores pueden sobrescribir modos de salida o desactivar háptica de audio mediante rumble. Una app acompañante no debe emitir efectos de manera periódica mientras un juego ya controla el mando. [HIDAPI de SDL](https://github.com/libsdl-org/SDL/blob/ceaf27ef3a921a13a8627aaa077cd34bbb38408d/src/hidapi/SDL_hidapi.c), [controlador PS5 de SDL](https://github.com/libsdl-org/SDL/blob/ceaf27ef3a921a13a8627aaa077cd34bbb38408d/src/joystick/hidapi/SDL_hidapi_ps5.c).

Un IOProc de entrada virtual o un tap de audio no es una sustitución invisible de la ruta actual: puede añadir permisos de micrófono o grabación de audio del sistema y no crea por sí mismo la asociación con el gamepad. No se amplían colas ni se elimina autenticación para ocultar fallos de planificación. Las medidas deben conservar pérdidas y colas, además de promedios y percentiles.

## Reutilización y licencias

SDL y HIDAPI son referencias de acceso/protocolo; se preservan sus licencias cuando se incorpora código. Los parches de motor de MacGameFix se declaran LGPL 2.1 o posterior, mientras que su aplicación tiene otra licencia. Chiaki-ng es AGPL; su estudio no autoriza copiar código y etiquetarlo simplemente como GPL de MadSense. DS5Dongle publica código MIT. Cualquier adaptación incluye revisión concreta, origen y avisos; no se distribuyen binarios ajenos sin una cadena de fuentes y compilación verificable.

El proyecto `tomarai85/dualsense-bridge` aporta una referencia reciente de micrófono Bluetooth mediante Opus y una salida PortAudio/BlackHole. Sus afirmaciones y scripts no prueban una integración completa en MadSense; además, el micrófono Bluetooth no forma parte de la paridad con nuestra versión Windows actual. [Fuente examinada](https://github.com/tomarai85/dualsense-bridge/tree/9472c8aebcba7c21b0e3a87c2423593c80f66b98).

## Umbral para anunciar una versión equivalente

Las comprobaciones remotas sin mando permiten verificar compilación, firma ad-hoc, ciclo de vida, permisos, protocolos y transporte interno de audio. Para anunciar equivalencia siguen siendo necesarias pruebas con DualSense y Edge reales, reconexión/suspensión, efectos, audio y juegos representativos de ambas familias. Deben registrar versión de macOS, arquitectura, firmware, juego, Steam Input, versión de Wine y medidas comparables con Windows. La ausencia de hardware no se sustituye por capturas de interfaz ni por un workflow verde.
