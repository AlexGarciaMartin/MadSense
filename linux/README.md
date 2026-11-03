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

En la revisión `40e2de1`, el
[CI de la aplicación](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653899459)
pasó en ambas arquitecturas: 429 pruebas compartidas, 100 de plataforma y 18 de
aplicación, las seis interfaces y el ciclo de escritorio de los paquetes extraídos.
Las [tres pruebas del kernel en `fc141e9`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41629966377)
pasaron con DualSense, Edge y Xbox, incluida la retirada de la VM y restauración
de permisos KVM. Las dos Sony conservaron las 48.000 muestras por canal en la
reproducción ALSA y el audio ISO IN sintético.
El [servicio nativo en `40e2de1`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653899463)
pasó diez casos de aislamiento/recuperación, dos actualizaciones en reposo y
cuatro casos de desinstalación/reinstalación. El núcleo pasó
sus nueve casos: identidad y calibración física del fixture, entrada completa,
RGB, audio ALSA de cuatro canales y cierre/restauración confirmados. El
controlador real de la app pasó sus diez casos de conexión y ciclo de vida.
La enumeración Linux admite un mando presente pero todavía silencioso y mantiene
el requisito de recibir entrada antes de Ready. Después pasaron una prueba
adicional del núcleo y seis de la app para gatillos, auriculares y controles
DualSense Edge. Una sesión nueva de la app pasó además sus diez casos y el
[ensayo de vibración Xbox](tests/app-integration/XBOX-FF.md): un consumidor evdev
ordinario envió dos intensidades distintas y una parada explícita; ambos informes
Bluetooth llegaron con CRC válido antes de borrar el efecto o cerrar el dispositivo.
Todas estas pruebas usan un fixture sintético en una VM propia;
no certifican el funcionamiento de un mando físico.

La [primera ejecución SDL/Wine](research/CONSUMER-RUN-7d9f906.md) ya aporta
capturas: SDL2/SDL3 reciben entrada y envían efectos; su parada explícita limpia
los selectores. En [el ensayo `d8f3ca0`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41634461320)
pasaron los cuatro casos SDL, con el informe USB completo y el Bluetooth con CRC
válido, incluida la fase de parada separada de LED/cierre. Eso verifica reenvío,
no actuación física. La corrección de la carrera al inspeccionar descriptores
permitió cerrar Wine y completar la limpieza del núcleo y de ambos consumidores.
La prueba real de reutilización de descriptores también pasó en x64 y ARM64.
La variante Wine UDEV recibió las características nativas y Raw Input completo;
sus marcadores RGB llegaron a USB y Bluetooth. La escritura asíncrona termina
correctamente pero informa de 47 bytes frente a los 48 solicitados. La captura
independiente contiene los 48 bytes USB y el informe Bluetooth íntegro. Se
conserva el fallo del contador: todavía no acredita equivalencia con el contrato
de la API de Windows. Wine 9 tampoco publica el ContainerId necesario para asociar
HID/audio en nuestra prueba. El diagnóstico detallado y las diferencias con
Proton están en [WINE-CONSUMER.md](research/WINE-CONSUMER.md).
Wine 9 predeterminado falla antes de verificar las características nativas HID;
no se ha validado WASAPI.
La [repetición en `0325da7`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41628578603)
registra capacidades de entrada de 15 bytes y cero bytes de salida y de informes
de características en Wine 9 predeterminado. Se investiga por separado su ruta
UDEV/hidraw, conservando este fallo de la configuración predeterminada.
Los observadores privados de PipeWire y Pulse completan la colección y limpieza.
La [medición de ganancia PipeWire](research/PIPEWIRE-GAIN-EXPERIMENT.md) confirmó
una interacción del mezclador: elevar únicamente las traseras a unidad conserva
sus 48.000 muestras exactas, pero reduce las delanteras de aproximadamente 0,904
a 0,064. La restauración recuperó exactamente el PCM inicial de los cuatro
canales, el grafo, ALSA y los permisos. Por ello ese ajuste aislado no se aplica
como política del producto. En `0a9c30d`, una solicitud que preserva los coeficientes
de software delanteros conservó exactamente sus 48.000 muestras por canal y
entregó las traseras a unidad. La restauración de PCM, grafo, ALSA y permisos
también pasó; sigue siendo una prueba privada, sin política automática instalada.
El [ensayo de aparición del dispositivo](tests/audio-hotplug/README.md), ya con
el control de selección explícita corregido en `0a9c30d`, registró dos conexiones
en las que WirePlumber sustituyó tanto el altavoz como el micrófono automáticos
por el virtual. En la sesión con selección explícita conservó ambos dispositivos
en sus dos conexiones. Se investiga una política limitada al dispositivo
virtual de la sesión, aplicada antes de publicar sus nodos de audio. El
[prototipo privado de exportación en `40e2de1`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653922705)
ya conserva ambos dispositivos predeterminados: una regla identifica el puerto
y una cadena aleatoria de la interfaz AudioControl antes de descubrir la tarjeta;
un cliente normal publica únicamente el altavoz de cuatro canales, con prioridad
baja. Pasaron dos reconexiones, cierre forzado, recreación y cierre normal. Los
controles con otro puerto o cadena no reciben esa política. No cambia el número
de serie USB ni HID. Todavía es un experimento: faltan la comprobación del PCM
de este exportador y su integración recuperable con el servicio de producto.
El alcance y las fuentes están en [la prueba de exportación](tests/audio-hotplug/export/README.md).
Estos resultados no equivalen a un escritorio ni a juegos reales.

Una [prueba aislada de bloqueo del grupo de hilos](platform/tests/starvation/README.md)
reproduce la dependencia del cliente anterior: con su único worker ocupado por
una operación HID síncrona, tampoco podía recibir la respuesta. La corrección
separa el lector y las esperas síncronas de ese grupo, conservando los límites
de cola. Los doce casos de control/restricción pasan en Linux x64 y ARM64,
incluido el cierre con ACK seguido inmediatamente por EOF. No son medidas de
latencia física.

El [análisis independiente del audio en `fc141e9`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41629966487)
pasó en ambas arquitecturas: Opus decodificado con libopus y hápticos contrastados
byte a byte, incluyendo controles que rechazan paquetes corruptos o reordenados.
Es una prueba del pipeline de audio sin transporte físico; no mide la latencia
del mando. La [primera medición sintética del puente](tests/latency/README.md)
recibió 1.000/1.000 estados a 100 Hz y 9.954/9.998 a 1.000 Hz. Conserva las 44
pérdidas y dos plazos de generación omitidos como límites de ese ensayo.
Una [repetición con diagnóstico en `adccde1`](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41641527528)
entregó todos los informes escritos en sus cuatro casos de 100 Hz. Sus dos
controles de 1.000 Hz terminaron antes de tiempo por desbordamiento de la cola
de ocho informes del cliente. Las sesiones instrumentadas completaron los diez
segundos y localizaron sus pérdidas entre el último estado almacenado y la
respuesta de interrupción USB. La instrumentación puede alterar la ejecución;
estos resultados no prueban por qué el consumidor dejó de drenar la cola ni
justifican aumentarla sin más. El cierre y la restauración pasaron en los ocho
casos. La [observación del servicio en `00484dc`](tests/latency/HELPER-TRACE.md)
reprodujo por separado desbordamientos en el servicio y en el cliente. En un caso
sin recolecciones GC, una revisión síncrona de dispositivos ocupó 7,506 ms antes
de una ráfaga de nueve informes que llenó la cola del cliente. Se está corrigiendo
el control de flujo; no se han ampliado los límites ni ocultado los informes perdidos.
No se ha medido Bluetooth físico: ninguno de estos resultados sustituye
un mando o un juego real.

La [captura del bloqueo de salida HID](research/KERNEL-WAIT-OBSERVER.md)
localizó una espera de cinco segundos al adquirir el bloqueo compartido de
hidraw. Linux ahora confirma la recepción completa de la interrupción USB antes
de reenviarla al físico, conservando el orden sin añadir una cola. La
[repetición completa en Debian](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653912192)
pasó los cuatro casos SDL, entregó sus 7.718 informes sin desbordamientos y
confirmó la limpieza de los consumidores y el núcleo; no reapareció aquella
espera. El ensayo sigue fallando por contratos de Wine y por el lanzamiento de
GE-Proton todavía no validado. Una ejecución GE independiente volvió a saturar
la cola del servicio durante sus requisitos previos, por lo que el problema de
flujo de entrada sigue abierto. La ruta de control USB `SET_REPORT` también
requiere validación específica y no se modifica con esta corrección.

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
La investigación de perfiles UCM, PipeWire y audio para Wine está en
[research/AUDIO-PROFILES.md](research/AUDIO-PROFILES.md).

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
`vhci_hcd`, `snd_usb_audio`, `hid_playstation` y `xpad`, según el modo.
El servicio usa Python 3.10+, systemd 254+, polkit, udev, kmod y herramientas ACL;
la sesión gráfica necesita un agente de autenticación polkit. El mando Bluetooth
se empareja y conecta desde el sistema operativo. Las utilidades `usbip` y el
decodificador independiente libopus pertenecen a los ensayos; el puente de
producto adjunta mediante la interfaz del kernel y usa su encoder administrado.
Los resultados del CI deben confirmar la disponibilidad de los módulos, no se presupone que
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
- [uinput](https://docs.kernel.org/input/uinput.html) permite publicar eventos de
  entrada y capacidades evdev. Es una alternativa para controles y vibración;
  de esa interfaz no se obtiene el dispositivo compuesto HID/audio ni el
  intercambio de informes de características de DualSense.
- [FunctionFS](https://docs.kernel.org/usb/functionfs.html) permite implementar
  funciones USB en espacio de usuario, pero necesita el lado gadget/UDC. El
  bucle local `dummy_hcd` parece atractivo para evitar el transporte USB/IP;
  sin embargo, [el código de Linux 6.8](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/gadget/udc/dummy_hcd.c)
  excluye las transferencias isócronas. Por eso no sustituye directamente la
  ruta de audio UAC de este port. Añadir ese soporte sería otro desarrollo de
  kernel, no una mejora de latencia demostrada. Un gadget sobre un UDC físico
  es otro escenario y requiere hardware; no lo hemos ejecutado.
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
USB/IP es la ruta con evidencia de HID y audio compuesto en este trabajo, no una
afirmación de que sea el único enfoque posible. Los adaptadores preparados de
[PipeWire](research/PIPEWIRE-DEFAULTS-OBSERVER.md) y
[GE-Proton/UMU](research/GE-UMU.md) conservan versiones, fuentes y resultados
separados; sus pruebas de preparación no equivalen a compatibilidad en juegos.
