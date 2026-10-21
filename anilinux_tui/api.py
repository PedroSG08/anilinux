# -*- coding: utf-8 -*-
"""Cliente HTTP del API de Kawaii Animes (v4).

Implementa el mismo protocolo que usa la app Android:
  * Catálogo público:  POST /sync,  POST /bulk-sync?target=&last_id=&limit=
  * Autenticación:     POST /auth/token, /auth/register, /auth/logout, GET /auth/user
  * Listas de usuario: GET /auth/lists/all, /auth/{favorites|watching|watchlater|views}/{add|remove}
  * Progreso:          POST /auth/progress/update, /auth/progress/delete-anime

Los errores del servidor llegan como {"s": false, "code": "...", "msg": "..."}.
"""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class ApiError(Exception):
    """Error de la API con código y mensaje legible."""

    MESSAGES = {
        "EMAIL_NOT_FOUND": "El correo electrónico no está registrado.",
        "INVALID_PASSWORD": "Contraseña incorrecta.",
        "EMAIL_IN_USE": "Ese correo ya está registrado.",
        "INVALID_CODE": "El código de recuperación no es válido o expiró.",
        "WEAK_PASSWORD": "La contraseña es demasiado débil.",
        "INVALID_EMAIL": "El correo electrónico no es válido.",
        "RATE_LIMITED": "Demasiados intentos. Espera un momento e inténtalo de nuevo.",
        "UNAUTHORIZED": "Sesión no válida o expirada.",
        "FORBIDDEN": "Acceso denegado por el servidor.",
        "NOT_FOUND": "Recurso no encontrado.",
        "SERVER_ERROR": "El servidor tuvo un problema. Inténtalo más tarde.",
    }

    def __init__(self, status, code="", msg=""):
        self.status = status
        self.code = code or ""
        self.msg = self.MESSAGES.get(code, msg or "Error desconocido.")
        super().__init__(self.msg)


class NetworkError(ApiError):
    def __init__(self, reason=""):
        super().__init__(0, "NETWORK", f"Sin conexión con el servidor ({reason}).")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class KawaiiApi:
    """Fachada del API. Todas las llamadas son síncronas."""

    def __init__(self, base_url=None, timeout=15):
        self.base = (base_url or config.BASE_URL).rstrip("/")
        self.timeout = timeout
        self._ctx = ssl.create_default_context()

    # ------------------------------------------------------------------ #
    # transporte
    # ------------------------------------------------------------------ #
    def _call(self, method, path, body=None, token=None, query=None,
              content_type="application/json"):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {
            "Accept": "application/json",
            "Accept-Language": config.ACCEPT_LANGUAGE,
            "User-Agent": config.USER_AGENT,
        }
        if body is not None:
            if content_type == "application/json":
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            else:
                data = urllib.parse.urlencode(body).encode("utf-8")
            headers["Content-Type"] = content_type
        if token:
            headers["Authorization"] = "Bearer " + token

        last_exc = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                    return self._parse(resp.status, raw)
            except urllib.error.HTTPError as exc:
                raw = ""
                try:
                    raw = exc.read().decode("utf-8", "replace")
                except Exception:
                    pass
                payload = self._parse(exc.code, raw)
                msg = (payload.get("msg") or payload.get("message") or "")
                raise ApiError(exc.code, payload.get("code", ""), msg) from None
            except urllib.error.URLError as exc:
                last_exc = exc
                time.sleep(0.6 * (attempt + 1))
            except TimeoutError as exc:
                last_exc = exc
                time.sleep(0.6 * (attempt + 1))
            except ssl.SSLError as exc:
                last_exc = exc
                time.sleep(0.6 * (attempt + 1))
        raise NetworkError(str(last_exc or "timeout")) from last_exc

    @staticmethod
    def _parse(status, raw):
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        if isinstance(payload, dict):
            payload.setdefault("_status", status)
            return payload
        return {"_list": payload, "_status": status}

    # ------------------------------------------------------------------ #
    # catálogo público
    # ------------------------------------------------------------------ #
    def bulk_sync(self, target, last_id, limit):
        return self._call("POST", "/bulk-sync", query={
            "target": target, "last_id": last_id, "limit": limit,
        })

    def sync(self, lei, laua, lpua):
        """Sincronización incremental con cursores (lei=último id de episodio,
        laua/lpua = última fecha de actualización de animes/players)."""
        return self._call("POST", "/sync", body={
            "lei": str(int(lei)), "laua": str(laua), "lpua": str(lpua),
        })

    # ------------------------------------------------------------------ #
    # autenticación
    # ------------------------------------------------------------------ #
    def login(self, email, password):
        return self._call("POST", "/auth/token", body={"e": email, "p": password})

    def register(self, name, email, password):
        return self._call("POST", "/auth/register", body={"n": name, "e": email, "p": password})

    def logout(self, token):
        return self._call("GET", "/auth/logout", token=token)

    def get_user(self, token):
        return self._call("GET", "/auth/user", token=token)

    def get_all_lists(self, token):
        # El server usaba POST en /auth/lists/all (GET responde 405).
        return self._call("POST", "/auth/lists/all", token=token)

    def request_password_code(self, email):
        return self._call("POST", "/auth/password/code", body={"e": email})

    def restore_password(self, email, code, new_password):
        return self._call("POST", "/auth/password/restore",
                          body={"e": email, "c": code, "p": new_password})

    # ------------------------------------------------------------------ #
    # listas de usuario (favoritos / viendo / ver más tarde / vistos)
    # ------------------------------------------------------------------ #
    def _list_op(self, token, path, anime_id, updated_at=None):
        body = {"aid": int(anime_id), "ua": updated_at or _now()}
        return self._call("POST", path, body=body, token=token)

    def add_favorite(self, token, anime_id):
        return self._list_op(token, "/auth/favorites/add", anime_id)

    def remove_favorite(self, token, anime_id):
        return self._list_op(token, "/auth/favorites/remove", anime_id)

    def add_watching(self, token, anime_id):
        return self._list_op(token, "/auth/watching/add", anime_id)

    def remove_watching(self, token, anime_id):
        return self._list_op(token, "/auth/watching/remove", anime_id)

    def add_watchlater(self, token, anime_id):
        return self._list_op(token, "/auth/watchlater/add", anime_id)

    def remove_watchlater(self, token, anime_id):
        return self._list_op(token, "/auth/watchlater/remove", anime_id)

    def add_view(self, token, anime_id):
        return self._list_op(token, "/auth/views/add", anime_id)

    def remove_view(self, token, anime_id):
        return self._list_op(token, "/auth/views/remove", anime_id)

    def update_progress(self, token, episode_id, anime_id, episode_number,
                        position_ms=0, duration_ms=0, completed=True,
                        updated_at=None):
        body = {
            "eid": int(episode_id), "aid": int(anime_id),
            "en": int(episode_number), "pos": int(position_ms),
            "dur": int(duration_ms), "c": bool(completed),
            "ua": updated_at or _now(),
        }
        return self._call("POST", "/auth/progress/update", body=body, token=token)

    def delete_progress(self, token, anime_id):
        return self._call("POST", "/auth/progress/delete-anime",
                          body={"aid": int(anime_id)}, token=token)


# ---------------------------------------------------------------------- #
# accesores tolerantes a claves cortas/largas
# ---------------------------------------------------------------------- #
def first(payload, *keys, default=None):
    for k in keys:
        if isinstance(payload, dict) and k in payload and payload[k] is not None:
            return payload[k]
    return default


def parse_user(payload):
    """UserDto -> dict homogéneo {id,name,email,avatar,is_premium,points,premium}."""
    u = first(payload, "u", "user", default=payload or {})
    return {
        "id": int(first(u, "id", default=0) or 0),
        "name": str(first(u, "n", "name", default="") or ""),
        "email": str(first(u, "e", "email", default="") or ""),
        "avatar": first(u, "a", "avatar"),
        "is_premium": int(first(u, "ip", "isPremium", "iprem", default=0) or 0),
        "points": int(first(u, "pk", "pointKawaii", "points", default=0) or 0),
        "premium_entitlement": first(u, "pe", "premiumEntitlement"),
    }


def parse_auth(payload):
    """AuthResponseDto -> (token, user_dict).

    El probador del v4 usa claves cortas: tk = token, u = usuario
    (AuthResponseDto$$serializer: elementos "tk" y "u").
    """
    token = first(payload, "tk", "t", "token", default="")
    user = parse_user(payload)
    return token, user


def parse_lists(payload):
    """UserDataDto -> dict de listas normalizadas.

    Cada elemento ListItemDto es {id, ua}; el progreso además incluye
    eid/aid/en/pos/dur/c/ua.  Se aceptan claves cortas y largas.
    """
    def items(root, *keys):
        raw = first(root, *keys, default=[]) or []
        out = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            out.append({
                "id": int(first(it, "id", "aid", default=0) or 0),
                "ua": str(first(it, "ua", default="") or ""),
                "eid": int(first(it, "eid", "episodeId", default=0) or 0),
                "aid": int(first(it, "aid", "animeId", default=0) or 0),
                "en": int(first(it, "en", "episodeNumber", default=0) or 0),
                "pos": int(first(it, "pos", "positionMs", default=0) or 0),
                "dur": int(first(it, "dur", "durationMs", default=0) or 0),
                "c": bool(first(it, "c", "completed", default=False)),
            })
        return out

    return {
        "favorites": items(payload, "f", "favorites"),
        "watching": items(payload, "w", "watching"),
        "watchlater": items(payload, "wl", "watchLater"),
        "views": items(payload, "v", "views"),
        # el server envía el progreso como "pr"
        "progress": items(payload, "pr", "p", "progress"),
    }
