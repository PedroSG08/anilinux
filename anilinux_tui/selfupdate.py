# -*- coding: utf-8 -*-
"""Actualización automática del programa desde el repositorio Git.

El catálogo se actualiza desde el APK oficial; el código de AniLinux Terminal
vive en ``origin/main``.  Este módulo comprueba si hay una versión nueva al
arrancar y, si puede hacerlo sin tocar cambios locales, aplica un
fast-forward y reinicia el programa con el código nuevo.

 Nunca se ejecuta una actualización si:
   * hay cambios locales en archivos de código (los .pyc se ignoran),
   * la rama actual no es ``main``,
   * el historial local ha divergido de ``origin/main``,
   * no hay conexión con el remoto.
"""
import os
import shutil
import subprocess
import sys
import time

from . import config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(REPO_ROOT, "anilinux")

# Segundos entre comprobaciones automáticas (6 horas).  Las actualizaciones
# forzadas (--update/--bootstrap/--self-update) ignoran este intervalo.
UPDATE_INTERVAL = 6 * 3600
FETCH_TIMEOUT = 12
MERGE_TIMEOUT = 30
BRANCH = "main"
REMOTE = "origin"


def _stamp_path():
    return os.path.join(config.CONFIG_DIR, "last_self_update")


def _git_env():
    env = dict(os.environ)
    # Evita que git espere credenciales de forma interactiva en la TUI.
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args, timeout=FETCH_TIMEOUT):
    try:
        return subprocess.run(["git"] + list(args), cwd=REPO_ROOT,
                              env=_git_env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def available():
    """True si hay git y el directorio del programa es un repositorio."""
    return (bool(shutil.which("git")) and
            os.path.isdir(os.path.join(REPO_ROOT, ".git")))


def _write_stamp():
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        with open(_stamp_path(), "w", encoding="ascii") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def should_check(force=False):
    """¿Toca comprobar actualizaciones en este arranque?"""
    if force:
        return available()
    if os.environ.get("ANILINUX_NO_SELF_UPDATE", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return False
    if not config.load_settings().get("auto_update", True):
        return False
    if not available():
        return False
    try:
        return time.time() - os.path.getmtime(_stamp_path()) >= UPDATE_INTERVAL
    except OSError:
        return True


def _local_changes():
    """Cambios locales en archivos de código (ignora .pyc / __pycache__)."""
    p = _git(["status", "--porcelain", "--untracked-files=no"])
    if p is None or p.returncode != 0:
        return None
    changes = []
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        path = line[3:].strip()
        if path.endswith(".pyc") or "__pycache__" in path:
            continue
        changes.append(line.strip())
    return changes


def check(force=False):
    """Comprueba y aplica la actualización si es segura.

    Devuelve ``(estado, detalle)`` donde estado es uno de:
    ``updated``, ``current``, ``skipped``, ``failed``, ``unavailable``.
    No reinicia el programa: el llamador decide cuándo hacerlo.
    """
    _write_stamp()
    if not available():
        return "unavailable", "git o repositorio no disponible"

    changes = _local_changes()
    if changes is None:
        return "failed", "no se pudo consultar el estado del repositorio"
    if changes:
        return "skipped", "hay cambios locales sin subir: " + ", ".join(changes[:3])

    p = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if p is None or p.returncode != 0:
        return "failed", "no se pudo determinar la rama actual"
    if p.stdout.decode("ascii", "replace").strip() != BRANCH:
        return "skipped", "la rama actual no es " + BRANCH

    p = _git(["fetch", "--quiet", REMOTE, BRANCH], timeout=FETCH_TIMEOUT)
    if p is None or p.returncode != 0:
        return "failed", "no se pudo contactar con " + REMOTE

    local = _git(["rev-parse", "HEAD"])
    remote = _git(["rev-parse", REMOTE + "/" + BRANCH])
    if local is None or remote is None or local.returncode or remote.returncode:
        return "failed", "no se pudieron comparar las versiones"
    old = local.stdout.decode("ascii", "replace").strip()[:7]
    new = remote.stdout.decode("ascii", "replace").strip()[:7]
    if old == new:
        return "current", old

    anc = _git(["merge-base", "--is-ancestor", "HEAD",
                REMOTE + "/" + BRANCH])
    if anc is None or anc.returncode != 0:
        return "skipped", "el historial local ha divergido; se conserva"

    p = _git(["merge", "--ff-only", "--quiet",
              REMOTE + "/" + BRANCH], timeout=MERGE_TIMEOUT)
    if p is None or p.returncode != 0:
        return "failed", "no se pudo aplicar el fast-forward"
    return "updated", "%s → %s" % (old, new)


def reexec():
    """Vuelve a lanzar el programa con el código ya actualizado."""
    script = LAUNCHER if os.path.isfile(LAUNCHER) else sys.argv[0]
    if not os.path.isfile(script):
        return False
    try:
        os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])
    except OSError:
        return False
    return False
