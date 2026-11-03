# Criterios de validación de MadSense Linux

La referencia de producto es MadSense Windows: sus modos, ajustes, textos y
comportamiento visible. La implementación compartida reduce diferencias, pero
no acredita por sí sola que las interfaces del sistema operativo funcionen.
El estado de cada ejecución está en los README de investigación, plataforma e
integración; los fallos y sus artefactos se conservan.

## Gates y pruebas que deben aportar

| Área | Evidencia necesaria |
|---|---|
| Identidad y escritorio | Artwork y seis idiomas originales; capturas reales x64/ARM64; bandeja o ventana accesible, singleton, inicio de sesión y cierre desde el paquete extraído. |
| Aislamiento | Negar hidraw extranjero, lectores FF y escritores evdev; permitir lectores de sensores; retener todos los grabs tras SIGKILL; restaurar permisos sin afectar otro mando/importador. |
| Instalación | Rechazar estado pendiente; actualizar únicamente tras cerrar el servicio; desinstalar conservando archivos ajenos y cambios locales; reinstalar con preflight inactivo explícito. |
| Controles y sensores | Informe Bluetooth marcado que atraviese el parser real y llegue al HID/evdev virtual con botones, ejes, movimiento y touchpad esperados. |
| DualSense Edge | Enumeración y selección automática de variante desde un fixture Edge; mantener las extensiones del informe; después comprobar mandos Edge reales. Una persona USB Edge aislada no sustituye esta prueba de la app. |
| Modo Xbox | Cambiar Auto→Xbox→Auto desde el controlador real de la app; comprobar VID/PID, controles y rumble solicitado por un consumidor independiente. |
| LED y gatillos | Marcadores de RGB y efectos L2/R2 solicitados al HID USB virtual, observados en los campos correspondientes del informe Bluetooth, con CRC válido y neutralización al cerrar. |
| Hápticos nativos | PCM cuadrafónico ALSA exacto a la entrada USB/IP; canales traseros correctos; bloques Bluetooth de 3 kHz contrastados con la fuente. No basta encontrar bytes distintos de cero. |
| Altavoz | Alto/Bajo/Off reales, campos de volumen/pre-gain entregados y paquetes Opus decodificados con una implementación independiente; documentar el resampling y la alineación del codec. |
| Auriculares | Cambio del estado físico sintético y de la ruta/volumen correspondiente, sin mezclar el audio de altavoz con el de auriculares. La detección real del jack necesita hardware. |
| Ciclo de conexión | Ready solo tras recibir entrada; pausa y Dispose solo tras cleanup confirmado; desconexión/reconexión; cancelación de autorización y de features; helper perdido o reiniciado sin falsa disponibilidad. |
| Consumidores nativos | SDL2, SDL3 y evdev/FF como usuario ordinario; entrada marcada y salida correlacionada con el dispositivo virtual exacto, no solo un retorno de API satisfactorio. |
| Wine | HID/Raw Input/XInput, identidad compartida de HID/audio y salida WASAPI cuadrafónica; prefijo y servidor de audio propios, consumidores sin acceso al fixture físico. |
| Proton | Ejecutar e identificar un runtime oficial concreto. Un build de Wine o la lectura de fuentes Proton no demuestra el comportamiento de Proton. |
| Rendimiento | Medir secuencias UHID→HID virtual, pérdidas/coalescing, percentiles, CPU y carga de audio; separar calentamiento, tasas de entrada y entorno. Confirmar después latencia física contra Windows. |

Los experimentos sintéticos usan exclusivamente una VM propia identificada por
UUID y marcadores, sin radio ni passthrough de hardware. La app y sus consumidores
se ejecutan como usuarios ordinarios. Las autorizaciones automáticas del fixture
no forman parte del instalador ni de las políticas del producto.

## Orden de trabajo

1. Cerrar los fallos de infraestructura conservando sus resultados originales.
   La aceleración de CI debe verificar tanto KVM real como la restauración de
   sus permisos; no se presenta como una mejora de latencia del mando.
2. Pasar servicio, motor y controlador de la app con las mismas fuentes instaladas
   y compiladas, registrando hashes y verificando cada cleanup.
3. Ampliar la evidencia de salida a gatillos, auriculares y audio decodificado;
   cubrir Edge desde la selección real de la aplicación.
4. Ejecutar SDL/FF y Wine, después el runtime Proton identificado. Cualquier
   configuración alternativa debe quedar separada del resultado por defecto.
5. Medir antes de cambiar colas, batching o planificación. El contador de cola
   del cliente no mide todo el tiempo en sockets/kernel, y el período del timer
   no es la latencia total.
6. Distribuir los binarios que hayan pasado sus comprobaciones, con fuentes y
   licencias. Mantener la etiqueta de desarrollo mientras falte validación física
   y con juegos; no convertir una prueba sintética en una promesa de paridad.

La conexión física USB conserva la ruta nativa del sistema, igual que el alcance
del producto Windows. El puente de MadSense traslada Bluetooth a USB virtual.
Captura de audio por aplicación e inyección de micrófono no son funciones de
producto de Windows; no se añaden a la lista por existir clases internas en la
biblioteca. El ISO IN sintético comprueba el transporte, no un micrófono físico.
