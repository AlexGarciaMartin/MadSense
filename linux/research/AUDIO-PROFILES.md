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
