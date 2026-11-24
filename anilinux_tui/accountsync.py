# -*- coding: utf-8 -*-
"""Sincronización de la cuenta al arrancar.

Descarga el perfil, reintenta los cambios que el servidor no confirmó y
fusiona las listas del servidor, todo de forma silenciosa y antes de entrar
en la TUI.  Es la misma operación que el menú «Sincronizar mis listas», pero
automática y **no destructiva**: una respuesta vacía o un fallo de red nunca
borra favoritos, progreso ni listas locales.
"""
from .api import ApiError, parse_lists, parse_user

_LIST_KEYS = ("f", "favorites", "w", "watching", "wl", "watchLater",
              "v", "views", "pr", "p", "progress")

# clave del API, tabla local y operación para volver a subir una alta local
_LIST_TABLES = (
    ("favorites", "UserFavorite", "add_favorite"),
    ("watching", "UserWatching", "add_watching"),
    ("watchlater", "UserWatchLater", "add_watchlater"),
    ("views", "UserView", "add_view"),
)
_ADD_OP = {table: op for _, table, op in _LIST_TABLES}
_OP_TABLE = {
    "add_favorite": "UserFavorite", "remove_favorite": "UserFavorite",
    "add_watching": "UserWatching", "remove_watching": "UserWatching",
    "add_watchlater": "UserWatchLater", "remove_watchlater": "UserWatchLater",
    "add_view": "UserView", "remove_view": "UserView",
}
_SIMPLE_OPS = frozenset(_OP_TABLE)


def _valid_lists_payload(payload):
    """Solo se acepta una respuesta que parezca contener listas reales.

    ``merge_server_lists`` reemplaza las tablas del usuario: una respuesta
    vacía no debe borrar las listas locales.
    """
    if not isinstance(payload, dict) or payload.get("s") is False:
        return False
    return any(k in payload for k in _LIST_KEYS)


def _replay(api, db, session):
    """Reintenta las operaciones pendientes.

    Devuelve ``(ok, total, enviadas)``, donde ``enviadas`` son las filas que
    el servidor aceptó y que ya no deben volver a encolarse.
    """
    pending = db.pending_sync(session.user_id)
    done, sent = [], []
    for row in pending:
        op = row["op"]
        try:
            if op in _SIMPLE_OPS:
                getattr(api, op)(session.token, row["anime_id"])
            elif op == "progress":
                api.update_progress(
                    session.token, row["episode_id"], row["anime_id"],
                    row["episode_number"], row["position_ms"],
                    row["duration_ms"], bool(row["completed"]),
                    row["updated_at"] or None)
            elif op == "delete_progress":
                api.delete_progress(session.token, row["anime_id"])
            else:
                done.append(row["id"])   # operación desconocida: se descarta
                continue
        except (ApiError, OSError):
            continue                     # se conserva para el próximo arranque
        done.append(row["id"])
        sent.append(row)
    if done:
        db.clear_pending_sync(done)
    return len(done), len(pending), sent


def _restore_local(db, session, lists, local_lists, local_progress,
                   pending, sent):
    """Conserva cambios locales que el servidor aún no tiene.

    Se ejecuta después de ``merge_server_lists`` (que reemplaza las tablas),
    usando las copias tomadas antes de la fusión.
    """
    uid = session.user_id
    adds, removes = {}, {}
    for row in pending:
        table = row["table_name"] or _OP_TABLE.get(row["op"], "")
        if not table:
            continue
        if row["op"].startswith("add_"):
            adds.setdefault(table, set()).add(row["anime_id"])
        elif row["op"].startswith("remove_"):
            removes.setdefault(table, set()).add(row["anime_id"])
    pending_progress = {r["episode_id"] for r in pending
                        if r["op"] == "progress"}
    sent_adds = {(r["table_name"] or _OP_TABLE.get(r["op"], ""), r["anime_id"])
                 for r in sent if r["op"].startswith("add_")}
    sent_progress = {r["episode_id"] for r in sent if r["op"] == "progress"}

    for key, table, add_op in _LIST_TABLES:
        server_ids = {it["id"] for it in lists[key]}
        # Un borrado local pendiente no debe ser resurrectado por el servidor.
        for aid in removes.get(table, ()):
            db.remove_list(table, uid, aid)
        # Un alta local pendiente se conserva aunque el servidor no la traiga.
        for aid, ua in local_lists.get(table, {}).items():
            if aid in server_ids or aid in removes.get(table, ()):
                continue
            db.add_list(table, uid, aid, updated_at=ua)
            if (session.token and aid not in adds.get(table, ()) and
                    (table, aid) not in sent_adds):
                db.queue_sync(add_op, uid, table_name=table, anime_id=aid,
                              updated_at=ua)

    server_progress = {it["eid"]: it for it in lists["progress"] if it["eid"]}
    for eid, row in local_progress.items():
        srv = server_progress.get(eid)
        newer = (not srv or
                 (row.get("updated_at") or "") > (srv.get("ua") or ""))
        if not newer and eid not in pending_progress:
            continue
        db.put_progress(uid, eid, row["anime_id"], row["episode_number"],
                        row["position_ms"], row["duration_ms"],
                        bool(row["completed"]),
                        updated_at=row["updated_at"])
        if (session.token and eid not in pending_progress and
                eid not in sent_progress):
            db.queue_sync("progress", uid, episode_id=eid,
                          anime_id=row["anime_id"],
                          episode_number=row["episode_number"],
                          position_ms=row["position_ms"],
                          duration_ms=row["duration_ms"],
                          completed=bool(row["completed"]),
                          updated_at=row["updated_at"])


def sync(api, db, session):
    """Sincroniza perfil, cambios pendientes y listas.

    Devuelve ``(estado, detalle)`` donde estado es ``synced``, ``no_session``
    o ``failed``.
    """
    if not session.is_logged:
        return "no_session", "sin sesión iniciada"

    # 1) Perfil: refresca nombre, puntos y estado de la cuenta.
    try:
        fresh = parse_user(api.get_user(session.token))
    except (ApiError, OSError) as exc:
        return "failed", "perfil: " + str(exc)
    if fresh.get("id"):
        session.update_user(fresh)

    uid = session.user_id

    # 2) Cambios que no llegaron al servidor (modo sin conexión, etc.).
    replayed, total_pending, sent = _replay(api, db, session)

    # 3) Copia de seguridad de lo que hay en local antes de fusionar.
    local_lists = db.local_list_map(uid)
    local_progress = db.local_progress_map(uid)

    # 4) Listas del servidor.
    try:
        payload = api.get_all_lists(session.token)
    except (ApiError, OSError) as exc:
        return "failed", "listas: " + str(exc)
    if not _valid_lists_payload(payload):
        return "failed", "listas: respuesta inesperada del servidor"

    lists = parse_lists(payload)
    db.merge_server_lists(uid, lists)

    # 5) Reaplica lo que el servidor aún no conoce.
    pending = db.pending_sync(uid)
    _restore_local(db, session, lists, local_lists, local_progress,
                   pending, sent)

    detail = ("%d fav · %d viendo · %d luego · %d vistos · %d progreso" % (
        len(lists["favorites"]), len(lists["watching"]),
        len(lists["watchlater"]), len(lists["views"]),
        len(lists["progress"])))
    if replayed:
        detail += " · %d/%d pendientes enviados" % (replayed, total_pending)
    left = len(pending)
    if left:
        detail += " · %d pendientes" % left
    return "synced", detail
