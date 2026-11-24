# -*- coding: utf-8 -*-
"""Punto de entrada de AniLinux Terminal."""
import argparse
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile

from . import api as api_mod
from . import config
from . import selfupdate
from .app import App
from .database import Catalog
from .session import Session


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _apk_signature(path):
    """Firma del catálogo interno del APK.

    Usa el CRC y el tamaño de `kawaii_animes.db`, no la ruta ni la fecha del
    archivo: volver a descargar el mismo APK no provoca una fusión inútil y
    dos APK con el mismo catálogo se consideran iguales.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.filename.endswith("kawaii_animes.db"):
                    return "%s:%d:%d" % (info.filename, info.file_size,
                                         info.CRC)
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def _download_apk(dest=None, url=None, progress=log):
    """Descarga y valida el APK oficial.  Devuelve la ruta final.

    Escribe primero en `<dest>.part`; si la descarga o la validación fallan,
    deja intacto el APK anterior y propaga el error.
    """
    dest = dest or config.CACHED_APK_PATH
    url = url or config.OFFICIAL_APK_URL
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={
        "User-Agent": config.USER_AGENT,
        "Accept": "application/octet-stream,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            next_mb = 5
            reported_pct = -10
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct >= reported_pct + 10 or pct >= 100:
                        progress("[catálogo] Descargando APK: %d%% (%d/%d MB)"
                                 % (pct, done >> 20, total >> 20))
                        reported_pct = pct
                elif done >= (next_mb << 20):
                    progress("[catálogo] Descargando APK: %d MB"
                             % (done >> 20))
                    next_mb = (done >> 20) + 5
        if not zipfile.is_zipfile(tmp):
            raise ValueError("el archivo descargado no es un APK/ZIP válido")
        os.replace(tmp, dest)
        return dest
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _import_apk(db, apk):
    """Importa el catálogo inicial y recuerda la firma del APK.

    Nunca reemplaza la base de datos si ya contiene listas, progreso o
    descargas: en ese caso fusiona el catálogo del APK.
    """
    if db.has_user_data():
        players, n, total = db.import_from_apk_merge(apk)
        db._repair_embeds()
        db.con.commit()
    else:
        n = db.import_from_apk(apk)
        db._repair_embeds()
        db.con.commit()
    db.set_meta("apk_sig", _apk_signature(apk))
    return n


def _try_import(db, apk):
    """Importa un APK sin abortar el arranque; devuelve True si hay catálogo."""
    if not apk or not os.path.isfile(apk):
        return False
    log(f"[catálogo] Importando catálogo desde el APK: {apk}")
    try:
        n = _import_apk(db, apk)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile,
            sqlite3.DatabaseError) as exc:
        log(f"[catálogo] No se pudo importar el APK: {exc}")
        return False
    log(f"[catálogo] Importado: {n} animes, "
        f"{db.count_episodes()} episodios.")
    return db.count_animes() > 0


def _merge_apk(db, apk):
    """Fusiona un APK nuevo sin tocar listas, progreso ni descargas."""
    players, animes, total = db.import_from_apk_merge(apk)
    db.set_meta("apk_sig", _apk_signature(apk))
    db._repair_embeds()
    db.con.commit()
    return players, animes, total


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="anilinux",
        description="Kawaii Animes — cliente de terminal para Linux.")
    parser.add_argument("--apk", metavar="RUTA",
                        help="APK del que importar el catálogo "
                             "(assets/databases/kawaii_animes.db)")
    parser.add_argument("--update", action="store_true",
                        help="descarga el APK oficial más reciente y "
                             "actualiza el catálogo")
    parser.add_argument("--bootstrap", action="store_true",
                        help="fuerza una actualización completa del catálogo "
                             "desde el APK oficial (conserva listas y progreso)")
    parser.add_argument("--no-download", action="store_true",
                        help="no descargar el APK oficial si no hay catálogo")
    parser.add_argument("--no-sync", action="store_true",
                        help="no sincronizar incrementalmente al arrancar")
    parser.add_argument("--self-update", action="store_true",
                        help="comprobar actualizaciones del programa ahora")
    parser.add_argument("--no-self-update", action="store_true",
                        help="no actualizar el programa en este arranque")
    parser.add_argument("--version", action="version",
                        version=f"AniLinux Terminal {config.APP_VERSION}")
    args = parser.parse_args(argv)

    config.ensure_dirs()

    # --- actualización automática del programa (independiente del catálogo) -
    # El APK actualiza los datos; git actualiza el código.  Si hay una versión
    # nueva y el árbol de trabajo está limpio, se aplica un fast-forward y se
    # reinicia la TUI para que use el código nuevo.
    if not args.no_self_update:
        force = args.self_update or args.update or args.bootstrap
        if selfupdate.should_check(force=force):
            status, detail = selfupdate.check(force=force)
            if status == "updated":
                log(f"[programa] Actualizado {detail}; reiniciando…")
                selfupdate.reexec()
            elif force and status in ("skipped", "failed", "unavailable"):
                log(f"[programa] No se actualizó: {detail}")

    settings = config.load_settings()
    api_ref = api_mod.KawaiiApi()
    session = Session()

    db = Catalog(config.DB_PATH)
    try:
        force_update = args.update or args.bootstrap
        apk = args.apk or config.find_apk()
        catalog_ready = db.count_animes() > 0

        # --- primer arranque: conseguir un catálogo válido --------------
        if not catalog_ready:
            if args.apk:
                catalog_ready = _try_import(db, args.apk)

            # El APK oficial es la fuente de verdad: se prefiere aunque haya
            # un APK viejo en Descargas.  Así un clon nuevo nunca importa
            # una lista desactualizada.
            if not catalog_ready and not args.no_download:
                log("[catálogo] Sin catálogo local; descargando el APK "
                    "oficial…")
                log("            (~25 MB; solo la primera vez)")
                try:
                    apk = _download_apk(progress=log)
                    catalog_ready = _try_import(db, apk)
                except (urllib.error.URLError, OSError, ValueError,
                        zipfile.BadZipFile) as exc:
                    log(f"[catálogo] No se pudo descargar el APK: {exc}")

            # Sin conexión (o con --no-download): usa un APK local.
            if not catalog_ready and not args.apk:
                apk = apk or config.find_apk()
                if apk:
                    catalog_ready = _try_import(db, apk)

            # Último recurso heredado: el API v4 (hoy solo trae servidores).
            if not catalog_ready:
                log("[catálogo] Probando el API como último recurso…")
                try:
                    n = db.bootstrap_from_api(
                        api_ref, progress=lambda t, i:
                        log(f"[catálogo] {t}: hasta id {i}"))
                except (api_mod.ApiError, OSError) as exc:
                    log(f"[catálogo] ERROR: {exc}")
                    n = 0
                if n:
                    log(f"[catálogo] Importados {n} animes desde el API.")
                    catalog_ready = True

            if not catalog_ready:
                log("[catálogo] No hay catálogo disponible; no se ha borrado "
                    "nada.")
                log("            Conecta a internet y vuelve a ejecutar, o "
                    "pasa --apk RUTA.")
                return 1

        # --- actualización forzada: descarga el APK más reciente --------
        if catalog_ready and force_update and not args.no_download:
            log("[catálogo] Buscando la versión más reciente del APK "
                "oficial…")
            try:
                apk = _download_apk(progress=log)
            except (urllib.error.URLError, OSError, ValueError,
                    zipfile.BadZipFile) as exc:
                log(f"[catálogo] No se pudo descargar el APK: {exc}")
                log("            Se conserva el catálogo actual.")

        # --- fusión no destructiva si el APK trae otro catálogo ---------
        if catalog_ready:
            merge_apk = args.apk
            if not merge_apk and os.path.isfile(config.CACHED_APK_PATH):
                merge_apk = config.CACHED_APK_PATH
            if not merge_apk and force_update and apk and os.path.isfile(apk):
                merge_apk = apk
            if merge_apk:
                sig = _apk_signature(merge_apk)
                if sig and sig != db.get_meta("apk_sig"):
                    log(f"[catálogo] Fusionando catálogo del APK: {merge_apk}")
                    try:
                        players, animes, total = _merge_apk(db, merge_apk)
                        log(f"[catálogo] Fusión aplicada: {animes} animes, "
                            f"{total} players ({players} nuevos).")
                    except (OSError, ValueError, KeyError, zipfile.BadZipFile,
                            sqlite3.DatabaseError) as exc:
                        log(f"[catálogo] No se pudo fusionar el APK: {exc}")
                elif force_update:
                    log("[catálogo] El catálogo ya está al día.")

        if not args.no_sync:
            try:
                t0 = time.time()
                applied = db.incremental_sync(api_ref)
                if applied or time.time() - t0 > 0.1:
                    log(f"[sync] Incremental: {applied} filas actualizadas.")
                db.set_meta("last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
            except api_mod.ApiError as exc:
                log(f"[sync] Aviso: {exc} (se usa el catálogo local)")

        app = App(api_ref, db, session, settings)
        app.run()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

