# AniLinux Terminal — Anime en tu terminal

Cliente nativo para GNU/Linux de **Kawaii Animes**, reescrito desde cero a
partir de la app Android (`app-release.apk`).  Solo usa la librería estándar
de **Python 3**: no hay dependencias, no hay Java, no hay emulador.

```
╭── ANILINUX TERMINAL ──────────────────────────────────────────────────────╮
│ Cuenta: Invitado · 2387 animes · 36970 episodios · 4 servidores           │
│ CONTENIDOS  │ Elige la sección con ↑↓ y ábrela con Enter o →              │
│ ‹ Inicio    │                                                             │
│ › Continuar │                                                             │
│ › Explorar  │ …                                                           │
│ › Géneros   │                                                             │
│ › Buscar    │                                                             │
│ › Calendario│                                                             │
│ › Perfil    │                                                             │
│ › Ajustes   │                                                             │
╰───────────────────────────────────────────────────────────────────────────╯
 ↑↓ mover · Enter/→ abrir · q salir
```

## Qué incluye

* **Navegación 100 % de teclado**: flechas ↑↓←→, Enter para abrir, Esc/←
  para volver, `q` para salir.
* **Secciones** equivalentes a la app Android:
  - **Inicio** — Continuar viendo, Novedades, Últimos episodios, Populares,
    Mejor valorados, En emisión, Películas, Audio latino, Audio castellano.
  - **Continuar** — capítulos empezados y no terminados, ordenados por
    actividad reciente.  Muestra barra de progreso y tiempo restante; Enter
    reanuda en la posición guardada.
  - **Explorar** — Todos, En emisión, Finalizados, Películas, OVA, ONA,
    Especiales, Populares, Mejor valorados…
  - **Géneros** — lista completa de géneros con recuento.
  - **Buscar** — búsqueda por nombre/nombre alternativo.
  - **Calendario** — emisiones por día de la semana.
  - **Perfil** — tu cuenta.
  - **Ajustes** — sincronización, reproductor, estado del catálogo.
* **Progreso persistente**: el avance se guarda cada ~5 s.  Al dejar un
  capítulo a medias aparece en Continuar; solo se marca como visto al llegar
  al 95 % o a menos de 15 s del final.
* **Catálogo offline completo**: al primer arranque importa la base de
  datos `assets/databases/kawaii_animes.db` incluida dentro del propio
  APK (~2 400 animes, ~37 000 episodios, ~160 000 enlaces).  Si no hay
  APK, reconstruye el catálogo completo desde el API paginando
  `POST /bulk-sync` (`server` → `anime` → `episode` → `player`).
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
* (Recomendado) guarda el APK de la app como `~/Descargas/app-release.apk`:
  al primer arranque importa el catálogo completo (~2 400 animes,
  ~37 000 episodios) en un segundo, sin conexión.  Sin APK también funciona,
  pero el primer arranque reconstruye el catálogo completo desde el API
  (puede tardar varios minutos).
* Arranca y listo:
  ```bash
  anilinux
  ```

**Desinstalación**

```bash
rm ~/.local/bin/anilinux
rm -rf ~/anilinux   # o la ruta donde lo hayas clonado
rm -rf ~/.local/share/anilinux-terminal ~/.config/anilinux-terminal
```

## Uso

```bash
anilinux                              # arrancar
```

Instalación del comando: se crea un symlink
`~/.local/bin/anilinux → ~/anilinux/anilinux`.  Si el `~/.local/bin`
no está en tu PATH, el `.bashrc` instalado lo añade automáticamente; si
mueves el proyecto a otra ruta, recrea el symlink con
`ln -sf RUTA/nueva/anilinux ~/.local/bin/anilinux`.

Si prefieres, instala a mano el catálogo sin TUI interactiva:

```bash
python3 -c "import sys; sys.path.insert(0,'$HOME/anilinux'); \
from anilinux_tui.database import Catalog; \
d=Catalog('$HOME/.local/share/anilinux-terminal/catalog.db'); \
print(d.import_from_apk('$HOME/Descargas/app-release.apk'))"
```

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
* Este programa no es original ni pretende serlo. Unicamente busca adaptar la aplicación kawaii animes a linux, con el fin de hacer accesible el contenido al mayor número de personas posible. Todo el crédito a: https://github.com/animelhd/kawaii-animes  
  
