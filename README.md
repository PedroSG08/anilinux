# AniLinux Terminal — Anime en tu terminal

Cliente nativo para GNU/Linux de **Kawaii Animes**, reescrito desde cero a
partir de la app Android (`app-release.apk`).  Solo usa la librería estándar
de **Python 3**: no hay dependencias, no hay Java, no hay emulador.

```
╭── ANILINUX TERMINAL ──────────────────────────────────────────────────────╮
│ Cuenta: Invitado · 2387 animes · 36970 episodios · 4 servidores           │
│ SECCIONES     │                                                           │
│ ▌ Inicio      │ Continuar viendo                                          │
│   Explorar    │ Novedades (más recientes)                                 │
│   Géneros     │ Últimos episodios añadidos                                │
│   Buscar      │ Populares (más vistos)                                    │
│   Calendario  │ Mejor valorados                                           │
│   Perfil      │ En emisión                                                │
│   Ajustes     │ Películas                                                 │
│               │ Audio latino                                              │
│               │ Audio castellano                                          │
╰───────────────────────────────────────────────────────────────────────────╯
 ↑↓ cambiar sección · Enter/→ entrar · q salir
```

## Qué incluye

* **Navegación 100 % de teclado**: al mover el rail con ↑/↓ se muestra al
  instante el contenido de cada sección.  Enter/→ entra en sus opciones y
  Esc/← vuelve al rail; `q` sale.
* **Secciones** equivalentes a la app Android:
  - **Inicio** — Continuar viendo (capítulos a medias ordenados por actividad
    reciente, con barra de progreso y tiempo restante; Enter reanuda en la
    posición guardada), Novedades, Últimos episodios, Populares, Mejor
    valorados, En emisión, Películas, Audio latino, Audio castellano.
  - **Explorar** — Todos, En emisión, Finalizados, Películas, OVA, ONA,
    Especiales, Populares, Mejor valorados…
  - **Géneros** — lista completa de géneros con recuento.
  - **Buscar** — búsqueda por nombre/nombre alternativo.
  - **Calendario** — emisiones por día de la semana.
  - **Perfil** — tu cuenta.
  - **Ajustes** — sincronización, reproductor, estado del catálogo.
* **Progreso persistente**: el avance se guarda cada ~5 s.  Al dejar un
  capítulo a medias aparece en Inicio → Continuar viendo; solo se marca como
  visto al llegar al 95 % o a menos de 15 s del final.
* **Catálogo offline completo**: la fuente de verdad es la base de datos
  `assets/databases/kawaii_animes.db` incluida dentro del APK oficial de la
  app (~2 400 animes, ~37 000 episodios, ~160 000 enlaces).  En el primer
  arranque, si no hay catálogo local, se descarga el APK oficial
  automáticamente (~25 MB) y se importa; queda cacheado para las siguientes
  ejecuciones.  Un clon nuevo nunca arranca con una lista vieja o vacía.
  La API v4 ya no publica el catálogo: solo devuelve los servidores.
* **Sincronización incremental** con `POST /sync` usando los cursores
  `lei`/`laua`/`lpua` (mismo mecanismo que la app).
* **Login real** contra el servidor:
  - `POST /auth/token` (iniciar sesión), `POST /auth/register` (crear
    cuenta), `POST /auth/password/code` + `POST /auth/password/restore`
    (recuperar contraseña) y `GET /auth/logout` (cerrar sesión).
  - El token se guarda en `~/.config/anilinux-terminal/session.json`
    (con permisos 0600).
  - `GET /auth/lists/all` baja favoritos/viendo/ver más tarde/vistos y
    progreso; los cambios locales se suben con `/auth/…/add|remove` y
    `/auth/progress/update`, igual que hace la app.
* **Reproducción**: elige anime → episodio; si hay varios idiomas, elige
  Sub/Lat/Cas.  La TUI prueba los servidores en orden y abre el primero que
  funcione con `mpv`.  En kitty el vídeo se dibuja dentro de la terminal; en
  el resto de terminales se abre una ventana de mpv a pantalla completa.
  Teclas: espacio pausa, ←/→ salta, ↑/↓ volumen, `m` silencio, `n`/`p`
  siguiente/anterior, `b` vuelve a la lista de capítulos y `q`/Esc sale.

## Instalación

**Requisitos**

* **Python 3.8+** (solo librería estándar: no hay dependencias).
* **`mpv`** (probado con 0.41) para reproducir: `sudo apt install mpv`
  (Arch: `pacman -S mpv`, Fedora: `dnf install mpv`).  Sin mpv la TUI
  permite navegar por el catálogo, pero no reproducir.
* **Terminal**: para ver el vídeo *dentro* de la terminal hace falta una con
  protocolo de imágenes de kitty (kitty, ghostty, wezterm…).  En el resto se
  abre una ventana de mpv a pantalla completa (o modo texto si no hay
  entorno gráfico).

**Pasos**

* Clona el repositorio:
  ```bash
  git clone https://github.com/PedroSG08/anilinux.git
  cd anilinux
  ```
* Crea el comando global:
  ```bash
  mkdir -p ~/.local/bin
  ln -sf "$PWD/anilinux" ~/.local/bin/anilinux
  ```
  Si `~/.local/bin` no está en tu `PATH`, añádelo a tu shell:
  ```bash
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
  source ~/.bashrc
  ```
* Arranca y listo:
  ```bash
  anilinux
  ```
  El primer arranque descarga el APK oficial y su catálogo (~2 400 animes,
  ~37 000 episodios) automáticamente.  Solo necesita conexión esa primera
  vez; después funciona sin red.
* (Opcional) si ya tienes el APK, evita la descarga:
  ```bash
  anilinux --apk ~/Descargas/app-release.apk
  ```

**Desinstalación**

```bash
rm ~/.local/bin/anilinux
rm -rf ~/anilinux   # o la ruta donde lo hayas clonado
rm -rf ~/.local/share/anilinux-terminal ~/.config/anilinux-terminal
```

## Uso

```bash
anilinux                    # arrancar (descarga el catálogo si falta)
anilinux --update           # descargar el APK oficial y actualizar el catálogo
anilinux --no-download      # no descargar; usar APK local o API
anilinux --apk RUTA         # importar/fusionar un APK concreto
anilinux --no-sync          # no sincronizar al arrancar
anilinux --self-update      # comprobar ya si hay versión nueva del programa
anilinux --no-self-update   # no comprobar versiones en este arranque
```

`--update` (o `--bootstrap`) descarga la última versión del APK oficial y
fusiona su catálogo **sin borrar** tus listas, progreso ni descargas.  Si la
descarga falla, conserva el catálogo que ya tenías.  El APK queda guardado en
`~/.local/share/anilinux-terminal/app-release.apk` para no volver a bajarlo.

## Actualización automática del programa

Además del catálogo (que se actualiza desde el APK), el **código** de
AniLinux Terminal se actualiza solo desde `origin/main`:

* Al arrancar (como máximo cada 6 horas) se consulta el repositorio.  Si hay
  una versión nueva, se aplica un `git pull --ff-only` y la TUI se reinicia
  sola con el código nuevo.
* `--update`, `--bootstrap` y `--self-update` fuerzan la comprobación.
* Si hay cambios locales en el código, la rama no es `main`, el historial ha
  divergido o no hay conexión, **no se cambia nada** y el programa arranca
  igualmente (se muestra un aviso solo cuando la comprobación es forzada).
* Para desactivarla: añade `"auto_update": false` a
  `~/.config/anilinux-terminal/settings.json`, usa `--no-self-update` o define
  `ANILINUX_NO_SELF_UPDATE=1`. Los archivos `.pyc`/`__pycache__` se ignoran al
  comprobar cambios locales.

Así, cuando sale una versión nueva del APK, basta con ejecutar
`anilinux --update`: se fusiona el catálogo y el programa se actualiza a la
vez, sin tocar listas, favoritos, progreso ni descargas.

Instalación del comando: se crea un symlink
`~/.local/bin/anilinux → ~/anilinux/anilinux`.  Si `~/.local/bin` no está en
tu `PATH`, añádelo a tu shell.  Si mueves el proyecto a otra ruta, recrea el
symlink con `ln -sf RUTA/nueva/anilinux ~/.local/bin/anilinux`.

## Actualizar en otro dispositivo

```bash
cd ~/anilinux
git pull --ff-only
anilinux --update
```

`git pull --ff-only` actualiza el código y `--update` el catálogo.  Si el
`git pull` se queja de cambios locales, revisa `git status` y guarda una
copia antes de forzar nada: no hace falta borrar el proyecto ni la base de
datos.

## Estructura del proyecto

```
anilinux/
├── anilinux                  # lanzador ejecutable (además disponible como comando global `anilinux`)
└── anilinux_tui/
    ├── main.py             # arranque, importación de catálogo, sync inicial
    ├── api.py              # cliente del API v4 (auth, listas, sync)
    ├── database.py         # SQLite: esquema, import del APK, bulk/incremental
    ├── session.py          # sesión persistente (token + usuario)
    ├── tui.py              # primitivas ANSI: teclado, colores, cajas
    └── app.py              # secciones y pantallas (rail + menús + listas)
```

## Datos

| Archivo | Contenido |
|---|---|
| `~/.local/share/anilinux-terminal/catalog.db` | catálogo + tus listas locales |
| `~/.local/share/anilinux-terminal/app-release.apk` | APK oficial cacheado (fuente del catálogo) |
| `~/.config/anilinux-terminal/session.json` | token de sesión (chmod 600) |
| `~/.config/anilinux-terminal/settings.json` | preferencias (reproductor, etc.) |

Variables de entorno opcionales: `ANILINUX_DATA_DIR` y
`ANILINUX_CONFIG_DIR` cambian esos directorios, y `ANILINUX_APK` la ruta del
APK.  Por compatibilidad, las antiguas `KAWAII_*` siguen aceptándose y los
datos viejos en `~/.config/kawaii-linux` y `~/.local/share/kawaii-linux` se
migran automáticamente al primer arranque si el destino no existe.

## Notas

* Es necesario instalar el reproductor: `mpv` (`apt install mpv`); sin él, la TUI
  permite navegar por el catálogo, pero no reproducir.
* Las conversaciones con el servidor usan las mismas cabeceras y cuerpo
  JSON que la app (`{"e": correo, "p": contraseña}`, `{"n": nombre, …}`),
  de modo que la cuenta y las listas son **las mismas** que en el móvil.
* **Tokens `kp2:`**: la app Android 13.7 usa un nuevo formato de token `kp2:` en
  los enlaces de los jugadores.  Esta versión incluye un descodificador propio que
  intenta transformar esos tokens a enlaces compatibles con la resolución
  habitual (servidores Delta/Byse y otros).  El primer arranque importa el catálogo
  del APK oficial; los nuevos tokens `kp2:` se fusionan con la base existente mediante
  `INSERT OR IGNORE`, por lo que **no se borran listas, favoritos, progreso ni
  descargas**.  Si el descodificador no puede transformar un token concreto, la
  reproducción falling back al flujo clásico o muestra un error amigable.
* Este programa no es original ni pretende serlo. Unicamente busca adaptar la aplicación kawaii animes a linux, con el fin de hacer accesible el contenido al mayor número de personas posible. Todo el crédito a: https://github.com/animelhd/kawaii-animes  
  
