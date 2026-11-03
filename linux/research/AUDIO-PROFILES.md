# Audio nativo, perfiles y compatibilidad con Wine

Las pruebas del kernel demuestran que la persona USB compuesta entrega los cuatro
canales a ALSA sin cambiar muestras. Todavía hay otra frontera: el perfil que
PulseAudio o PipeWire presenta al juego. Una reproducción directa con `aplay -D
hw:...` evita esa capa y no puede validarla.

## UCM no implica que haya que eliminar perfiles

El proyecto ALSA incorporó un caso **Direct** de cuatro canales precisamente para
Wine, y lo coloca primero. El
[cambio 31df87b](https://github.com/alsa-project/alsa-ucm-conf/commit/31df87b5d77f3e02b6bdf61b4974654fdaaf9597)
explica el problema de presentar los canales separados. Su
[configuración Sony](https://github.com/alsa-project/alsa-ucm-conf/blob/31df87b5d77f3e02b6bdf61b4974654fdaaf9597/ucm2/USB-Audio/Sony/DualSense-PS5.conf)
y la [macro DirectUseCase](https://github.com/alsa-project/alsa-ucm-conf/blob/31df87b5d77f3e02b6bdf61b4974654fdaaf9597/ucm2/common/directm.conf)
seleccionan el PCM hardware con cuatro canales de reproducción, sin el split del
perfil de altavoz/auriculares.

La incidencia posterior
[#828](https://github.com/alsa-project/alsa-ucm-conf/issues/828) está cerrada como
duplicada. En su
[respuesta](https://github.com/alsa-project/alsa-ucm-conf/issues/828#issuecomment-5241056989),
el mantenedor indica usar Direct y mantener el volumen de los canales hápticos
al 100 %. Por tanto, borrar configuraciones UCM globales no es nuestra solución
por defecto. Primero hay que identificar la versión instalada, el caso/perfil
activo, los canales expuestos y sus volúmenes.

La configuración inicial de UCM también depende de controles de jack añadidos
en kernels posteriores al 6.8 usado por nuestro banco actual; el
[cambio de introducción](https://github.com/alsa-project/alsa-ucm-conf/commit/36a111ad007f2f424e6b00155970aaf07e86cee3)
describe esa relación. Validar el kernel 6.8 no cubre automáticamente una sesión
de escritorio moderna con esos perfiles.

## Qué prueba el primer consumidor Wine

El experimento usa una sesión PulseAudio privada y configurada: cuatro canales,
48 kHz, volumen completo y buffers conservadores. Conserva esa configuración
en sus artefactos. Busca el dispositivo virtual exacto y exige la asociación
ContainerId entre HID y audio; no acepta el endpoint predeterminado ni una
coincidencia de nombre. Si WASAPI acepta el formato, aún debe comprobarse el PCM
recibido por USB/IP y sus canales hápticos.

Ese resultado no acredita los valores por defecto de un escritorio, PipeWire,
Proton ni la menor latencia posible. Los buffers de esa sesión son una condición
de prueba explícita, no una recomendación de producto.

Los siguientes casos deben ejecutar una sesión con configuración de distribución
y otra con PipeWire/WirePlumber identificados. Deben conservar versión de UCM,
perfil, mapa y volumen de canales, identidad USB/ContainerId, PCM entregado y
estado antes/después. Si hace falta seleccionar Direct, la operación debe quedar
limitada a la tarjeta virtual propiedad de esa sesión de MadSense. También hay
que comprobar que crearla no sustituye la salida de audio predeterminada del
usuario. Las pruebas usarán salidas sintéticas propias, sin altavoces físicos.

## Ganancia y llegada de dispositivos: resultados y siguientes comprobaciones

El [ensayo nativo d8f3ca0](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41634461320)
midió las muestras de los cuatro canales, no sólo los valores de los controles.
El baseline PipeWire entregó una ganancia aproximada de 0,904. Al llevar los
volúmenes traseros a 1, sus muestras llegaron exactas, pero la ganancia delantera
cayó a aproximadamente 0,064 aunque sus valores raw no habían cambiado. El
mezclador ALSA común pasó de 77 a 100 y cambió la compensación software. La fase
de restauración recuperó el PCM baseline byte a byte y los controles previos.
[PIPEWIRE-GAIN-EXPERIMENT.md](PIPEWIRE-GAIN-EXPERIMENT.md) conserva el detalle.
Ese ajuste rear-only no es una política válida del producto: la corrección debe
medir y preservar el audio delantero además de los hápticos.

La [documentación de prioridades de WirePlumber](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/priorities.html)
explica que la elección automática depende de `priority.session`; una selección
guardada del usuario tiene preferencia. Por ello son casos distintos y ambos
necesitan una prueba de llegada y retirada. Los defaults de reproducción y
captura se eligen por separado: también hay que observar el micrófono. No se
debe concluir que se conserva el default sólo porque MadSense no lo escriba.

Las [reglas del monitor ALSA](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/alsa.html)
permiten configurar propiedades al crear dispositivos y nodos. Son una posible
vía para reducir la prioridad del virtual sin deshabilitar su salida para los
juegos. Una regla de producto debe identificar el virtual de MadSense con
precisión y cubrir la versión de WirePlumber instalada; una coincidencia
genérica por Sony/VID/PID también alcanzaría mandos USB físicos y no sirve.
Todavía no se instala ninguna regla global ni se cambia la elección del usuario.

## Alternativa libre que merece comparación

[dualsense-neo, revisión 8af7898](https://github.com/forgerpl/dualsense-neo/tree/8af78981115167affec777a134070fce7e90ab3d)
expone nodos PipeWire directamente y documenta audio/hápticos Bluetooth mediante
Opus e informes HID. Ofrece licencias MIT o Apache-2.0 y atribuye el trabajo de
protocolo de SAxense y DS5Dongle. Su documentación es una referencia adicional
para investigar cadencia, resampling y rutas de audio; no sustituye medidas
propias con hardware. No hemos ejecutado ni incorporado su código.

Unos nodos PipeWire separados pueden servir para reproducir audio o alimentar
hápticos directamente. Eso no demuestra que un juego vea el mismo dispositivo
HID/audio que espera del DualSense USB. La vía USB/IP conserva esa identidad
compuesta y ya tiene evidencia en el kernel; la comparación con nodos separados
debe incluir el descubrimiento real por Wine/juegos, no solo sonido disponible.
