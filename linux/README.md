# MadSense para Linux

Desarrollo en `feature/linux`, separado de Windows y macOS. La interfaz conserva
el artwork, las frases y los seis catálogos originales de MadSense. La app usa
Avalonia libre y .NET 10; no necesita servicios de pago.

## Arquitectura y alcance

La primera ruta es el cliente USB/IP **del kernel**, `vhci_hcd`, conectado al
servidor local de MadSense. Permite conservar un dispositivo USB compuesto con
HID y audio UAC de cuatro canales. Reutilizamos los informes, las calibraciones,
la traducción Xbox y el motor Bluetooth/Opus/hápticos de la versión Windows.

La aplicación se ejecuta como usuario normal. La carga del módulo, el puerto
virtual y el aislamiento temporal del mando físico requieren un componente de
permisos limitado. Un grab de evdev por sí solo no bloquea hidraw y no basta
para anunciar que se evita la entrada duplicada o los escritores en competencia.

Esta rama está en integración: hay paquetes de desarrollo autocontenidos para
x64 y ARM64, comprobados después de extraerlos. Sus vistas de prueba no abren
mandos. La conexión permanece bloqueada hasta verificar el componente de acceso;
no hay un estado «listo» ficticio. El probe de kernel exporta solamente mandos
sintéticos y nunca inicializa Bluetooth. No es aún un lanzamiento para jugar.

En la revisión `1dd841f`, el
[CI de la aplicación](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41604139572)
pasó en ambas arquitecturas: 380 pruebas compartidas, 82 de plataforma y 15 de
aplicación, las seis interfaces y el ciclo de escritorio de los paquetes extraídos.
Las [tres pruebas del kernel](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41604139381)
pasaron con DualSense, Edge y Xbox; las dos Sony conservaron las 48.000 muestras
por canal en la reproducción ALSA y el audio ISO IN sintético. El workflow global
falló en una compilación independiente de SDL3, que se conserva como fallo.
El [servicio nativo](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41604139844)
pasó diez casos de aislamiento/recuperación y dos reinstalaciones en reposo; la
etapa posterior del motor falló al preparar el dispositivo sintético, antes de
ejecutar C#. La corrección y las pruebas de la aplicación completa son la
siguiente etapa. Ninguno de estos resultados sustituye un mando o un juego real.

El motor de la app ya conecta la sesión del broker con el servicio de emulación,
los ajustes originales y el forwarder Bluetooth. Pausar cancela una adquisición
pendiente. Una denegación de autorización espera otra acción de reanudar, sin
prompts automáticos en bucle. El diagnóstico y el cierre conservan métricas de
las colas e IPC, separadas de cualquier medida física. La implementación y los
límites de aislamiento están en [platform/README.md](platform/README.md).

El escritorio incluye instancia única, abrir/parar por IPC local, bandeja,
inicio de sesión opcional y cierre ante SIGTERM. Si el escritorio carece de
bandeja, la ventana sigue visible y las opciones siguen accesibles en Registros.
Las preferencias usan XDG. El comprobador de actualizaciones solo admite tags
estables `linux-vX.Y.Z` y el archivo exacto para su arquitectura del repositorio
oficial; no ofrece el instalador Windows ni ejecuta descargas. Hasta publicar
una versión Linux válida informa de esa ausencia.

## Secuencia de validación

Los criterios por función y los límites de cada prueba están en
[VALIDATION.md](VALIDATION.md).

1. Protocolo USB/IP: conservar la variante Windows y probar los paquetes ISO
   cortos, vacíos y con huecos con el formato de Linux.
2. Kernel real: importar una persona propia DualSense, Edge y Xbox; comprobar
   descriptores, controlador del kernel, evdev, hidraw y audio ALSA de cuatro
   canales. Comparar PCM por muestra y conservar pérdidas y errores.
3. Componente de permisos: acceso exclusivo al físico, attach/detach solo propios,
   restauración al cerrar, desconectar, fallar o morir el cliente. Pruebas con
   dispositivos sintéticos antes de tocar hardware.
4. Motor de producto: modo nativo/Xbox, informes y sensores, efectos, audio,
   reconexión, suspensión, auriculares, tres niveles de altavoz, preferencias,
   bandeja, inicio de sesión, singleton y cierre completo.
5. Consumidores nativos y Wine/Proton: identificación común de HID/audio,
   propiedades y salida cuadrafónica, compatibilidad por juego y Steam Input.
6. Hardware real: DualSense y Edge, continuidad, latencia de extremo a extremo
   y comparación con Windows. Una VM o una prueba sintética no suple esta etapa.

## Compilar y comprobar

```sh
dotnet build linux/app/MadSense.Linux.csproj -c Release
dotnet run --project linux/app/MadSense.Linux.csproj -- --start-paused
```

`--preview` no abre controladores ni guarda preferencias. `--ui-smoke DIRECTORIO
--language es` genera una captura y evidencia de una ventana real y la cierra.
El workflow `Linux app` ejecuta estas vistas en X11 sobre Linux x64 y ARM64.
Después empaqueta los mismos binarios autocontenidos, las fuentes de la revisión,
las licencias y un manifiesto SHA-256. Extrae cada archivo y vuelve a ejecutar
la interfaz y el ciclo de escritorio desde él. Comprueba también la revisión
incrustada en el ejecutable: no acepta un binario anterior bajo un manifiesto nuevo.
Los archivos `MadSense-linux-*-development-*.tar.gz` son builds de desarrollo,
no una certificación de compatibilidad con juegos. Incluyen `RUNNING.txt` y
un instalador separado del servicio; ejecutar la interfaz no instala el helper.
`Linux kernel USB-IP neutral integration` prueba el kernel en máquinas desechables;
su script rechaza ejecutarse como prueba automática sobre un PC de usuario.

Dependencias de interfaz en Debian/Ubuntu: libX11, libICE, libSM, fontconfig,
FreeType y DejaVu. La ruta HID usa `libhidapi-hidraw`; la ruta kernel necesita
`vhci_hcd`, `snd_usb_audio`, `hid_playstation`, `xpad` y las utilidades usbip.
Los resultados del CI deben confirmar su disponibilidad, no se presupone que
toda distribución incluya los módulos.

## Fuentes y alternativas

- [Protocolo USB/IP del kernel](https://docs.kernel.org/usb/usbip_protocol.html):
  admite transferencias isócronas; eso no demuestra que cualquier servidor
  externo implemente bien su empaquetado o planificación.
- [Kernel examinado, revisión 6628e7d](https://github.com/torvalds/linux/tree/6628e7d4c6b25c8d18f1345e3a20d21829aae7e4/drivers/usb/usbip):
  `vhci_rx.c` y `usbip_common.c` esperan datos útiles ISO IN compactos y restauran
  los offsets. Linux habilita `PackIsoInPayload`; Windows conserva su formato.
- [hid-playstation](https://github.com/torvalds/linux/blob/6628e7d4c6b25c8d18f1345e3a20d21829aae7e4/drivers/hid/hid-playstation.c):
  requiere los feature reports de emparejamiento, firmware y calibración.
  Conservamos la dirección virtual propia `02:D5`, distinta de la física.
- [UHID](https://docs.kernel.org/hid/uhid.html) permite HID virtual en espacio de
  usuario, pero no crea por sí solo la función de audio USB. Sigue siendo una
  alternativa HID; un sink PipeWire separado no acredita identidad compuesta.
- [Wine de Proton, revisión b8fdff8](https://github.com/ValveSoftware/wine/tree/b8fdff8e1f855b5276ec4ddca0f31b2792554322):
  `winebus.sys/bus_udev.c` transporta hidraw y `winepulse.drv/pulse.c` obtiene
  identidad USB y ContainerId del árbol del sistema. Esto favorece la vía USB
  compuesta; falta comprobar la integración y juegos, no basta leer el código.
- [Avalonia en Linux](https://docs.avaloniaui.net/docs/platform-specific-guides/linux):
  X11 es la ruta inicial y puede funcionar mediante XWayland. No se anuncia
  una prueba de Wayland nativo a partir de un lanzamiento en Xvfb.

La versión Windows no expone captura de audio por aplicación ni inyección de
micrófono: no se presentan esas capacidades internas de la biblioteca como
funciones del producto ya portadas. Tampoco se anuncia «latencia mínima» a
partir de un temporizador de 1 ms; hacen falta colas, percentiles y medidas físicas.
