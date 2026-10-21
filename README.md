# AniLinux Terminal — Kawaii Animes en tu terminal

Cliente nativo para GNU/Linux de **Kawaii Animes**, reescrito desde cero a
partir de la app Android (`app-release.apk`).  Solo usa la librería estándar
de **Python 3**: no hay dependencias, no hay Java, no hay emulador.

```
╭── ANILINUX TERMINAL ──────────────────────────────────────────────────────╮
│ Cuenta: Invitado · 2325 animes · 36490 episodios · 4 servidores      │
│ CONTENIDOS  │ Elige la sección con ↑↓ y ábrela con Enter o →         │
│ ‹ Inicio    │                                                        │
│ › Explorar  │                                                          │
│ › Géneros   │ …                                                        │
│ › Buscar    │                                                          │
│ › Calendario│                                                          │
│ › Perfil    │                                                          │
│ › Ajustes   │                                                          │
╰───────────────────────────────────────────────────────────────────────╯
 ↑↓ mover · Enter/→ abrir · q salir
```

## Qué incluye

* **Navegación 100 % de teclado**: flechas ↑↓←→, Enter para abrir, Esc/←
  para volver, `q` para salir.
* **Secciones** equivalentes a la app Android:
  - **Inicio** — Continuar viendo, Novedades, Últimos episodios, Populares,
    Mejor valorados, En emisión, Películas, Audio latino, Audio castellano.
  - **Explorar** — Todos, En emisión, Finalizados, Películas, OVA, ONA,
    Especiales, Populares, Mejor valorados…
  - **Géneros** — lista completa de géneros con recuento.
  - **Buscar** — búsqueda por nombre/nombre alternativo.
  - **Calendario** — emisiones por día de la semana.
  - **Perfil** — tu cuenta.
  - **Ajustes** — sincronización, reproductor, estado del catálogo.
* **Catálogo offline completo**: al primer arranque importa la base de
  datos `assets/databases/kawaii_animes.db` incluida dentro del propio
  APK (~2 300 animes, ~36 000 episodios, ~105 000 enlaces).  Si no hay
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
* **Reproducción**: elige anime → episodio → idioma (Sub/Lat/Cas) →
  servidor.  El enlace se abre con `mpv` (si está instalado) o en el
  navegador (`xdg-open`); también puedes copiar la URL al portapapeles.

## Uso

```bash
anilinux                              # arrancar (busca el APK en ~/Descargas)
```

Instalación del comando: se crea un symlink
`~/.local/bin/anilinux → ~/anilinux-terminal/anilinux`.  Si el `~/.local/bin`
no está en tu PATH, el `.bashrc` instalado lo añade automáticamente; si
mueves el proyecto a otra ruta, recrea el symlink con
`ln -sf RUTA/nueva/anilinux ~/.local/bin/anilinux`.

Si prefieres, instala a mano el catálogo sin TUI interactiva:

```python3
python3 -c "import sys; sys.path.insert(0,'/home/pedro/anilinux-terminal'); \
from anilinux_tui.database import Catalog; \
d=Catalog('$HOME/.local/share/anilinux-terminal/catalog.db'); \
print(d.import_from_apk('$HOME/Descargas/app-release.apk'))"
```

## Estructura del proyecto

```
anilinux-terminal/
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
  abre el enlace en el navegador por defecto.
* Las conversaciones con el servidor usan las mismas cabeceras y cuerpo
  JSON que la app (`{"e": correo, "p": contraseña}`, `{"n": nombre, …}`),
  de modo que la cuenta y las listas son **las mismas** que en el móvil.
  
