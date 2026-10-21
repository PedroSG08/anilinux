# -*- coding: utf-8 -*-
"""Punto de entrada de AniLinux Terminal."""
import argparse
import os
import sys
import time
import zipfile

from . import api as api_mod
from . import config
from .app import App
from .database import Catalog
from .session import Session


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="anilinux",
        description="Kawaii Animes — cliente de terminal para Linux.")
    parser.add_argument("--apk", metavar="RUTA",
                        help="APK del que importar el catálogo inicial "
                             "(assets/databases/kawaii_animes.db)")
    parser.add_argument("--bootstrap", action="store_true",
                        help="reconstruye el catálogo descargándolo entero "
                             "desde el API (si no hay APK disponible)")
    parser.add_argument("--no-sync", action="store_true",
                        help="no sincronizar incrementalmente al arrancar")
    parser.add_argument("--version", action="version",
                        version=f"AniLinux Terminal {config.APP_VERSION}")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    settings = config.load_settings()
    api_ref = api_mod.KawaiiApi()
    session = Session()

    rebuilt = False
    if args.bootstrap:
        if os.path.exists(config.DB_PATH):
            try:
                os.remove(config.DB_PATH)
            except OSError:
                pass
        rebuilt = True

    db = Catalog(config.DB_PATH)
    try:
        if db.count_animes() == 0:
            apk = args.apk or config.find_apk()
            if apk:
                log(f"[catálogo] Importando catálogo desde el APK: {apk}")
                try:
                    n = db.import_from_apk(apk)
                    log(f"[catálogo] Importado: {n} animes, "
                        f"{db.count_episodes()} episodios.")
                except (FileNotFoundError, OSError, zipfile.BadZipFile) as exc:
                    log(f"[catálogo] No se pudo importar del APK: {exc}")
                    apk = None
            if db.count_animes() == 0:
                log("[catálogo] Sin catálogo local. Descargando desde el API…")
                log("            (puede tardar varios minutos)")
                try:
                    db.bootstrap_from_api(api_ref, progress=lambda t, i:
                        log(f"[catálogo] {t}: hasta id {i}"))
                    log(f"[catálogo] Completado: {db.count_animes()} animes.")
                except (api_mod.ApiError, OSError) as exc:
                    log(f"[catálogo] ERROR: {exc}")
                    log("            Primer arranque necesita conexión o un APK "
                        "con catálogo (usa --apk).")
                    return 1

        if not args.no_sync and not rebuilt:
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

