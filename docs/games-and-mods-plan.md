# Juegos y mods: exploración

Fecha de revisión: 2 de noviembre de 2026. Rama de trabajo aislada; esta propuesta no anuncia compatibilidad nueva en la versión pública.

## Resultado comprobado del prototipo

- Rama `feature/games-and-mods`, en worktree separado. Acceso «Juegos y mods» en el pie y la bandeja; ventana independiente con búsqueda, filtro de mods para instalaciones conocidas y enlaces al autor.
- Prueba local: 10 manifiestos instalados de Steam; la vista muestra 8 juegos al excluir DSX y Steamworks Common Redistributables por sus AppID. Aún falta clasificación general de aplicaciones/herramientas; el lector no deduce el tipo por el nombre.
- Consulta real de los README de Paliverse y DSZ: 16 fichas tras combinar las referencias iniciales y deduplicar. Las entradas nuevas de esas listas aparecen sin recompilar la app. No es un índice exhaustivo de Internet ni certifica compatibilidad.
- PCGamingWiki: prueba real del endpoint por AppID devuelve HTTP 403 de Cloudflare; contratos de extracción, límites, cancelación y cooldown verificados con transporte simulado. No se ha validado todavía la descarga real de capacidades.
- 201 pruebas de la suite de aplicación/instalador correctas. Compilación Release del preview: sin advertencias ni errores. Revisión visual de navegación, datos reales, maximización, escala y cierre; corregidos escala inicial y ciclo de cancelación/disposición.
- La interfaz nueva incluye los seis idiomas de la app. Sin publicación, cambios de versión, instalación de mods ni cambios de Steam Input. El preview no crea el puente ni abre el mando.

Para abrir solo esta exploración desde el repositorio, con el SDK .NET 10 disponible:

```powershell
dotnet run --project tests/MadSense.UiPreview -c Release -- --data-dir "$env:TEMP\MadSense-GamesPreview" --language es --view mods
```

`--view games` abre la biblioteca y `--view main` permite comprobar el acceso desde la pantalla principal simulada. La caché del catálogo y de PCGW es de sesión; el trabajo pendiente incluye persistencia, salud de cada fuente y actualización manual de datos cacheados.

## Alcance de esta iteración

Prototipo de consulta con dos secciones, **Instalados** y **Catálogo**, accesible desde un menú secundario. Mantener la pantalla principal centrada en conectar y jugar.

- Leer bibliotecas y manifiestos locales de Steam para identificar instalaciones por AppID, nombre y ruta. No inferir que una carpeta suelta es una copia Steam.
- Importar automáticamente las listas de Paliverse y DSZ al abrir Mods o pulsar Actualizar, deduplicar enlaces y excluir las bibliotecas de desarrollo. Combinar con tres fichas de referencia, con autor, requisitos y estado pendiente de validación con MadSense.
- Consultar PCGamingWiki al seleccionar un juego, con caché de sesión, límites de tráfico y atribución visible. Conservar los estados sí/no/limitado/con modificaciones/sin datos, sin inferir soporte nativo de una etiqueta genérica.
- No descargar ni ejecutar mods; no cambiar Steam Input, opciones de lanzamiento ni archivos de juegos; no publicar una nueva versión.
- Una coincidencia entre juego y ficha significa que existe un mod documentado para ese juego. No certifica compatibilidad con MadSense ni con la edición instalada.

## Catálogo propio, con fuentes originales

La denominación propuesta es **catálogo oficial de MadSense de mods comunitarios**. No es un catálogo oficial de DSX y la autoría de cada mod se conserva.

Existen listas de varios autores que podemos importar automáticamente. Ninguna de las investigadas proporciona por sí sola recetas verificadas de instalación para todos sus elementos; no se afirma que la búsqueda sea exhaustiva.

| Fuente de descubrimiento | Qué permite obtener |
| --- | --- |
| [README de Paliverse](https://github.com/Paliverse/DualSenseX#game-mods-with-dualsensex) | Siete enlaces a mods o hubs de varios autores, juegos y fuentes GitHub/Nexus/Workshop/RaceDepartment. El [XML de juegos](https://github.com/Paliverse/DualSenseX/blob/main/Resources/_GamesList.xml) solo contiene una demo. |
| [Tabla de DSZ](https://github.com/MaximumFreak/dsz#mod-compatibility) | Repositorios de varios autores, juegos y licencias declaradas por ese proyecto; filtrar las bibliotecas de desarrollo, que no son mods instalables. |
| [Lista de PadForge](https://padforge.org/docs/features/game-mods/#mods-made-for-this-interface) | Nombres de mods y juegos; sirve para contrastar descubrimientos. Sus pruebas de mensajes no equivalen a pruebas físicas de todos los mods. |
| [PCGamingWiki: DualSense](https://www.pcgamingwiki.com/wiki/Controller:DualSense) y [lista de juegos](https://www.pcgamingwiki.com/wiki/List_of_games_that_support_DualSense) | Candidatos de compatibilidad y enlaces/notas de cada juego. Separar funciones nativas, soporte mediante mod y simples botones o reconocimiento del mando. |

El [Workshop de DSX](https://steamcommunity.com/app/1812620/workshop/) se presenta como intercambio de perfiles de mando; no tratar sus elementos como plugins de juegos. [DualSenseY](https://github.com/WujekFoliarz/DualSenseY-v2) documenta recepción automática de mods, pero no se encontró un catálogo multijuego en su README y árbol propios revisados.

| Candidato | Evidencia y requisitos | Decisión inicial |
| --- | --- | --- |
| [WF2_DSX](https://github.com/SYSTEMATI0N/WF2_DSX), SYSTEMATI0N | Wreckfest 2; EXE externo, UDP 6969, configura telemetría y ofrece ejecución oculta y supervisión del proceso. MIT. | Primer candidato para validar. Aún sin prueba con MadSense. |
| [RacingDSX](https://github.com/cosmii02/RacingDSX/blob/main/Readme.md), cosmii02 | Forza y DiRT; .NET 8, configuración de telemetría según juego. El autor declara pruebas con las aplicaciones DSX v2 y v3.1. GPL-3.0; repositorio archivado. | Ficha informativa; no confundir versión de la app DSX con versión del protocolo. |
| [DualSense4Rockstar](https://github.com/tommargar/DualSense4Rockstar), tommargar y autores acreditados | GTA V/RDR2; varias dependencias ScriptHook y, en GTA V, LemonUI. El autor advierte de falta de pruebas recientes; no hay licencia declarada en GitHub. | Ficha informativa. Revisar versiones y permisos antes de distribuir o automatizar. |

Los mods de Nexus añaden otra condición: la [API oficial](https://github.com/Nexus-Mods/node-nexus-api/blob/master/docs/classes/_nexus_.nexus.md#getdownloadurls) requiere para usuarios no Premium una clave del enlace de descarga generado en su web. No prometer descarga directa universal. Ofrecer enlace a la fuente o importación del archivo mientras no exista una integración autorizada.

## Evolución del catálogo

Publicar un manifiesto pequeño y versionado, independiente del instalador de MadSense. Consultarlo al abrir la sección, reutilizar caché y permitir actualización manual. La biblioteca local no necesita enviarse a un servidor.

Importar las listas anteriores mediante adaptadores por fuente y detectar releases nuevas en los repositorios enlazados. GitHub permite consultar contenido de README y releases por API; normalizar URL e identidad del repositorio, deduplicar por mod y juego, y conservar fecha, revisión y origen de cada afirmación. La búsqueda por temas o palabras puede aportar candidatos adicionales; no prueba compatibilidad ni cubre todos los mods.

Separar **descubrimiento automático** de **instalación verificada**. Un candidato nuevo puede aparecer automáticamente como referencia pendiente, con enlace y requisitos conocidos. El botón de instalación solo se habilita tras revisar el paquete, la receta y su prueba. Así la lista puede crecer sin introducir manualmente cada enlace, mientras los binarios que instala MadSense conservan un proceso de validación. No descargar ni ejecutar un candidato por el mero hecho de aparecer en una fuente.

El cliente PCGamingWiki implementado sigue la consulta pública por AppID y lectura de la plantilla Input que utiliza [ZerkSense5](https://github.com/florinp93/ZerkSense5/blob/7ac8a876d6374de17a7e6ba8d3f2464506e788d9/Core/Games/Pcgw.cs). Sus contratos se validan con respuestas simuladas. La prueba HTTP real desde este PC el 2 de noviembre de 2026 devolvió **403 de Cloudflare**, incluso con User-Agent identificable: la recuperación real de capacidades sigue pendiente. No sortear el bloqueo ni convertir ese fallo en «no compatible». Hay referencias editoriales de tres juegos como respaldo limitado.

Según la [API de PCGamingWiki](https://www.pcgamingwiki.com/wiki/API), respetar 60 consultas/minuto, identidad del cliente y restricciones de acciones autenticadas; `cargoquery` no es una solución anónima garantizada. El prototipo separa consultas por al menos 2,1 segundos y pausa tras 403/429. Para publicación falta resolver acceso autorizado estable, caché persistente y actualización con fecha de los datos. Mantener [atribución/licencia CC BY-NC-SA](https://www.pcgamingwiki.com/wiki/PCGamingWiki:Copyrights) separada de la licencia del código; no redistribuir el contenido de la wiki como MIT.

Separar tres operaciones: actualizar fichas, descargar una versión y sustituir un mod instalado. Una actualización del catálogo nunca modifica por sí sola los juegos. Conservar la versión instalada hasta que el usuario elija actualizar. Las entradas retiradas pueden bloquear nuevas instalaciones y conservar desinstalación y explicación.

Esquema futuro mínimo:

| Nivel | Datos |
| --- | --- |
| Catálogo | `schemaVersion`, `catalogVersion`, `minAppVersion`, fecha, firma y clave identificada |
| Identidad | ID estable, nombre, autor, enlaces originales, licencia y avisos |
| Compatibilidad | IDs por tienda, edición y rango de versiones del juego; protocolo requerido; efectos; fecha, versión de MadSense y pruebas realizadas |
| Paquete | Versión exacta, URL permitida, tamaño, SHA-256 y firma del editor si existe |
| Receta | Dependencias, conflictos, archivos y destinos concretos, cambios de configuración tipados, modo de arranque y retirada |
| Estado | Referencia, en validación, verificado o retirado; motivo y restricciones documentadas |

Verificar la firma del catálogo con una clave pública incorporada en la app, su versión de esquema y su secuencia; impedir retrocesos silenciosos. Descargar solo desde fuentes admitidas y verificar tamaño y hash antes de extraer. El hash identifica el paquete revisado, no demuestra por sí mismo que sea fiable. La [API de assets de GitHub](https://docs.github.com/en/rest/releases/assets) proporciona URL, tamaño y digest; conservar versiones fijadas, no ejecutar lo que resuelva `latest` sin nueva revisión.

## Instalación y retirada

El motor futuro interpretará operaciones limitadas: extraer archivos concretos, copiarlos dentro del destino validado, editar campos conocidos y registrar un helper. El catálogo no podrá introducir scripts CMD/PowerShell arbitrarios.

Antes de instalar: comprobar edición y versión, cerrar o esperar al juego, resolver dependencias, validar espacio y permisos, y presentar qué preparación necesita. Extraer primero en staging; rechazar rutas fuera del destino, enlaces que escapen de él y archivos comprimidos con tamaños desproporcionados.

Registrar por instalación los archivos propios, hashes, configuración anterior y cambios aplicados. Detectar colisiones con otros mods y dependencias compartidas; no sobrescribirlas por defecto. Aplicar una transacción con recuperación tras cierre inesperado. Repetir una instalación de la misma versión debe ser idempotente. Desinstalar solo archivos cuyo contenido siga correspondiendo al instalado por MadSense; conservar modificaciones posteriores del usuario y dependencias que otro mod utilice. Un rollback no debe restaurar una copia completa de la carpeta del juego.

## Receptor DSX y funciones nativas

El receptor es un requisito previo a activar cualquier botón de instalación compatible. Implementar el contrato de mensajes y descubrimiento que utilicen los candidatos, respuestas de estado y limpieza de efectos. La [documentación de Paliverse](https://github.com/Paliverse/DSX/blob/main/Mod%20System%20%28DSX%20v3%29/README.md) describe órdenes UDP; no todos los mods usan la misma revisión o el mismo mecanismo.

Arbitrar por canal las órdenes del juego nativo y del mod: gatillos, luces y audio no deben sobrescribirse incidentalmente. Conservar el recorrido de hápticos nativos y altavoz. Definir quién posee un efecto y cómo se libera al terminar el juego, desconectar el mando o fallar el helper. Un temporizador de silencio indiscriminado podría cortar efectos que el mod envía solo cuando cambian.

Validar primero WF2_DSX con el receptor y después su receta. Matriz mínima: DualSense/XInput; Bluetooth conectado, reconexión y cambio de modo; DSX ocupando el puerto; mensajes inválidos; cierre normal y fallo del helper; juego nativo y mod simultáneos; instalación repetida, actualización, colisión, rollback y retirada. Las pruebas de protocolo no sustituyen una partida real ni confirman sensación o latencia física.

## Preparación de Steam Input

Detectar soporte nativo requiere evidencia por juego y función. La lista de PCGW se alimenta del campo `Playstation controller models`; aparecer en ella no prueba hápticos, gatillos y altavoz. Cruzar AppID con la página del juego y conservar notas, conexión necesaria, dependencia de Steam Input, fuente y fecha. Un texto que enlaza un mod debe clasificarse como soporte mediante mod. Si faltan datos, mostrar estado desconocido.

Como referencia para la lectura local, [SteamLocalService de Playnite](https://github.com/JosefNemec/PlayniteExtensions/blob/master/source/Libraries/SteamLibrary/Services/SteamLocalService.cs) consulta `libraryfolders.vdf` y manifiestos `appmanifest_*.acf`, identifica AppID/nombre/ruta y comprueba estado instalado. No necesita escanear todos los discos ni credenciales web para esa detección.

La automatización de preferencias por juego necesita una investigación y prueba separadas. La [API pública ISteamInput](https://partner.steamgames.com/doc/api/ISteamInput) se orienta a la integración del juego; no asumir que permite a MadSense administrar las preferencias de toda la biblioteca. Algunos juegos envían efectos mediante esa API, por lo que desactivar Steam Input no es una regla universal.

[Steam ROM Manager](https://github.com/SteamGridDB/steam-rom-manager/blob/master/src/lib/controller-manager.ts) ofrece un precedente de edición de `UseSteamControllerConfig` por AppID; sus [instrucciones](https://github.com/SteamGridDB/steam-rom-manager/wiki/Command-Line-Interface#before-you-add-or-remove-games) requieren cerrar Steam para evitar que sobrescriba cambios. Esto es una integración con archivos del cliente, no una API estable avalada por Valve.

El flujo propuesto es **Preparar y jugar**, con consentimiento inicial claro por juego. Guardar usuario de Steam, AppID, valor previo y valor aplicado. Si hace falta cerrar Steam para persistir el cambio, explicar ese paso antes de hacerlo; no terminarlo silenciosamente. Tras preparar, confirmar el valor efectivo antes de lanzar mediante Steam.

Ofrecer restablecer la preferencia anterior. Restaurar exclusivamente ese campo y solo si su valor actual aún coincide con el que escribió MadSense; conservar cambios posteriores del usuario. No restaurar un snapshot completo de `localconfig.vdf` ni tocar ajustes globales del mando. Probar perfiles múltiples, Steam abierto/cerrado, reescritura del cliente, cierre inesperado y juego ya iniciado antes de habilitarlo.

## Orden de entrega

1. Revisar el prototipo de biblioteca y catálogo de referencia; comprobar lectura con varias bibliotecas, manifiestos incompletos y carpetas ausentes.
2. Implementar y validar el receptor DSX con un candidato conocido, empezando por WF2_DSX.
3. Incorporar la primera receta de instalación reversible y probar el ciclo completo con el juego real.
4. Habilitar catálogo remoto revisado y firmado, conservando caché y modo sin conexión.
5. Validar aparte la preparación por juego de Steam Input y su recuperación. Activarla solo cuando las pruebas demuestren persistencia fiable.

La rama exploratoria no modifica la versión pública ni constituye autorización para publicar un catálogo que anuncie mods como verificados antes de esas pruebas.
