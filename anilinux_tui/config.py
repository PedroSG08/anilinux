# -*- coding: utf-8 -*-
"""Configuración y rutas de AniLinux Terminal."""
import json
import os
import sys

APP_NAME = "AniLinux Terminal"
APP_VERSION = "1.0.0"

# API pública de Kawaii Animes (mismo protocolo que usa la app Android)
BASE_URL = "https://api.kawaiianimes.app/api/v4"
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
ACCEPT_LANGUAGE = "es-419,es;q=0.9,en;q=0.8"

# --- Rutas -------------------------------------------------------------
# Compatibilidad: siguen aceptándose las variables KAWAII_* antiguas.
def _env(var, old):
    return os.environ.get(var) or os.environ.get(old)

def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)

DATA_DIR = _env("ANILINUX_DATA_DIR", "KAWAII_DATA_DIR") \
    or _home(".local", "share", "anilinux-terminal")
CONFIG_DIR = _env("ANILINUX_CONFIG_DIR", "KAWAII_CONFIG_DIR") \
    or _home(".config", "anilinux-terminal")
DB_PATH = os.path.join(DATA_DIR, "catalog.db")
SESSION_PATH = os.path.join(CONFIG_DIR, "session.json")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

# directorios usados por versiones antiguas (se migran al primer arranque)
_OLD_DIRS = {
    _home(".local", "share", "kawaii-linux"): DATA_DIR,
    _home(".config", "kawaii-linux"): CONFIG_DIR,
}

CANDIDATE_APKS = [
    _home("Descargas", "app-release.apk"),
    _home("Downloads", "app-release.apk"),
    _home("Descargas", "Descargas", "app-release.apk"),
]

def _migrate_old_dirs():
    """Tras el renombrado mueve los datos de ~/.config/kawaii-linux y
    ~/.local/share/kawaii-linux si el destino aún no existe."""
    for old, new in _OLD_DIRS.items():
        if old != new and os.path.isdir(old) and not os.path.exists(new):
            try:
                os.rename(old, new)
            except OSError:
                pass      # migración best-effort: se crea el nuevo vacío

def ensure_dirs():
    _migrate_old_dirs()
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)

def find_apk():
    """Busca el APK en rutas habituales o en las variables ANILINUX_APK / KAWAII_APK."""
    env = _env("ANILINUX_APK", "KAWAII_APK")
    cands = [env] if env else []
    cands.extend(CANDIDATE_APKS)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None

# --- Preferencias -------------------------------------------------------
DEFAULT_SETTINGS = {
    "player": "auto",          # auto | mpv | browser
    "incremental_sync": True,  # sincronizar catálogo al arrancar
    "last_language": "0",      # idioma preferido para reproducir
}

def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        s = dict(DEFAULT_SETTINGS)
        s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        return s
    except Exception:
        return dict(DEFAULT_SETTINGS)

def save_settings(settings):
    ensure_dirs()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass

# --- Etiquetas comunes ---------------------------------------------------
LANGUAGE_LABELS = {"0": "Subtitulado", "1": "Latino", "2": "Castellano"}
LANGUAGE_SHORT = {"0": "SUB", "1": "LAT", "2": "CAS"}
TYPE_LABELS = {"TV": "Serie", "Movie": "Película", "OVA": "OVA", "ONA": "ONA", "Special": "Especial"}
STATUS_LABELS = {0: "Finalizado", 1: "En emisión"}
BROADCAST_DAYS = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves",
                  5: "Viernes", 6: "Sábado", 7: "Domingo"}

def fmt_views(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def safe_terminal_size(fallback=(100, 30)):
    try:
        import shutil
        size = shutil.get_terminal_size(fallback)
        return size.columns, size.lines
    except Exception:
        return fallback
