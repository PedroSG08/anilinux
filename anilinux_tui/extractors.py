# -*- coding: utf-8 -*-
"""Resolución de servidores de streaming para AniLinux Terminal.

Replica el flujo de la app oficial de Kawaii Animes:

  · Los enlaces del catálogo están "mezclados"; la app los revierte con
    resolveTokenV28 (libprotection.so) antes de abrir la página. La página
    real es  {servidor.embed}e/{link invertido}.
  · LuluStream (luluvdoo.com): página XUpload con JavaScript empaquetado
    (Dean-Edwards); tras desempaquetar queda  file:"...m3u8".
  · Byse (byseraguci.com):  GET /api/videos/{id}  devuelve el blob
    `playback` (AES-256-GCM con clave derivada de `key_parts` según
    `version`); al descifrar queda JSON con sources[0].url.

Devuelve (url_directa, referer) listos para mpv:
    mpv --referrer <referer> --user-agent <UA-iPhone> <url>

IMPORTANTE (probado contra el CDN): solo Referer + UA-iPhone; añadir más
cabeceras (Sec-Fetch-*, Accept-Language, ...) hace que el nginx devuelva 403.
"""

import base64
import json
import re
import socket
import urllib.error
import urllib.request

from .aesgcm import gcm_decrypt

IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML like Gecko) "
          "Version/16.6 Mobile/15E148 Safari/604.1")
ACCEPT_HTML = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/webp,*/*;q=0.8")
ACCEPT_JSON = "application/json"
ACCEPT_LANG = "es-419,es;q=0.9,en;q=0.8"

TIMEOUT = 15


class ResolveError(Exception):
    """No se pudo resolver el servidor (con mensaje amigable en español)."""


# ------------------------------------------------------------------ URL -- #
def origin_of(url):
    m = re.match(r"^(https?)://([^/?#]+)", url or "", re.I)
    if not m:
        return ""
    return m.group(1).lower() + "://" + m.group(2).lower()


def embed_page_url(server_embed, link):
    """URL de la página de reproducción: embed + 'e/' + link invertido.

    La inversión es exactamente resolveTokenV28 de la app (libprotection.so):
    los enlaces del catálogo van revertidos y la app vuelve a invertirlos
    para construir la URL pública.
    """
    link = (link or "").strip()
    if link.startswith("http"):
        return link
    embed = (server_embed or "").strip()
    if not embed:
        raise ResolveError("el servidor no tiene dirección configurada")
    if not embed.endswith("/"):
        embed += "/"
    return embed + "e/" + link[::-1]


# ----------------------------------------------------------------- red -- #
def _http(url, headers, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ResolveError("el archivo ya no existe (HTTP 404)")
        raise ResolveError(f"el servidor respondió HTTP {e.code}")
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise ResolveError(f"sin conexión con el servidor ({e.__class__.__name__})")


# ------------------------------------------- desempaquetador Dean-Edwards -- #
# eval(function(p,a,c,k,e,d){...}("CODIFICADO",36,233,"a|b|...".split("|"),0,{}))
_RE_PACKED = re.compile(
    r"eval\(function\(p,a,c,k,e,[dj]?\)\{.*?\}\(\s*"
    r"'((?:\\.|[^'\\])+)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"
    r"'((?:\\.|[^'\\])+)'\s*\.split",
    re.S)

_JS_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "/": "/", "n": "\n",
               "r": "\r", "t": "\t", "b": "\b", "f": "\f"}


def _js_unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in _JS_ESCAPES:
                out.append(_JS_ESCAPES[n])
                i += 2
            elif n == "x" and i + 3 < len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 4], 16)))
                except ValueError:
                    out.append("\\x")
                i += 4
            elif n == "u" and i + 5 < len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                except ValueError:
                    out.append("\\u")
                i += 6
            else:
                out.append(n)
                i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _js_radix(n, base):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if base <= 10:
        return str(n)
    out = ""
    while n:
        out = digits[n % base] + out
        n //= base
    return out or "0"


def _js_unpack(body):
    m = _RE_PACKED.search(body)
    if not m:
        return None
    payload = _js_unescape(m.group(1))
    a, c = int(m.group(2)), int(m.group(3))
    words = _js_unescape(m.group(4)).split("|")
    # límites JS de boundary (\\w ASCII); reemplazo descendente como en JS
    for idx in range(c - 1, -1, -1):
        if idx >= len(words) or not words[idx]:
            continue
        word = _js_radix(idx, a)
        p = (r"(?<![0-9A-Za-z_$])" + re.escape(word) +
             r"(?![0-9A-Za-z_$])")
        payload = re.sub(p, lambda _m, t=words[idx]: t, payload)
    return payload


# ------------------------------------------------------- patrones de lk4 -- #
_RE_FILE_ANYQ = re.compile(r'file."(.*?m3u8.*?)"')          # genérico de la app
_RE_FILE_DIRECT = re.compile(r'file:"(https://.*?\.m3u8[^"]*)"')
_RE_SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.S | re.I)
_RE_SRC_ANY = re.compile(r'"file"\s*:\s*"([^"]*?\.m3u8[^"]*)"')
_DEAD_HINTS = ("file is no longer", "file not found", "no such file",
               "archivo no encontrado", "file was deleted")


def _find_m3u8(text):
    m = _RE_FILE_ANYQ.search(text)
    if m:
        u = m.group(1).strip()
        if "m3u8" in u:
            return u
    m = _RE_FILE_DIRECT.search(text)
    if m:
        return m.group(1)
    m = _RE_SRC_ANY.search(text)
    if m:
        return m.group(1)
    return None


def resolve_lulu(page_url):
    """XUpload/LuluStream: página /e/{id} con JS empaquetado (patrones lk4)."""
    html = _http(page_url, {
        "User-Agent": IOS_UA,
        "Accept": ACCEPT_HTML,
        "Accept-Language": ACCEPT_LANG,
        "Upgrade-Insecure-Requests": "1",
    })
    low = html.lower()
    for hint in _DEAD_HINTS:
        if hint in low:
            raise ResolveError("el archivo ya no está disponible en el servidor")

    # primero por script: empaquetado → desempaquetar y desnormalizado (lk4)
    for sb in _RE_SCRIPT.finditer(html):
        t = sb.group(1).strip()
        if not t:
            continue
        if _RE_PACKED.search(t) or t.startswith("eval"):
            try:
                unpacked = _js_unpack(t)
            except Exception:
                unpacked = None
            if unpacked:
                u = _find_m3u8(unpacked)
                if u:
                    return u, page_url
        u = _find_m3u8(t)
        if u:
            return u, page_url

    # última red de seguridad: página entera
    u = _find_m3u8(html)
    if u:
        return u, page_url
    u = re.search(r'(https?://[^"\']{10,}?\.m3u8[^"\']*)', html)
    if u:
        return u.group(1), page_url
    raise ResolveError("no se encontró el enlace del vídeo en la página")


# ----------------------------------------------------------------- Byse -- #
def _b64u(s):
    s = (s or "").strip().replace("-", "+").replace("_", "/")
    if not s:
        return b""
    return base64.b64decode(s + "=" * (-len(s) % 4))


def resolve_byse(page_url):
    """Byse (Delta): GET /api/videos/{id} + AES-256-GCM (flujo yl2)."""
    origin = origin_of(page_url)
    if not origin:
        raise ResolveError("URL de página inválida")
    video_id = page_url.rstrip("/").rsplit("/", 1)[-1]
    headers = {
        "User-Agent": IOS_UA,
        "Accept": ACCEPT_JSON,
        "Accept-Language": ACCEPT_LANG,
        "Referer": page_url,
        "Origin": origin,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    body = _http(origin + "/api/videos/" + video_id, headers)
    try:
        data = json.loads(body)
    except ValueError:
        raise ResolveError("el servidor devolvió una respuesta inválida")
    pb = data.get("playback") or {}
    iv_s, payload_s = pb.get("iv"), pb.get("payload")
    parts = pb.get("key_parts") or []
    if not iv_s or not payload_s or not parts:
        raise ResolveError("el API no entregó datos de reproducción")
    try:
        iv = _b64u(iv_s)
        blob = _b64u(payload_s)
    except Exception:
        raise ResolveError("datos de reproducción ilegibles")

    def _key_from(sel):
        key = b""
        for s in sel:
            key += _b64u(s)
        return key if len(key) in (16, 24, 32) else None

    # rotación por versión (yl2): parts[ver-1] + parts[30-ver]; fuera de rango
    # o sin versión → todas las partes concatenadas
    candidates = []
    v_raw = str(pb.get("version") or "").strip()
    if v_raw.isdigit():
        v = int(v_raw)
        if 1 <= v < 21 and len(parts) > v - 1 and len(parts) > 30 - v:
            candidates.append([parts[v - 1], parts[30 - v]])
    candidates.append(parts)

    plain = None
    for sel in candidates:
        key = _key_from(sel)
        if key is None or len(blob) < 16:
            continue
        try:
            plain = gcm_decrypt(key, iv, blob[:-16], blob[-16:])
        except ValueError:
            plain = None
        if plain is not None:
            break
    if plain is None:
        raise ResolveError("no se pudo descifrar el vídeo (clave caducada)")

    try:
        raw = plain.decode("utf-8", "replace").lstrip("\ufeff")
        info = json.loads(raw)
        sources = info.get("sources") or []
        url = sources[0].get("url")
    except (ValueError, IndexError, AttributeError):
        raise ResolveError("el descifrado devolvió datos inesperados")
    if not url:
        raise ResolveError("la fuente del vídeo no está disponible")
    return url, origin + "/"


# --------------------------------------------------------------- despacho -- #
_HOST_MAP = (
    (("luluvdoo.com", "lulustream.com", "luluvdo.com", "lulu.st"),
     resolve_lulu, "Epsilon"),
    (("byseraguci.com",), resolve_byse, "Delta"),
)


def resolve(player):
    """Resuelva un player del catálogo → (url_m3u8, referer).

    player: fila de players_for_episode (necesita `embed` y `link`).
    """
    page = embed_page_url(player.get("embed"), player.get("link"))
    host = origin_of(page).lower()
    for domains, fn, _label in _HOST_MAP:
        if any(d in host for d in domains):
            return fn(page)
    raise ResolveError("servidor sin soporte de reproducción (" +
                       (host or "host desconocido") + ")")


def try_resolve(player, timeout=TIMEOUT):
    """resolve() con tiempo tope — para iterar varios servidores."""
    import threading
    box = {}

    def work():
        try:
            box["ok"] = resolve(player)
        except ResolveError as e:
            box["err"] = str(e)
        except Exception as e:  # red inesperada, etc.
            box["err"] = f"error inesperado ({e.__class__.__name__})"

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, f"time out tras {timeout}s"
    if "ok" in box:
        return box["ok"], None
    return None, box.get("err", "fallo desconocido")
