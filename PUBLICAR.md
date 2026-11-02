# Preparar y publicar el instalador de MadSense

El repositorio es [AlexGarciaMartin/MadSense](https://github.com/AlexGarciaMartin/MadSense), GPL-3.0. La distribución actual es el instalador `MadSense-0.4.5-Setup.exe` de la release `v0.4.5`; `v0.4` conserva el ZIP histórico. **Compilar no publica una release ni modifica la descarga de la web**: esos pasos se realizan por separado.

## Preparar el artefacto

Trabaja en una rama o un worktree dedicado. Necesitas Windows x64 y .NET SDK 10. No hace falta cerrar la copia de desarrollo para compilar el instalador:

```powershell
git status --short
.\tools\build-installer.ps1
```

La app se publica en una carpeta nueva de `.build/`. Si no se indica `-IsccPath` y `ISCC.exe` no está en PATH, el script descarga la versión portable fijada `Tools.InnoSetup 7.1.0` desde NuGet, comprueba el SHA-256 y la extrae en `.tools/`. No instala el compilador en Windows.

También descarga USBip 0.9.8.1 e HidHide 1.5.230 desde sus releases oficiales, comprueba sus SHA-256 y firmas Authenticode e incorpora esos paquetes al instalador. Las versiones, URLs y hashes se mantienen en `setup/dependencies.json`.

Con `<Version>0.4.5</Version>` se generan:

- `dist/installer/MadSense-0.4.5-Setup.exe`
- `dist/installer/MadSense-0.4.5-Setup.exe.sha256`
- `dist/installer/MadSense-0.4.5-Setup.exe.json`

El JSON describe el commit, si había cambios sin confirmar, compilador y paquetes incorporados. No sustituye las pruebas del instalador. Para una release reproducible, confirma el código que se va a distribuir y vuelve a compilar desde ese estado limpio.

Parámetros de `tools/build-installer.ps1`:

| Parámetro | Uso |
|---|---|
| `-IsccPath` | Ruta a un `ISCC.exe` ya disponible; usar una versión compatible y registrar cuál se utilizó. |
| `-DotnetPath` | Ruta al ejecutable del SDK .NET 10. |
| `-PublishDirectory` | Publicación completa existente para reutilizar en lugar de compilar. Debe corresponder al código de la release. |
| `-DependencyDirectory` | Caché de los instaladores oficiales; no omite la verificación. |
| `-InstallerTestBuild` | EXE de pruebas con identidad separada, helper simulado y arranque final desactivado. No distribuir. |

`app/`, `dist/`, `.build/`, `.tools/`, `bin/`, `obj/` y los resultados locales no deben subirse como código. Comprueba `git status` antes de confirmar. El ZIP de `tools/build-release.ps1` es el formato histórico, no el nuevo procedimiento de distribución.

## Validar antes de publicar

1. Ejecuta las pruebas específicas de [`setup/README.md`](setup/README.md) y las del núcleo afectado.
2. Revisa el asistente a escala normal y con escalado alto: texto completo, botones utilizables y reinicio indicado cuando proceda.
3. Completa la matriz en Windows limpio, incluido usuario estándar que introduce otra cuenta administradora, cancelación de UAC, reinicio pendiente y actualización de una instalación antigua.
4. Tras instalar y reiniciar, comprueba conexión Bluetooth, modos DualSense/Xbox y salida limpia. La simulación de drivers no acredita esta prueba física.
5. Desinstala desde Aplicaciones instaladas y verifica que se conserve la configuración del usuario, que no se quiten drivers compartidos ni otras autorizaciones de HidHide, y que un fallo de recuperación bloquee el borrado.
6. Comprueba el hash del EXE final después de cualquier firma o modificación. No distribuyas el archivo `-TEST.exe`.

La publicación no declara por sí sola que toda esta matriz esté superada. Registra resultados, sistema utilizado y limitaciones; mantén como pendientes las pruebas que no se hayan realizado. Los resultados del instalador `v0.4.0`, incluida la reinstalación de ambos drivers y el primer arranque tras reiniciar, están en `setup/README.md`. Firmar el EXE es un paso independiente: compilar con Inno Setup no le añade una firma Authenticode.

## Publicar próximas versiones

Cuando se apruebe la versión, elige su número y actualiza `src/MadSense.csproj` y la versión visible de la app, si procede. No sobrescribas silenciosamente el ZIP existente ni reutilices su hash para el EXE nuevo.

Sube la rama para revisión, integra los cambios cuando corresponda y publica el instalador, su hash y las notas de esa versión en una nueva release o prerelease. Identifica en las notas las comprobaciones realizadas y las pendientes; no presentes como probados otros equipos o escenarios. Actualiza después los enlaces e instrucciones de la web y verifica que el SHA-256 publicado coincide con el EXE. Conserva el código exacto correspondiente a los binarios distribuidos bajo GPL-3.0.

Desde `0.4.5`, las apps instaladas consultan `https://madsense.pro/downloads/release.json` para avisar de nuevas versiones. **Actualiza y publica `dist/downloads/release.json` en la web solo después de que la release estable y su EXE estén disponibles públicamente.** Comprueba la descarga sin autenticación y su SHA-256 antes de anunciarla en ese manifiesto; no lo apuntes a borradores ni prereleases.

El manifiesto debe mantener una versión estable de tres números, sin prefijo `v` ni sufijos, y estos valores coherentes (sustituye `X.Y.Z` por la misma versión en todos ellos):

- `version`: `X.Y.Z`
- `filename`: `MadSense-X.Y.Z-Setup.exe`
- `downloadUrl`: `https://github.com/AlexGarciaMartin/MadSense/releases/download/vX.Y.Z/MadSense-X.Y.Z-Setup.exe`
- `releaseUrl`: `https://github.com/AlexGarciaMartin/MadSense/releases/tag/vX.Y.Z`

Conserva también tamaño, SHA-256 y datos del artefacto exacto en el manifiesto. El aviso solo abre la descarga cuando el usuario pulsa; no instala nada por su cuenta. Los usuarios de `0.4.4` o anteriores deben instalar una vez la versión nueva para incorporar esta comprobación.

## Licencias y atribución

`LICENSE` contiene GPL-3.0; `THIRD-PARTY-NOTICES.txt` y `dependency-notices.txt` recogen los componentes de la app y el nuevo empaquetado. El Setup redistribuye los **instaladores oficiales sin modificar** de USBip (BSD-2-Clause) e HidHide (MIT), cuyos textos íntegros y procedencia están en `setup/licenses/`.

El motor del asistente procede de Inno Setup. Se conservan sus avisos de copyright, y se incluye su licencia en `setup/licenses/`. `Tools.InnoSetup` es el paquete de herramientas que facilita el compilador; no es una dependencia del funcionamiento de MadSense. La fuente Anton se usa para generar las imágenes del asistente y se conserva con OFL en `setup/assets/`; no se instala como fuente del sistema.
