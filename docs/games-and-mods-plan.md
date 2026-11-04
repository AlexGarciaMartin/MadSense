# Juegos y mods

Rama `feature/games-and-mods`. Estado al 4 de noviembre de 2026: función completa en la app, pendiente de la prueba de Alex antes de publicarla.

## Qué hace

Una pantalla, **JUEGOS**, accesible desde el pie de la ventana principal y desde el menú de la bandeja. Lista todos los juegos instalados en el PC y, para cada uno, dice qué hace con el DualSense:

| Estado | Significado | De dónde sale |
| --- | --- | --- |
| DUALSENSE NATIVO | El juego usa hápticos o gatillos adaptativos por sí mismo | PCGamingWiki: campos `dualsense haptics support` / `dualsense adaptive trigger support` = true |
| MOD DISPONIBLE · 1 CLIC | Existe un mod comunitario y MadSense lo instala con un botón | Catálogo MadSense (`src/Games/catalog.json`), entradas `helper`, `gamefolder`, `beamng` |
| MOD INSTALADO | MadSense lo instaló; lo arranca con el juego y lo para al salir | Registro `%LOCALAPPDATA%\MadSense\mods\installed.json` |
| MOD · INSTALACIÓN MANUAL | Existe un mod, pero vive tras un inicio de sesión (Nexus) o necesita un cargador externo | Catálogo, entradas `manual` |
| MANDO PS, SIN FUNCIONES DUALSENSE | PCGamingWiki dice que acepta mandos PlayStation, sin hápticos ni gatillos | PCGamingWiki |
| SIN SOPORTE DUALSENSE / SIN DATOS | PCGamingWiki lo niega, o no tiene ficha confirmada para esa edición | PCGamingWiki |

El detalle muestra las seis funciones (hápticos, gatillos, altavoz, barra de luz, sensores, iconos), los modos de conexión que el juego reconoce, la atribución a PCGamingWiki (CC BY-NC-SA, enlazada) y, por cada mod, autor, licencia, qué añade y qué hará el botón.

## Biblioteca de juegos

`GameLibrary` lee Steam (manifiestos `appmanifest_*.acf`, todas las bibliotecas de `libraryfolders.vdf`), Epic (`%ProgramData%\Epic\EpicGamesLauncher\Data\Manifests\*.item`), GOG Galaxy (registro `GOG.com\Games`), Ubisoft Connect (registro `Ubisoft\Launcher\Installs`), Xbox (`<unidad>:\XboxGames\*\Content\MicrosoftGame.config`) y la lista de desinstalación de Windows para los editores que no dejan otro rastro (EA, Rockstar, Blizzard, Riot, Bethesda…). Después mira las carpetas sueltas: `Games`, `Juegos`, `Jogos`, `Spiele`, `Jeux`, `Giochi`, `Gry` o `GOG Games` en la raíz de cada disco fijo y un nivel por debajo (`D:AlexJuegos`), `Games` en el perfil, la carpeta madre y la abuela de cada juego que un launcher ya registró (nunca una raíz de disco, Program Files ni AppData) y las carpetas que el usuario añade con el botón Carpeta… de la pantalla, guardadas en `%LOCALAPPDATA%MadSensegames-folders.json` y olvidadas desde la ficha del juego; cada subcarpeta con un ejecutable real (no desinstalador, redist ni crash handler) hasta cuatro niveles es un juego, con el nombre de la carpeta limpio de etiquetas y versión. Solo lee listados de directorio, acotados a 160 carpetas por candidato, y nunca cuenta dos veces lo que un launcher ya registró. Cada fuente se lee aislada; una ilegible no esconde las demás. Enfoque tomado del escáner de Zerk Sense 5 (MIT).

## Receptor de mods (protocolo DSX)

Los mods de la comunidad hablan por UDP con DSX en `127.0.0.1:6969` enviando JSON `{"instructions":[{"type":N,"parameters":[...]}]}`. MadSense escucha ese puerto desde que arranca (`src/Mods/ModReceiver.cs`), escribe los dos ficheros de puerto que algunos mods leen (`%LOCALAPPDATA%\DSX\DSX_UDP_PortNumber.txt` y `C:\Temp\DualSenseX\DualSenseX_PortNumber.txt`) y responde con el estado que los mods esperan (`Status`, `TimeReceived`, `isControllerConnected`, `BatteryLevel`, `Devices`), con batería y conexión reales.

Instrucciones: `TriggerUpdate` (modos 0-26: los 20 heredados de DSX v2 y los 7 nombres de v3), `RGBUpdate`, `PlayerLED`, `PlayerLEDNewRevision`, `MicLED`, `TriggerThreshold` (sin efecto en el mando físico), `ResetToUserSettings`, `GetDSXStatus` (0 y 8). Cada modo se traduce al bloque de 11 bytes del gatillo con los constructores de la librería (`TriggerEffectBuilder`); los valores fuera de rango se recortan, nunca tiran el receptor.

Lo que un mod posee (cada gatillo, barra de luz, LED de jugador, LED del micro) vive en `ModOverlay`. El puente instala `ModOverlay.Apply` como filtro de estado en el adaptador de salidas de la librería (`DualSenseDeviceOutputs.StateFilter`, modificación MadSense), de modo que **todo** informe que llega al mando —el 0x31 del juego, el inicio del canal de audio y cada 0x36 combinado— lleva los valores del mod en los canales que posee y los del juego en el resto. Funciona en modo DualSense y en modo Xbox. Cuando el canal de audio está parado, un cambio del mod se empuja además como 0x31 propio para que el mando reaccione al instante. Al parar el puente o al `ResetToUserSettings` se envía un Off y el juego recupera el control.

La ventana principal enciende una lámpara **MOD** mientras un mod envía paquetes (últimos 10 s). Si el puerto está ocupado (DSX abierto), se registra en el log con el aviso correspondiente.

Comprobación: `tools/mod-client.ps1` envía la secuencia de un mod real y muestra las respuestas; `tools/mod-soak-check.ps1` lo hace contra la compilación de la rama en modo `--soak` y lee los contadores del log. `tests/MadSense.Installer.Tests/ModReceiverTests.cs` cubre parseo, mapeo de modos, prioridad sobre el juego, respuesta y puerto ocupado.

## Catálogo e instalación

`src/Games/catalog.json` (incrustado) describe cada mod: juegos (AppID de Steam o patrón de nombre para otros lanzadores), autor, licencia, página, funciones, tipo y, cuando MadSense puede instalarlo, el release exacto de GitHub con tamaño y SHA-256 cuando el autor lo publica (digest de la API de releases). Solo se admiten paquetes alojados en `github.com/<autor>/<repo>/releases/download/…`.

| Tipo | Qué hace Instalar | Ejemplos |
| --- | --- | --- |
| `helper` | Descarga, verifica, extrae en `%LOCALAPPDATA%\MadSense\mods\<id>\`; `ModRunner` lo arranca cuando detecta el juego en marcha y lo cierra al salir | WF2_DSX, RacingDSX, dr2ds, Race Element |
| `gamefolder` | Descarga, verifica, copia a la carpeta del juego **sin sobrescribir nada** (si un archivo existe, no se instala); Desinstalar borra solo lo copiado | Dying Light DualSense mod |
| `beamng` | Deja el zip del mod en la carpeta `mods` del perfil de BeamNG.drive | DualSense integration via DSX |
| `manual` | Solo página y pasos: descarga tras inicio de sesión (Nexus) o cargador externo (ScriptHook, SKSE, ASI Loader, MelonLoader) | Cyberpunk 2077, RDR2, Control, God of War, Skyrim SE, GTA V, GTASense, art of rally… |

Protecciones del instalador: tamaño máximo, rechazo de rutas fuera del destino, verificación de tamaño y SHA-256, registro de cada archivo escrito, desinstalación idempotente. No ejecuta scripts del paquete ni toca Steam Input ni opciones de lanzamiento.

## Límites conocidos

- Nexus Mods exige inicio de sesión para descargar; esos mods se muestran con su página, no se instalan.
- PCGamingWiki: 60 consultas/minuto como máximo; MadSense espera 2,1 s entre consultas, cachea 30 días y se detiene un minuto tras un 403/429. Un juego sin ficha o cuya edición no coincide por AppID queda en SIN DATOS, nunca en "no compatible".
- Los helpers de telemetría (Forza, DiRT) necesitan activar la salida de datos del juego; se explica en la ficha, no se automatiza todavía.
- No se ha probado un mod con un juego real en este PC (no hay ninguno de la lista instalado); el receptor se probó con paquetes reales de la secuencia de un mod y el instalador con paquetes de prueba.

## Fuentes

- Protocolo: README del sistema de mods de DSX v3 (Paliverse), servidor UDP de DualSenseY v2 (MIT) y de DSZ (MIT), `DsxServer.cs` de Zerk Sense 5 (MIT).
- Lista de mods: Awesome Adaptive Triggers (CC0), README de DualSenseX, tabla de DSZ, releases de cada repositorio.
- Compatibilidad: PCGamingWiki, plantilla `Input` (CC BY-NC-SA).
