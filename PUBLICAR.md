# Publicar MadSense en GitHub

El repositorio vive en https://github.com/AlexGarciaMartin/MadSense (público, rama `main`, GPL-3.0) y la primera versión instalable está en su Release `v0.4`. Se publicó el 1 de noviembre de 2026 con Git y GitHub CLI (`gh`), ya instalados e identificados en este PC.

## Publicar una versión nueva (resumen)

```powershell
app\MadSense.exe --stop
tools\build-release.ps1
git add -A
git commit -m "MadSense 0.5: ..."
git push
gh release create v0.5 dist\MadSense-0.5-win-x64.zip dist\MadSense-0.5-win-x64.zip.sha256 --title "MadSense 0.5" --notes-file dist\release-notes-0.5.md --latest
```

Antes, sube `<Version>` en `src/MadSense.csproj` y la etiqueta "VERSIÓN" de la ventana. Lo que sigue es la guía completa, por si hay que repetirlo en otro PC.

Pasos para subir este repositorio y publicar la versión instalable. Todo se hace desde PowerShell en la carpeta del proyecto.

## 1. Git

Si `git --version` no responde, instálalo con winget y abre una ventana nueva de PowerShell:

```powershell
winget install --id Git.Git -e --source winget
```

La primera vez, dile a Git quién eres (aparece en cada commit):

```powershell
git config --global user.name "Tu nombre"
git config --global user.email "tu@correo"
```

## 2. Repositorio local

```powershell
git init -b main
git add .
git status
```

`git status` no debe listar `app/`, `dist/`, `bin/`, `obj/`, `test-results/`, `MadSense.lnk` ni ningún `.log` fuera de `evidence/`: `.gitignore` los excluye. Las evidencias se versionan con las rutas y la MAC del mando anonimizadas.

```powershell
git commit -m "MadSense 0.4: DualSense por Bluetooth con hápticos, gatillos y altavoz"
```

## 3. Repositorio en GitHub

1. En GitHub: **New repository**, nombre `MadSense`, público, **sin** README, licencia ni .gitignore (ya existen aquí).
2. Conecta y sube:

```powershell
git remote add origin https://github.com/<tu-usuario>/MadSense.git
git push -u origin main
```

Git pedirá iniciar sesión en GitHub la primera vez (ventana del navegador).

## 4. Versión instalable (Release)

El zip no va en el repositorio; se adjunta a una *release*.

```powershell
app\MadSense.exe --stop
tools\build-release.ps1
```

Deja `dist\MadSense-0.4-win-x64.zip` y su `.sha256`. Luego en GitHub: **Releases → Draft a new release**, etiqueta `v0.4`, título `MadSense 0.4`, adjunta el zip y pega el contenido del `.sha256` en la descripción. Como texto de la release sirve la sección "Novedades de 0.4" del README.

## 5. Licencias

El proyecto es GPL-3.0 (`LICENSE`); el núcleo vendido en `vendor/DualSenseClient` también. `THIRD-PARTY-NOTICES.txt` y `dependency-notices.txt` recogen el resto. El instalador descarga USBip (BSD-2) e HidHide (MIT) de sus GitHub oficiales con la huella SHA-256 comprobada; no se redistribuyen sus binarios.
