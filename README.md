# MadSense 0.4

Abre MadSense, enciende tu DualSense y abre el juego. La app espera al mando, conecta por Bluetooth y recupera la conexión automáticamente. Debe estar emparejado previamente en Windows. Esta versión controla un mando y es 100 % Bluetooth: por cable el juego usa el DualSense en nativo sin necesitar MadSense.

## Novedades de 0.4

- **Puente por eventos.** Pausar, reanudar y cambiar de modo se ejecutan al instante (antes podían esperar hasta un segundo). Windows avisa cuando aparece o desaparece un dispositivo y MadSense reintenta en ese momento: desde abrir el exe hasta "Listo para jugar" se midieron 1,0 s. Si el mando se apaga o pierde el enlace, la lectura falla y el puente se libera al momento en vez de esperar cinco segundos de silencio.
- **Prioridad de tiempo real.** El proceso corre en prioridad "por encima de lo normal" y el lector de entrada y la bomba de audio/hápticos Bluetooth se registran en MMCSS (las mismas clases de planificación que usa el audio profesional en Windows), para mantener la cadencia aunque el juego sature la CPU. La bomba corre en hilo propio y espera cada tick con un temporizador de alta resolución (0,27 ms de media de desvío medido) en vez de girar el último milisegundo.
- **La ventana te dice qué está haciendo el juego.** En la franja LINK hay tres lámparas: HÁPTICOS (el juego envía vibración nativa), GATILLOS (hay un efecto adaptativo aplicado) y AUDIO (el juego tiene abierto el altavoz/hápticos del mando). En modo Xbox, VIBRACIÓN. La línea inferior lleva hora y registra "Listo para jugar", la pausa y los cambios de auriculares.
- **Batería.** La bandeja muestra el nivel y avisa una vez por ciclo al bajar del 15 %.
- **Iniciar con Windows** desde el menú de la bandeja: arranca oculto (`--tray`) y conecta el mando sin que tengas que abrir nada.
- Si el DualSense está enchufado por USB, lo dice claramente en vez de "esperando": por cable ya va en nativo.
- Corregido un test del núcleo que fallaba por el nombre de la opción `strict_native_haptics` en `controllers.json` (ahora en el mismo estilo que el resto).
- **Latencia háptica medida de verdad y recortada 30 ms.** `tools/HapticLatencyProbe` manda un impulso háptico (por el DualSense virtual, como un juego, o directo al mando) y detecta la sacudida en el acelerómetro del mando físico: latencia real hasta el actuador, sin equipo externo. Con ella se descubrió que el quinto byte del bloque de sesión Bluetooth fija cuánto háptico retiene el firmware del mando antes de sonar: con el valor de referencia que envían todos los puentes (0x40) el mando tarda 74 ms desde que recibe el informe; con 0x20, 42 ms y la misma continuidad. MadSense envía ahora 0x20 en ese byte (los cuatro del altavoz siguen en 0x40). Camino completo juego → actuador: 72-79 ms de mediana frente a 109-114 antes y 117-120 de DSX 3.2 Beta02, mismo mando y misma noche. `MADSENSE_BT_HAPTIC_BUFFER=0x10` da 67 ms pero corta un 4 % de la vibración continua; por eso no es el valor por defecto. Estudio completo en `evidence/comparison/haptic-latency-audit.txt`.
- **Altavoz escuchado y al volumen de DSX (01-11-2026).** El altavoz por Bluetooth se oyó por primera vez con `HapticLatencyProbe --speaker-test`, que manda pitidos por el DualSense virtual igual que un juego, sin tocar el mando físico. Sonaba bajo: MadSense enviaba 0x50 en el byte de volumen y el mando admite hasta 0x64; DSX guarda 100 (0x64). Ahora se envía 0x64. Aun así el parry sonaba más bajo que con DSX: una escalera de tonos directa al mando mostró que la palanca real es la preganancia del altavoz (audio control 2, de 0 a 7), que la librería dejaba en 2; MadSense envía 7 (`speaker_pre_gain` en `controllers.json`). El audio que llega mientras el canal calienta se recorta a los tres bloques más recientes en vez de reproducirse troceado. Queda un chasquido al abrir el canal del altavoz coincidiendo con sonido (el mando enciende el amplificador); con el canal ya abierto no se produce, y un juego lo abre con silencio al arrancar. Detalle en `evidence/speaker-audit.txt`.
- **Nivel del altavoz desde la ventana y la bandeja.** Fila ALTAVOZ DEL MANDO con ALTO, BAJO y APAGADO; el mismo menú en el icono de la bandeja. Cambia al instante, sin reconectar ni reiniciar el juego, y se recuerda. Cada nivel es una pareja fija de volumen y preganancia del mando (ALTO 0x64/7, BAJO 0x64/2, APAGADO 0/0). La preganancia alimenta el compresor del altavoz, así que sus pasos no son uniformes: un nivel intermedio (4) se probó y sonaba casi como ALTO, por eso hay tres niveles con hueco real y no un deslizador. En modo Xbox la fila se desactiva. `preferences.json` guarda ahora `{"mode","speaker"}`; el fichero antiguo con solo el modo se sigue leyendo.

Detalles y mediciones en `RESULTADOS.txt`, `evidence/v04-validation.txt`, `evidence/comparison/haptic-latency-audit.txt` y `evidence/speaker-audit.txt`.

## Instalación en un PC nuevo

Necesitas Windows 10 u 11 de 64 bits, Bluetooth y un DualSense. MadSense lleva dentro el runtime .NET; lo único externo son dos controladores de sistema, y el instalador se ocupa de ellos:

- **USBip** (usbip-win2 0.9.8.1, BSD-2): presenta el DualSense virtual por USB.
- **HidHide** (Nefarius, MIT): oculta el mando físico para que el juego no lo vea dos veces.

1. Descarga `MadSense-0.4-win-x64.zip` de la página de *Releases* y descomprímelo entero.
2. Ejecuta **`Instalar.cmd`** y acepta el aviso de administrador. Instala los controladores que falten descargándolos de sus GitHub oficiales con la huella SHA-256 comprobada, copia MadSense a `%LOCALAPPDATA%\Programs\MadSense` y crea los accesos directos en Inicio y Escritorio.
3. Si ha instalado HidHide, reinicia Windows.
4. Empareja el DualSense por Bluetooth en Windows (Configuración → Bluetooth y dispositivos).

Para desinstalar: `Desinstalar.cmd`. Los controladores se quitan desde Configuración → Aplicaciones. Opciones avanzadas (`-SkipDrivers`, `-Target`) en `setup/install.ps1`. Si prefieres no instalar, también vale descomprimir y abrir `app/MadSense.exe` desde una carpeta donde puedas escribir, con los dos controladores instalados a mano.

## Uso

1. Cierra DSX, SenseBridge u otro programa que controle el mismo mando.
2. Abre MadSense (menú Inicio, o `app/MadSense.exe`).
3. Pulsa PS en el mando. Espera **Listo para jugar**.
4. Abre el juego. Cerrar o minimizar la ventana deja MadSense en la bandeja.
5. **Pausar** libera el mando. Para salir completamente: botón derecho en el icono de la bandeja → **Salir y liberar el mando**.

MadSense no necesita administrador para funcionar, solo el instalador para los controladores. No modifica la firma de controladores ni toca Steam.

## Compilar y empaquetar

Con el SDK de .NET 10: `build.ps1` publica a `app/`, `test.ps1` pasa las pruebas (`-Hardware` incluye las físicas, con el mando conectado y MadSense cerrada) y `tools/build-release.ps1` genera el zip instalable en `dist/` con su SHA-256. Cómo subirlo a GitHub: `PUBLICAR.md`.

## Qué modo elegir

| Modo | Comportamiento | Para qué sirve |
|---|---|---|
| DualSense · recomendado | Conexión automática; presenta un DualSense USB virtual estable | Funciones nativas; Steam Input puede cubrir los juegos sin soporte |
| Mando Xbox (XInput) | Presenta un Xbox 360 mediante XInput | Juegos XInput, especialmente fuera de Steam; botones, sticks y vibración convencional |

**La conexión automática no detecta universalmente el soporte de cada juego ni modifica la configuración de Steam.** Configura en Steam la compatibilidad PlayStation para juegos sin soporte; desactiva Steam Input en las propiedades de los juegos cuyo soporte DualSense nativo quieras usar. Si un juego fuera de Steam solo acepta Xbox, selecciona Mando Xbox antes de abrirlo. La selección se recuerda.

Un juego no necesita venir de PS5 para aceptar el mando. Lo decisivo es la API y las funciones que implementa. En Xbox se pierden háptica nativa, gatillos adaptativos, altavoz y coordenadas táctiles; no se inventan esos efectos. Steam Input puede mapear funciones adicionales según su perfil. Los juegos exclusivamente de teclado/ratón necesitan un mapeo externo; MadSense no incorpora macros.

Cambia de modo **antes** de abrir el juego. No se cambian identidades al detectar procesos ni se publican dos mandos simultáneamente: muchos juegos fijan el dispositivo al arrancar, y ambas técnicas pueden provocar desconexión o doble entrada.

## Cambios frente a SenseBridge

- Nombre, logo vectorial e interfaz nativa MadSense: estética de fanzine en marfil/tinta/naranja, trama de puntos, ilustración y bandeja. Sin navegador integrado ni paquetes gráficos adicionales. La animación se detiene al ocultarse.
- Arranque automático, espera normal cuando el mando está apagado, reintentos y recuperación de la creación virtual atascada. Trabajo de conexión fuera del hilo de interfaz.
- Xbox/XInput además del DualSense nativo. Preferencia persistente e instancia única: volver a abrir la app recupera la ventana existente.
- Calibración real del giroscopio/acelerómetro reenviada al mando virtual. Passthrough de entrada sin remapeos que eliminen el estado físico de gatillos o sensores.
- Calentamiento de audio sin dormir con el bloqueo de callbacks adquirido; sigue aceptando hápticos y actualizaciones de gatillos/luces mientras se prepara el audio.
- Audio ocioso despertado por datos, en lugar de consultar cada 2 ms; temporización de alta resolución liberada cuando deja de ser necesaria.
- Eliminado el formateo hexadecimal de cada paquete cuando no se registra Trace. Registros acotados y fallos de escritura no fatales.
- Cola háptica nativa continua: respeta fragmentos USB, silencio e impulsos cortos. No convierte el sonido del altavoz en háptica automáticamente ni aplica un umbral artificial de silencio.
- Limpieza protegida de recursos y registro de recuperación de los cambios propios de HidHide, incluido el estado global anterior. El estado listo exige ocultación verificada.

## Qué queda sin garantizar

No se ha demostrado equivalencia total con DSX ni PS5, ni compatibilidad del 100 % de juegos. Las pruebas automatizadas validan protocolo, Windows y escrituras al hardware; no sustituyen sentir el mando o medir con equipo externo. El altavoz se ha escuchado con pitidos de prueba, no todavía con el sonido de un juego.

La conversión Bluetooth utiliza Opus para audio y 3 kHz/8 bits para hápticos. El altavoz mantiene ocho paquetes Opus de calentamiento; los hápticos nativos ya se envían desde el primer paquete. No es audio USB bit a bit. Las colas son acotadas y descartan contenido antiguo bajo sobrecarga para evitar retraso acumulado. No se ha medido latencia física extremo a extremo ni demostrado que sea menor que DSX.

No hay captura del micrófono físico por Bluetooth. El endpoint virtual de captura no prueba lo contrario. El jack y la reconexión física apagando/encendiendo requieren comprobación manual; el cambio de ruta por detección del jack está implementado. Edge tiene su variante, pero solo se ha probado hardware DualSense normal. El firmware y algunos informes de características virtuales siguen siendo valores de referencia; la calibración física sí se copia.

## Código y pruebas

`src/` contiene la app y los recursos. `vendor/DualSenseClient/` contiene el núcleo GPLv3 y sus modificaciones. La interfaz original no se compila. `tests/`, `test-results/` y `evidence/` contienen pruebas, resultados y auditorías. Consulta `RESULTADOS.txt` para la validación final de esta sesión.

Con .NET SDK 10: `./build.ps1`. Para pruebas físicas: `./test.ps1 -Hardware`, con MadSense/DSX cerrados y el mando encendido. Envía un tono y efectos suaves al mando; no ejecutar durante una partida.

Diagnóstico: `MadSense.exe --diagnose`, `--probe`, `--exercise`, `--soak`, `--soak --xbox`. `--stop` cierra de forma ordenada también la interfaz. `--tray` arranca oculto en la bandeja (lo usa el inicio con Windows). `tools/measure-process.ps1` toma una muestra de CPU y memoria del proceso. `tools/HapticLatencyProbe` (publicar con `dotnet publish`, autorizar su exe en HidHide mientras se usa) mide la latencia háptica hasta el actuador: `--list`, `--label X --out f.json` contra el puente activo, `--direct` al mando físico con MadSense cerrado, `--session-bytes 40,40,40,40,20`, `--sustain-ms 3000 --speaker-tone` para continuidad, `--speaker-test [--lead-ms N --gap-ms N]` para oír pitidos por el altavoz a través del puente (no necesita HidHide ni cerrar MadSense), `--endpoint-volume` para leer el volumen Windows del DualSense virtual, `--ladder 64:2,64:7` para comparar de oído volumen y preganancia directos al mando (MadSense cerrada). `--exercise-stream` junto con `--soak` se usa en la prueba de gatillos durante audio. Los modos de diagnóstico no cambian la preferencia guardada. No se transmite telemetría.

## Fuentes y licencias

Núcleo [DualSense Client](https://github.com/DualSenseClient/DualSenseClient), commit `210ba4e3ea27e8aca455215d772ca7af6a2f987f`, con modificaciones identificadas en `THIRD-PARTY-NOTICES.txt`. MadSense se entrega bajo GPLv3 con su código correspondiente, sin DLL privadas de DSX. Conserva las licencias si lo redistribuyes.

Fuentes primarias consultadas el 31-10-2026:

- [DSX: notas oficiales](https://steamcommunity.com/app/1812620/announcements/): audio/hápticos virtuales y correcciones Beta02 de gatillos, silencio y salida durante audio.
- [DualSense Client: emulación](https://dualsenseclient.github.io/DualSenseClient/guides/virtual-controller/).
- [Valve: emulación de mandos Steam Input](https://partner.steamgames.com/doc/features/steam_controller/steam_input_gamepad_emulation_bestpractices).
- [Valve: soporte nativo y Steam Input](https://partner.steamgames.com/doc/features/steam_controller/getting_started_for_devs).
- [PlayStation: soporte de juegos PC](https://www.playstation.com/en-us/support/playstation-gaming-on-pc/).
- [usbip-win2](https://github.com/vadimgrn/usbip-win2) y [HidHide](https://github.com/nefarius/HidHide).

Para volver a DSX: sal de MadSense desde su bandeja y abre DSX. Se conserva la ocultación que ya existía y las autorizaciones de otras aplicaciones en HidHide.


Medición histórica de la versión 0.2, anterior a corregir la temporización de entrada (no representa la versión actual): **0,13 % de CPU total y 88,69 MiB de memoria residente** en una muestra de 15 segundos, conectado y en bandeja sin juego. Antes de retirar el motor local no usado: 0,47 % y 93,30 MiB. Son muestras puntuales del proceso, no incluyen el controlador ni miden latencia; detalles en `evidence/performance-final.txt`.

## Comparación con DSX y mejoras de 0.3

Consulta `COMPARATIVA.txt` y `evidence/comparison/` para los resultados completos.

- Entrada por eventos: las muestras nuevas despiertan un trabajador que completa la lectura USB pendiente sin esperar al siguiente temporizador. Se conserva un temporizador de respaldo, orden FIFO y cancelación.
- Temporización precisa mientras el puente está conectado, también sin audio y en bandeja; se libera al detenerlo. Corrige la caída de cadencia observada en 0.2. Aumenta el trabajo respecto al estado ocioso de aquella versión: se prioriza responder a tiempo.
- Hápticos desde el primer paquete de calentamiento, sin esperar al altavoz; cola nativa recortada antes de reproducir a dos paquetes recientes. Es una cota de contenido en cola, no una garantía de latencia física.
- Redondeo háptico al valor más cercano, simétrico, para conservar mejor efectos suaves. Sin ganancia artificial ni vibración derivada del altavoz.
- La prueba real de Stellar Blade detectó hápticos y gatillos activos sin errores Bluetooth; el usuario confirmó ambos y valoró notablemente mejor la última corrección. No es un ensayo ciego ni una medición mecánica.
- Steam Input sigue siendo una configuración de Steam. La app lo indica expresamente y no toca sus archivos ni cambia automáticamente la identidad durante la partida.

`--soak --diagnostic-audio` añade estadísticas locales de la cola USB de audio cada dos segundos. Está desactivado en la interfaz normal. La sonda reproducible está en `tools/MadSenseProbe`; requiere acceso al mando físico a través de HidHide y no forma parte del funcionamiento normal.

Versión 0.3, muestra con Stellar Blade abierto y MadSense en bandeja: 1,21 % de CPU total en este PC de 20 procesadores lógicos y 96,75 MiB residentes. No comparable con la medición histórica sin juego. Detalles en `RESULTADOS.txt` y `evidence/performance-v03.txt`.
