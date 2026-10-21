# -*- coding: utf-8 -*-
"""Aplicación TUI de AniLinux Terminal.

Navegación estilo asistente: barra lateral (rail) con las secciones,
navegable con ↑/↓ y Enter o →. Dentro de cada sección se navega igual
y con Esc/← se vuelve atrás.
"""
import os
import re
import shutil
import subprocess
import sys
import time

from . import config
from . import extractors
from . import tui
from .api import ApiError, NetworkError
from .api import parse_auth, parse_lists, parse_user
from .tui import C, draw_box, fit, pad, visible_len, wrap

HINT_BACK = "↑↓ mover   ·   Enter/→ seleccionar   ·   Esc/← volver   ·   q salir"
HINT_LIST = ("↑↓/PgUp/PgDn navegar · Enter detalles · f ★fav · t ⌛luego · "
             "g ▶viendo · v ✓visto · Esc volver")


def _visible_window(text, cursor, width):
    """Ventana visible de un campo de texto que puede desbordar el ancho."""
    text = text or ""
    if visible_len(text) <= width:
        return text, 0
    start = max(0, min(cursor - width, len(text) - width))
    return text[start:start + width], start



class App:
    SECTIONS = (
        "inicio", "explorar", "generos", "buscar", "calendario", "perfil", "ajustes",
    )
    SECTION_LABELS = (
        "Inicio", "Explorar", "Géneros", "Buscar", "Calendario", "Perfil", "Ajustes",
    )

    def __init__(self, api_ref, db, session, settings):
        self.api = api_ref
        self.db = db
        self.session = session
        self.settings = settings
        self.scr = tui.Screen()
        # mensaje transitorio mostrado en la parte inferior (tono, texto)
        self.flash_msg = None
        self.flash_ts = 0.0
        self._restore = None

    # ================================================================= #
    # ciclo principal
    # ================================================================= #
    def run(self):
        if not tui.can_raw():
            print("Necesitas una terminal interactiva (TTY) para ejecutar la TUI.")
            sys.exit(1)
        self._restore = tui.make_raw()
        self.scr.enter()
        try:
            self.rail_loop()
        finally:
            self.scr.leave()
            self._restore()

    def flash(self, msg, tone="info"):
        self.flash_msg = (tone, msg)
        self.flash_ts = time.time()

    def rail_loop(self):
        sel = 0
        while True:
            self.draw_rail_frame(sel)
            try:
                key = tui.read_key()
            except (KeyboardInterrupt, EOFError):
                return
            if key in ("up", "k"):
                sel = (sel - 1) % len(self.SECTIONS)
            elif key in ("down", "j"):
                sel = (sel + 1) % len(self.SECTIONS)
            elif key in ("enter", "right"):
                self.flash_msg = None
                self.run_section(self.SECTIONS[sel])
            elif key == "q":
                if self.confirm("¿Salir de AniLinux Terminal?", default=True):
                    return
            elif key == "ctrl+c":
                return

    # ================================================================= #
    # encuadre base
    # ================================================================= #
    def _terminal(self):
        return self.scr.size

    def draw_base(self, active, title, hint, rail_sel=-1):
        """Dibuja marco completo. Devuelve rect de contenido (y, x, h, w)."""
        W, H = self._terminal()
        self.scr.clear()
        # cabecera
        self.scr.move(0, 0)
        self.scr.write(C.TITLE_BG + C.BOLD + pad(" " + "ANILINUX TERMINAL", W) + C.RESET)
        # línea de estado bajo la cabecera
        user_txt = self.session.label + (f" · {self.session.user.get('points', 0)} pts"
                                         if self.session.is_logged and self.session.user else "")
        stats = (f"{self.db.count_animes()} animes · {self.db.count_episodes()} episodios · "
                 f"{self.db.count_servers()} servidores")
        left = f" Cuenta: {user_txt}"[: max(0, W - visible_len(C.strip(stats)) - 2)]
        line = pad(left, max(0, W - visible_len(stats))) + stats + " "
        self.scr.move(1, 0)
        self.scr.write(C.FG_DIM + pad(line, W) + C.RESET)

        rail_w = 18
        body_top = 2
        body_h = H - 3 - body_top - 0
        # rail
        for yy in range(body_top, body_top + body_h + 0):
            if yy > H - 2:
                break
            self.scr.move(yy, 0)
            self.scr.write(C.RAIL_BG + " " * rail_w + C.RESET)
        self.scr.move(body_top, 0)
        self.scr.write(C.RAIL_BG + C.BOLD + pad(fit(" SECCIONES", rail_w - 1), rail_w - 1) + C.RESET)
        for i, name in enumerate(self.SECTIONS):
            row_label = self.SECTION_LABELS[i]
            yy = body_top + 2 + i
            if yy >= body_top + body_h:
                break
            self.scr.move(yy, 0)
            if name == active:
                # estás dentro de esta sección
                item = C.BOLD + C.OK + "▌ " + row_label
            elif i == rail_sel:
                # es la sección que abrirás con Enter/→
                item = C.BOLD + C.ACCENT + "▌ " + row_label
            else:
                item = C.FG + "  " + row_label
            self.scr.write(C.RAIL_BG + pad(item, rail_w - 1) + C.RESET)
        # pie
        hint_line = hint if hint else ""
        if self.flash_msg and time.time() - self.flash_ts < 10:
            tone, text = self.flash_msg
            col = {"info": C.OK, "warn": C.WARN, "err": C.ERR}.get(tone, C.FG)
            hint_line = hint_line + "   " if hint_line else ""
        self.scr.move(H - 1, 0)
        display = fit(hint_line, max(0, W - 2))
        self.scr.write(C.STATUS_BG + pad(" " + display, W) + C.RESET)
        if self.flash_msg and time.time() - self.flash_ts < 10:
            tone, text = self.flash_msg
            col = {"info": C.OK, "warn": C.WARN, "err": C.ERR}.get(tone, C.FG)
            self.scr.move(H - 2, rail_w + 1)
            self.scr.write(col + fit(" " + text, max(0, W - rail_w - 3)) + C.RESET)
        # rect de contenido
        y = body_top + 1
        x = rail_w + 1
        h = body_h - 2
        w = W - rail_w - 2
        draw_box(self.scr, y - 1, x - 1, h + 2, w + 2,
                 title if title else "", C.BORDER)
        return y, x, h, w

    def draw_rail_frame(self, sel):
        hint = (f"Enter/→ abre {self.SECTION_LABELS[sel]}   ·   "
                "↑↓ mover   ·   q salir   ·   e ajustes")
        y, x, h, w = self.draw_base(None, "Bienvenido 👋", hint, rail_sel=sel)
        lines = [
            (f"Cliente nativo de Kawaii Animes para Linux", C.ACCENT + C.BOLD),
            ("", 0),
            (f"Catálogo local · {self.db.count_animes()} animes · "
             f"{self.db.count_episodes()} episodios · {self.db.count_servers()} servidores", C.FG),
            (f"Última sincronización: {self.db.get_meta('last_sync', '—')}", C.FG_DIM),
            (f"Cuenta: {self.session.label}", C.FG),
            ("", 0),
            ("Elige la sección con ↑↓ y ábrela con Enter o →", C.ACCENT2),
            ("Todo funciona con el teclado. Pulsa q para salir.", C.FG_DIM),
        ]
        yy = y
        for text, col in lines:
            if yy >= y + h - 1:
                break
            self.scr.move(yy, x + 2)
            if col:
                self.scr.write(col + fit(str(text), w - 4) + "")
            else:
                self.scr.write(fit(str(text), w - 4))
            self.scr.write(C.RESET)
            yy += 1

    @staticmethod
    def _fix_offset(sel, offset, h, n):
        if sel < offset:
            offset = sel
        elif sel >= offset + h:
            offset = sel - h + 1
        return max(0, min(offset, max(0, n - h)))

    def menu(self, y, x, h, w, items, sel, offset):
        """Dibuja un menú vertical desplazable.  items: [(texto, color|None)]"""
        n = len(items)
        offset = self._fix_offset(sel, offset, h, n)
        show = min(h, n)
        for row_i in range(show):
            idx = offset + row_i
            if idx >= n:
                break
            text, col = items[idx]
            self.scr.move(y + row_i, x)
            prefix = "› " if idx == sel else "  "
            if idx == sel:
                self.scr.write(C.SEL_BG + C.BOLD + pad(fit(prefix + text, w), w) + C.RESET)
            else:
                body = (col if col is not None else C.FG) + prefix + text
                self.scr.write(pad(fit(body, w), w) + C.RESET)
        if n > h:
            self.scr.move(y, x + w - 2)
            if offset > 0:
                self.scr.write(C.FG_DIM + "▲" + C.RESET)
            else:
                self.scr.write(" " + C.RESET)
            self.scr.move(y + show - 1, x + w - 2)
            if offset + show < n:
                self.scr.write(C.FG_DIM + "▼" + C.RESET)
            else:
                self.scr.write(" " + C.RESET)

    def centered(self, W, H, height, width):
        y0 = max(3, min(H - height - 2, (H - height) // 2 + 1))
        x0 = 19 + max(1, ((W - 20) - width) // 2)
        return y0, x0, height, width

    # ================================================================= #
    # diálogos
    # ================================================================= #
    def alert(self, text, title="Aviso"):
        W, H = self._terminal()
        tw = max(40, min(W - 24, 68))
        lines = wrap(str(text), tw - 4) or [""]
        h = min(len(lines) + 3, H - 5)
        w = tw
        y0, x0, _, _ = self.centered(W, H, height=h, width=w)
        while True:
            self.draw_base(None, title, HINT_BACK)
            draw_box(self.scr, y0 - 1, x0 - 1, h + 1, w + 2, "", C.WARN)
            for i in range(min(len(lines), h - 1)):
                self.scr.move(y0 + i, x0)
                self.scr.write(C.WARN + fit(lines[i], w) + C.RESET)
            self.scr.move(y0 + min(len(lines), h - 1), x0)
            self.scr.write(C.FG_DIM + fit("Enter / Esc para continuar", w) + C.RESET)
            key = tui.read_key()
            if key in ("enter", "esc", "q", "left"):
                return

    def confirm(self, text, title="Confirmar", default=True):
        W, H = self._terminal()
        h, w = 7, max(50, min(W - 24, 66))
        y0, x0, _, _ = self.centered(W, H, height=h, width=w)
        lines = wrap(str(text), w - 4)
        sel = 0 if default else 1
        items = ["Sí, continuar", "No, cancelar"]
        while True:
            self.draw_base(None, title, "↑↓ mover · Enter confirmar · Esc cancela")
            draw_box(self.scr, y0 - 1, x0 - 1, h + 2, w + 2, "", C.WARN)
            for i, ln in enumerate(lines[:3]):
                self.scr.move(y0 + i, x0 + 1)
                self.scr.write(C.FG + fit(ln, w - 2) + C.RESET)
            for i, it in enumerate(items):
                self.scr.move(y0 + 4 + i, x0 + 2)
                prefix = "› " if i == sel else "  "
                row = prefix + it
                self.scr.write((C.SEL_BG + C.BOLD if i == sel else C.FG) +
                               pad(fit(row, w - 4), w - 4) + C.RESET)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key == "enter":
                return sel == 0
            elif key in ("esc", "left", "q"):
                return False

    def input_box(self, title, label, initial="", password=False):
        """Entrada de texto en caja; devuelve str o None si cancela."""
        W, H = self._terminal()
        w = max(40, min(W - 28, 62))
        y0, x0, _, _ = self.centered(W, H, height=6, width=w)
        value = initial
        cursor = len(value)
        field_w = w
        while True:
            self.draw_base(None, title, "Escribe · Enter aceptar · Esc cancela")
            draw_box(self.scr, y0 - 1, x0 - 1, 8, w + 2, title, C.ACCENT2)
            self.scr.move(y0, x0 + 1)
            self.scr.write(C.FG_DIM + fit(label, w - 1) + C.RESET)
            shown = "•" * len(value) if password else value
            line, start = _visible_window(shown, cursor, field_w - 1)
            line = fit(line, field_w - 1)
            self.scr.move(y0 + 2, x0)
            self.scr.write(C.SEL_BG + pad(" " + line, field_w) + C.RESET)
            self.scr.move(y0 + 2, x0 + 1 + visible_len(line[:cursor - start]))
            self.scr.write("\x1b[?25h")
            key = tui.read_key()
            self.scr.write("\x1b[?25l")
            if key == "enter":
                return value if password else value.strip()
            elif key in ("esc", "q"):
                return None
            elif key == "backspace":
                if cursor > 0:
                    value = value[:cursor - 1] + value[cursor:]
                    cursor -= 1
            elif key == "delete":
                value = value[:cursor] + value[cursor + 1:]
            elif key == "ctrl+u":
                value, cursor = "", 0
            elif key == "home":
                cursor = 0
            elif key == "end":
                cursor = len(value)
            elif key == "left":
                cursor = max(0, cursor - 1)
            elif key == "right":
                cursor = min(len(value), cursor + 1)
            elif isinstance(key, str) and len(key) == 1 and key >= " ":
                value = value[:cursor] + key + value[cursor:]
                cursor += 1

    @staticmethod
    def _start(shown, cursor, width):
        _, start = _visible_window(shown, cursor, width)
        return start


    # ================================================================= #
    # enrutamiento de secciones
    # ================================================================= #
    def run_section(self, name):
        handlers = {
            "inicio": self.scr_inicio,
            "explorar": self.scr_explorar,
            "generos": self.scr_generos,
            "buscar": self.scr_buscar,
            "calendario": self.scr_calendario,
            "perfil": self.scr_perfil,
            "ajustes": self.scr_ajustes,
        }
        handlers[name]()

    # ------------------------------------------------------------------ #
    # INICIO
    # ------------------------------------------------------------------ #
    def scr_inicio(self):
        items = [
            ("Continuar viendo", None),
            ("Novedades (más recientes)", None),
            ("Últimos episodios añadidos", None),
            ("Populares (más vistos)", None),
            ("Mejor valorados", None),
            ("En emisión", None),
            ("Películas", None),
            ("Con audio latino", None),
            ("Con audio castellano", None),
            ("Volver", C.FG_DIM),
        ]
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base("inicio", "Inicio", HINT_BACK)
            self.menu(y + 1, x + 2, min(h - 2, len(items)), w - 4, items, sel, offset)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key in ("enter", "right"):
                if sel == 0:
                    self.list_continue()
                elif sel == 1:
                    self.list_screen("Novedades", self.db.new_animes())
                elif sel == 2:
                    self.scr_recent_episodes()
                elif sel == 3:
                    self.list_screen("Populares", self.db.popular_animes())
                elif sel == 4:
                    self.list_screen("Mejor valorados", self.db.top_rated())
                elif sel == 5:
                    self.list_screen("En emisión", self.db.on_air())
                elif sel == 6:
                    self.list_screen("Películas", self.db.by_type("Movie"))
                elif sel == 7:
                    self.list_screen("Audio latino", self.db.with_language("1"))
                elif sel == 8:
                    self.list_screen("Audio castellano", self.db.with_language("2"))
                else:
                    return
            elif key in ("esc", "left"):
                return

    # ------------------------------------------------------------------ #
    # EXPLORAR
    # ------------------------------------------------------------------ #
    def scr_explorar(self):
        items = [
            ("Todos los animes (A-Z)", None),
            ("En emisión", None),
            ("Finalizados", None),
            ("Películas", None),
            ("OVA", None),
            ("ONA", None),
            ("Especiales", None),
            ("Populares", None),
            ("Mejor valorados", None),
            ("Novedades", None),
            ("Audio latino", None),
            ("Audio castellano", None),
            ("Volver", C.FG_DIM),
        ]
        sel, offset = 0, 0
        actions = [
            self._explore_all, self._explore_air, self._explore_done,
            self._explore_movie, self._explore_ova, self._explore_ona,
            self._explore_special, self._explore_popular, self._explore_top,
            self._explore_new, self._explore_lat, self._explore_cas,
        ]
        while True:
            y, x, h, w = self.draw_base("explorar", "Explorar", HINT_BACK)
            self.menu(y + 1, x + 2, min(h - 2, len(items)), w - 4, items, sel, offset)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key in ("enter", "right"):
                if sel == len(items) - 1:
                    return
                actions[sel]()
            elif key in ("esc", "left"):
                return

    def _explore_all(self):
        self.list_screen("Todos los animes", self.db.all_with_episodes())

    def _explore_air(self):
        self.list_screen("En emisión", self.db.on_air())

    def _explore_done(self):
        self.list_screen("Finalizados", self.db.finished())

    def _explore_movie(self):
        self.list_screen("Películas", self.db.by_type("Movie"))

    def _explore_ova(self):
        self.list_screen("OVA", self.db.by_type("OVA"))

    def _explore_ona(self):
        self.list_screen("ONA", self.db.by_type("ONA"))

    def _explore_special(self):
        self.list_screen("Especiales", self.db.by_type("Special"))

    def _explore_popular(self):
        self.list_screen("Populares", self.db.popular_animes())

    def _explore_top(self):
        self.list_screen("Mejor valorados", self.db.top_rated())

    def _explore_new(self):
        self.list_screen("Novedades", self.db.new_animes())

    def _explore_lat(self):
        self.list_screen("Audio latino", self.db.with_language("1"))

    def _explore_cas(self):
        self.list_screen("Audio castellano", self.db.with_language("2"))

    # ------------------------------------------------------------------ #
    # GÉNEROS
    # ------------------------------------------------------------------ #
    def scr_generos(self):
        genres = self.db.genres()
        if not genres:
            self.flash("Sin datos de géneros. Sincroniza el catálogo.", "warn")
            return
        items = [(f"{name} ({cnt})", None) for name, cnt in genres] + \
                [("Volver", C.FG_DIM)]
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base("generos", "Géneros", HINT_BACK)
            self.menu(y + 1, x + 2, min(h - 2, len(items)), w - 4, items, sel, offset)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
                offset = self._fix_offset(sel, offset, min(h - 2, len(items)), len(items))
            elif key == "down":
                sel = (sel + 1) % len(items)
                offset = self._fix_offset(sel, offset, min(h - 2, len(items)), len(items))
            elif key in ("enter", "right"):
                if sel == len(items) - 1:
                    return
                name = genres[sel][0]
                self.list_screen(f"Género: {name}", self.db.by_genre(name))
            elif key in ("esc", "left"):
                return

    # ------------------------------------------------------------------ #
    # BUSCAR
    # ------------------------------------------------------------------ #
    def scr_buscar(self):
        while True:
            q = self.input_small("Buscar anime…")
            if q is None:
                return
            if not q:
                continue
            self.flash(f"Buscando «{q}»…")
            rows = self.db.search(q)
            self.flash_msg = None
            if not rows:
                self.alert(f'Sin resultados para «{q}».', title="Búsqueda")
                continue
            self.list_screen(f'Resultados para «{q}» ({len(rows)})', rows,
                             empty_text="Sin resultados.")

    def input_small(self, placeholder):
        W, H = self._terminal()
        w = max(40, min(W - 28, 62))
        y0, x0, _, _ = self.centered(W, H, height=5, width=w)
        value = ""
        cursor = 0
        field_w = w
        self.draw_base("buscar", "Buscar", "Escribe · Enter buscar · Esc cancelar")
        draw_box(self.scr, y0 - 1, x0 - 1, 7, w + 2, "Buscar", C.ACCENT2)
        self.scr.move(y0, x0 + 1)
        self.scr.write(C.FG_DIM + fit(placeholder, field_w) + C.RESET)
        while True:
            line, start = _visible_window(value, cursor, field_w - 1)
            line = fit(line, field_w - 1)
            self.scr.move(y0 + 2, x0)
            self.scr.write(C.SEL_BG + pad(" " + line, field_w) + C.RESET)
            self.scr.move(y0 + 2, x0 + 1 + visible_len(line[:cursor - start]))
            self.scr.write("\x1b[?25h")
            key = tui.read_key()
            self.scr.write("\x1b[?25l")
            if key == "enter":
                return value.strip()
            elif key in ("esc", "q"):
                return None
            elif key == "backspace":
                if cursor > 0:
                    value = value[:cursor - 1] + value[cursor:]
                    cursor -= 1
            elif key == "ctrl+u":
                value, cursor = "", 0
            elif isinstance(key, str) and len(key) == 1 and key >= " ":
                value = value[:cursor] + key + value[cursor:]
                cursor += 1

    # ------------------------------------------------------------------ #
    # CALENDARIO
    # ------------------------------------------------------------------ #
    def scr_calendario(self):
        today = (time.localtime().tm_wday + 1)   # lunes = 1 … domingo = 7
        days = list(config.BROADCAST_DAYS)       # [1..7]
        items = [(f"{config.BROADCAST_DAYS[today]} (hoy)", C.ACCENT)] + \
                [(config.BROADCAST_DAYS[d], None) for d in days] + \
                [("Volver", C.FG_DIM)]
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base("calendario", "Calendario de emisión", HINT_BACK)
            self.menu(y + 1, x + 2, min(h - 2, len(items)), w - 4, items, sel, offset)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key in ("enter", "right"):
                if sel == len(items) - 1:
                    return
                day = today if sel == 0 else days[sel - 1]
                rows = self.db.calendar(day)
                if not rows:
                    self.alert(f"Sin emisiones el {config.BROADCAST_DAYS[day]}.",
                               title="Calendario")
                    continue
                self.list_screen(f"Emisión: {config.BROADCAST_DAYS[day]} "
                                 f"({len(rows)} animes)", rows)
            elif key in ("esc", "left"):
                return

    # ================================================================= #
    # listados de animes
    # ================================================================= #
    def anime_flags(self, anime_id):
        uid = self.session.user_id
        return {
            "fav": self.db.has_list("UserFavorite", uid, anime_id),
            "seeing": self.db.has_list("UserWatching", uid, anime_id),
            "later": self.db.has_list("UserWatchLater", uid, anime_id),
            "viewed": self.db.has_list("UserView", uid, anime_id),
        }

    def list_row(self, a):
        st = self.anime_flags(a["id"])
        parts = []
        if st["fav"]:
            parts.append(C.ACCENT + "★" + C.RESET)
        else:
            parts.append(" ")
        if st["viewed"]:
            parts.append(C.OK + "✓" + C.RESET)
        else:
            parts.append(" ")
        if st["seeing"]:
            parts.append(C.ACCENT2 + "▶" + C.RESET)
        else:
            parts.append(" ")
        if st["later"]:
            parts.append(C.WARN + "⌛" + C.RESET)
        else:
            parts.append(" ")
        marks = "".join(parts)
        year = (a["aired"] or "")[:4] or "    "
        tipo = config.TYPE_LABELS.get(a["type"], a["type"])[:5]
        vote = f"{a['vote_average']:.2f}" if a["vote_average"] else "  —"
        line = (f"{marks} {a['name']} ({year} · {tipo}) · ★{vote} · "
                f"{config.fmt_views(a['views'])} vistas")
        return (line, None)

    def list_screen(self, title, rows, empty_text="No hay animes."):
        """Lista navegable de animes. Devuelve None al salir."""
        if not rows:
            self.flash(empty_text, "warn")
            return None
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base(None, title, HINT_LIST)
            items = [self.list_row(a) for a in rows]
            count = min(h - 1, len(rows))
            self.menu(y, x + 1, count, w - 3, items, sel, offset)
            offset = self._fix_offset(sel, offset, count, len(rows))
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(rows)
            elif key == "down":
                sel = (sel + 1) % len(rows)
            elif key == "pgup":
                sel = max(0, sel - count)
            elif key == "pgdn":
                sel = min(len(rows) - 1, sel + count)
            elif key in ("enter", "right"):
                anime = rows[sel]
                self.db.push_search(anime["id"])
                self.anime_detail(anime)
            elif key in ("esc", "left", "q"):
                return None
            elif key == "f":
                self.toggle_user_list("UserFavorite", rows[sel]["id"], "favorito")
            elif key == "t":
                self.toggle_user_list("UserWatchLater", rows[sel]["id"], "ver más tarde")
            elif key == "g":
                self.toggle_user_list("UserWatching", rows[sel]["id"], "siguiendo")
            elif key == "v":
                self.mark_anime_viewed(rows[sel])

    # ------------------------------------------------------------------ #
    # acciones de listas (locales + servidor si hay sesión)
    # ------------------------------------------------------------------ #
    def toggle_user_list(self, table, anime_id, label):
        ops = {
            "UserFavorite": ("add_favorite", "remove_favorite"),
            "UserWatchLater": ("add_watchlater", "remove_watchlater"),
            "UserWatching": ("add_watching", "remove_watching"),
            "UserView": ("add_view", "remove_view"),
        }
        add_op, rm_op = ops[table]
        uid = self.session.user_id
        has = self.db.has_list(table, uid, anime_id)
        if not has:
            self.db.add_list(table, uid, anime_id)
            if self.session.token:
                try:
                    getattr(self.api, add_op)(self.session.token, anime_id)
                except (ApiError, NetworkError) as exc:
                    self.flash(f"Cambio local ok; servidor no confirmó ({exc})", "warn")
                    return
            self.flash(f"Añadido a {label}.", "ok")
        else:
            self.db.remove_list(table, uid, anime_id)
            if self.session.token:
                try:
                    getattr(self.api, rm_op)(self.session.token, anime_id)
                except (ApiError, NetworkError) as exc:
                    self.flash(f"Cambio local ok; servidor no confirmó ({exc})", "warn")
                    return
            self.flash(f"Quitado de {label}.", "info")

    def mark_anime_viewed(self, anime):
        self.toggle_user_list("UserView", anime["id"], "vistos")

    # ================================================================= #
    # detalle de anime
    # ================================================================= #
    def anime_detail(self, anime):
        while True:
            y, x, h, w = self.draw_base(None, anime["name"], HINT_BACK)
            st = self.anime_flags(anime["id"])
            flags = ""
            if st["fav"]:
                flags += C.ACCENT + "★ favorito   " + C.RESET
            if st["later"]:
                flags += C.WARN + "⌛ luego   " + C.RESET
            if st["seeing"]:
                flags += C.ACCENT2 + "▶ viendo   " + C.RESET
            if st["viewed"]:
                flags += C.OK + "✓ visto" + C.RESET
            if not flags:
                flags = C.FG_DIM + "sin listas"
            self.scr.move(y, x + 1)
            self.scr.write(fit(flags, w - 2))
            self.scr.move(y + 1, x + 1)
            tipo = config.TYPE_LABELS.get(anime["type"], anime["type"])
            estado = config.STATUS_LABELS.get(anime["status"], "?")
            aired = anime["aired"] or "—"
            vote = f"{anime['vote_average']:.2f}" if anime["vote_average"] else "—"
            self.scr.write(C.FG_DIM +
                           fit(f"{tipo} · {estado} · {aired} · temporada "
                               f"{anime['season'] or '—'} · ★{vote} · "
                               f"{config.fmt_views(anime['views'])} vistas", w - 2))
            self.scr.move(y + 2, x + 1)
            gens = (anime["genres"] or "").replace(",", " · ")
            self.scr.write(C.ACCENT2 + fit("Géneros: " + gens, w - 2))
            ov = wrap(anime["overview"] or "", w - 4)
            head_n = len(ov[: max(0, h - 9)])
            for i, ln in enumerate(ov[: max(0, h - 9)]):
                self.scr.move(y + 3 + i, x + 2)
                self.scr.write(C.FG + fit(ln, w - 4) + C.RESET)
            ymenu = y + 4 + head_n if head_n else y + 4
            st = self.anime_flags(anime["id"])
            items = [
                ("Ver episodios", None),
                (("Quitar de favoritos") if st["fav"] else ("Marcar como favorito"), None),
                (("Quitar de ver más tarde") if st["later"]
                 else ("Añadir a ver más tarde"), None),
                (("Dejar de seguir") if st["seeing"] else ("Seguir (viendo)"), None),
                (("Desmarcar anime visto") if st["viewed"]
                 else ("Marcar anime como visto"), None),
                ("Volver", C.FG_DIM),
            ]
            sel, offset = 0, 0
            mrows = len(items)
            while True:
                self.menu(ymenu + 1, x + 2, min(mrows, max(1, h - (ymenu - y) - 1)),
                          w - 4, items, sel, offset)
                key = tui.read_key()
                if key == "up":
                    sel = (sel - 1) % mrows
                elif key == "down":
                    sel = (sel + 1) % mrows
                elif key in ("enter", "right"):
                    if sel == 0:
                        self.scr_episodes(anime)
                    elif sel == 1:
                        self.toggle_user_list("UserFavorite", anime["id"], "favoritos")
                    elif sel == 2:
                        self.toggle_user_list("UserWatchLater", anime["id"], "ver más tarde")
                    elif sel == 3:
                        self.toggle_user_list("UserWatching", anime["id"], "siguiendo")
                    elif sel == 4:
                        self.mark_anime_viewed(anime)
                    else:
                        return
                    break
                elif key in ("esc", "left", "q"):
                    return

    # ------------------------------------------------------------------ #
    # episodios
    # ------------------------------------------------------------------ #
    def scr_episodes(self, anime):
        episodes = self.db.episodes_for_anime(anime["id"])
        if not episodes:
            self.alert("Este anime no tiene episodios todavía.", title="Episodios")
            return
        uid = self.session.user_id
        langs_cache = {e["id"]: self.db.languages_for_episode(e["id"]) for e in episodes}
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base(None, f"{anime['name']} · Episodios", HINT_BACK +
                                        " · v: marcar visto")
            viewed = self.db.viewed_episodes(uid, anime["id"])
            rows = []
            for e in episodes:
                seen = C.OK + "✓ " + C.RESET if e["id"] in viewed else "   "
                langs = " · ".join(config.LANGUAGE_SHORT.get(str(l), str(l))
                                   for l in langs_cache[e["id"]])
                rows.append((f"{seen}Ep {e['number']:<4} {langs}", None))
            count = min(h - 1, len(rows))
            self.menu(y, x + 1, count, w - 3, rows, sel, offset)
            self.scr.move(y + h if False else y + count, x + 1)
            self.scr.write(C.FG_DIM + fit(f"{len(episodes)} episodios  ·  Enter: "
                                          "reproducir (mpv)  ·  v: ✓ visto", w - 2))
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(rows)
            elif key == "down":
                sel = (sel + 1) % len(rows)
            elif key == "pgup":
                sel = max(0, sel - count)
            elif key == "pgdn":
                sel = min(len(rows) - 1, sel + count)
            elif key in ("esc", "left", "q"):
                return
            elif key == "v":
                ep = episodes[sel]
                if ep["id"] in viewed:
                    self.episode_unmark_viewed(anime, ep)
                else:
                    self.mark_episode_viewed(anime, ep)
            elif key in ("enter", "right"):
                ep = episodes[sel]
                self.episode_flow(anime, ep)

    def episode_flow(self, anime, ep):
        """Idioma → reproducción automática (sin más menús)."""
        langs = self.db.languages_for_episode(ep["id"])
        if not langs:
            self.alert("No hay servidores disponibles para este episodio.",
                       title="Reproducir")
            return
        if len(langs) == 1:
            self.play_episode(anime, ep, langs[0])
            return
        items = [(config.LANGUAGE_LABELS.get(str(l), f"Idioma {l}"), None)
                 for l in langs] + [("Volver", C.FG_DIM)]
        sel = 0
        while True:
            y, x, h, w = self.draw_base(
                None, f"Ep {ep['number']} · Elegir idioma", HINT_BACK)
            self.menu(y + 1, x + 2, len(items), w - 4, items, sel, 0)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key in ("enter", "right"):
                if sel == len(items) - 1:
                    return
                self.play_episode(anime, ep, langs[sel])
                return
            elif key in ("esc", "left", "q"):
                return

    def play_episode(self, anime, ep, lang):
        """Reproducción única: prueba los servidores del idioma en orden y
        abre el primero que funcione, con mpv dentro de esta misma ventana."""
        players = [p for p in self.db.players_for_episode(ep["id"])
                   if str(p["language"]) == str(lang)]
        if not players:
            self.alert("No hay servidores para ese idioma.", title="Reproducir")
            return
        if not shutil.which("mpv"):
            self.alert("mpv no está instalado.\n\n"
                       "Instálalo con: sudo apt install mpv", title="mpv")
            return
        label = config.LANGUAGE_LABELS.get(str(lang), lang)
        errors = []
        for i, p in enumerate(players):
            srv = p.get("server_name") or f"Servidor {p['server_id']}"
            self.draw_probe(ep, label, srv, i, len(players))
            res, err = extractors.try_resolve(p, timeout=extractors.TIMEOUT)
            if res is None:
                errors.append(f"{srv}: {err}")
                continue
            url, referer = res
            self.play_in_window(url, referer, anime, ep, lang)
            return
        self.alert("No se pudo reproducir en ningún servidor:\n\n" +
                   "\n".join(errors[:5]) +
                   ("\n…" if len(errors) > 5 else ""), title="Reproducir")

    def draw_probe(self, ep, lang_label, srv, i, n):
        y, x, h, w = self.draw_base(None,
            f"Ep {ep['number']} · {lang_label} · reproducir", HINT_BACK)
        self.scr.move(y + 2, x + 2)
        self.scr.write(C.OK + f"▶ Preparando servidor {srv} ({i + 1}/{n})…" +
                       C.RESET)
        self.scr.move(y + 4, x + 2)
        self.scr.write(C.FG_DIM +
                       "el episodio se abrirá aquí, dentro de la ventana" +
                       C.RESET)
        self.scr.flush()

    def play_in_window(self, url, referer, anime, ep, lang):
        """Reproduce el episodio con mpv buscando la mejor calidad.

        1. En kitty: vídeo dibujado en esta terminal (--vo=kitty, nítido),
           a máxima resolución, con la ventana a pantalla completa (Hyprland,
           best-effort), fondo negro opaco y vídeo centrado entre dos barras
           horizontales con mini-UI (progreso + anterior/lista/siguiente).
        2. Con Wayland/X11: ventana propia de mpv a pantalla completa.
        3. Sin gráficos: modo texto (--vo=tct) llenando toda la terminal.
        Siempre la máxima calidad de HLS disponible (mejor resolución).

        En kitty y tct mpv dibuja SOBRE la pantalla alterna de la TUI (no
        salta a la principal), para no perder la pantalla del usuario."""
        self.flash_msg = None
        # vecinos de la lista de capítulos (mismo orden que la pantalla)
        prev_ep = next_ep = None
        try:
            eps = self.db.episodes_for_anime(anime["id"])
            for i, e in enumerate(eps):
                if e["id"] == ep["id"]:
                    prev_ep = eps[i - 1] if i > 0 else None
                    next_ep = eps[i + 1] if i + 1 < len(eps) else None
                    break
        except Exception:
            pass
        title = "%s · Ep %s" % (anime.get("name", "?"), ep.get("number", "?"))

        via = self._play_via()
        in_alt = via in ("kitty", "tct")
        if in_alt:
            try:
                rc, act = self._run_mpv(url, referer, prev_ep, next_ep,
                                        title)
            finally:
                # limpia restos de vídeo en la pantalla de la TUI
                self.scr.write("\x1b[?25l\x1b[2J\x1b[H")
                self.scr.flush()
        else:
            self.scr.leave()      # ventana propia de mpv: terminal libre
            try:
                rc, act = self._run_mpv(url, referer, prev_ep, next_ep,
                                        title)
            finally:
                self.scr.enter()
        if act == "next" and next_ep:
            self.play_episode(anime, next_ep, lang)
            return
        if act == "prev" and prev_ep:
            self.play_episode(anime, prev_ep, lang)
            return
        if act == "back":
            self.flash("Lista de capítulos.", "info")
            return
        if rc == 0:
            self.mark_episode_viewed(anime, ep)
            self.flash("Reproducción finalizada.", "info")
        else:
            self.alert(f"mpv terminó con error (código {rc}).\n\n"
                       "El enlace puede haber caducado; vuelve a intentarlo.",
                       title="Reproducir")

    @staticmethod
    def _play_via():
        """Elige la vía de vídeo según el entorno (sin tocar mpv)."""
        env = os.environ
        is_kitty = bool(env.get("KITTY_WINDOW_ID")) \
            or bool(env.get("KITTY_PID")) \
            or "kitty" in (env.get("TERM") or "")
        has_gui = bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))
        if is_kitty:
            return "kitty"
        if has_gui:
            return "ventana"
        return "tct"

    def _mpv_video_path(self, via, url="", referer=""):
        """Args de mpv según la vía de vídeo. Devuelve (args, geom).

        En kitty fija la geometría real de la terminal (filas/cols y tamaño
        del lienzo en px): mpv 0.41 la lee de TIOCGWINSZ y, si el pty no
        informa ws_xpixel/ws_ypixel, asume 80x25 celdas y 320x240 px, por lo
        que el vídeo saldría pequeño, anclado arriba y con fondo translúcido.
        Además calculamos nosotros la celda de anclaje (CUP): mpv emite la
        fracción 0-based que él computa por un CUP 1-based y el vídeo queda
        ~1 celda más arriba de lo correcto. La última fila queda reservada
        para la mini-UI; geom describe la geometría empleada (None en
        ventana/tct). Solo usa la biblioteca estándar."""
        if via == "kitty":
            args = ["--vo=kitty",
                    "--vo-kitty-alt-screen=no",     # dibuja en la pantalla de
                                                    # la TUI (fondo pintado)
                    "--vo-kitty-config-clear=no"]   # no borra ese fondo
            geom = None
            try:
                rows, cols, cw, ch = self._terminal_metrics()
            except Exception:
                rows = cols = cw = ch = 0
            if rows and cols:
                # el lienzo del vídeo deja SIEMPRE fuera dos filas: la de la
                # barra de progreso y la de los botones, para que el vídeo
                # (de cualquier aspecto, incluso 4:3) nunca pueda taparlas
                rows_ui = rows - 2 if rows >= 4 else \
                    (rows - 1 if rows >= 3 else rows)
                geom = {"rows": rows_ui, "cols": cols, "cw": cw, "ch": ch,
                        "reserved": rows_ui < rows, "top": None, "left": None,
                        "bar_y": None, "btn_y": rows}
                args += ["--vo-kitty-rows=%d" % rows_ui,
                         "--vo-kitty-cols=%d" % cols]
                if cw > 0 and ch > 0:
                    args += ["--vo-kitty-width=%d" % (cw * cols),
                             "--vo-kitty-height=%d" % (ch * rows_ui)]
                    try:
                        place = self._kitty_placement(rows_ui, cols, cw, ch,
                                                      url, referer)
                    except Exception:
                        place = None
                    if place:
                        top, left, vh_fit = place
                        args += ["--vo-kitty-top=%d" % top,
                                 "--vo-kitty-left=%d" % left]
                        geom["top"], geom["left"] = top, left
                        if rows >= 4:
                            geom["bar_y"] = rows - 1   # barra siempre a la
                                                       # vista, bajo el vídeo
            return args, geom
        if via == "ventana":
            return ["--fullscreen"], None
        return ["--vo=tct", "--panscan=1.0"], None

    def _kitty_placement(self, rows, cols, cw, ch, url, referer=""):
        """Celdas de anclaje del vídeo kitty: (top, left, alto_px_vídeo).

        mpv 0.41 calcula el anclaje del vídeo como fracción de celdas
        (0-based) pero la emite por CUP, que es 1-based, así que el vídeo
        queda ~1 celda más alto de la cuenta. Lo calculamos aquí: ajuste a
        lo ancho del lienzo y centrado en vertical, usando la resolución
        del mejor variante HLS (la misma que elige mpv con
        --hls-bitrate=max). Si la medición no se pudo hacer (CDN bloqueado
        o silencioso) se asume 16:9, que era el caso real de los fallos
        anteriores; el vídeo sigue centrado en vez de pegarse arriba.
        El alto en px (vh_fit) permite saber dónde acaba el vídeo y poner
        la barra de progreso justo debajo (ver _mpv_video_path)."""
        res = self._hls_aspect(url, referer)
        if not res:
            res = (1280, 720)               # 16:9 por defecto: igual centrado
        vw, vh = res
        if vw <= 0 or vh <= 0:
            return None
        W, H = cw * cols, ch * rows
        vh_fit = min(H, W * vh // vw)
        vw_fit = min(W, H * vw // vh)
        if vh_fit <= 0 or vw_fit <= 0:
            return None
        y0 = (H - vh_fit) // 2
        x0 = (W - vw_fit) // 2
        # CUP(r) dibuja en la fila r (1-based): el vídeo empieza en el píxel
        # y0 → redondeo de y0/ch da la celda 0-based; +1 la pasa a 1-based.
        return ((y0 + ch // 2) // ch + 1, (x0 + cw // 2) // cw + 1, vh_fit)

    def _draw_player_ui(self, y_top, y_bar, y_time, y_btn, W, elapsed,
                        duration, paused, title, has_prev, has_next):
        """Mini-UI del reproductor sobre las bandas negras (solo kitty).

        Filas:
        - y_top: icono de estado + título (banda negra superior).
        - y_bar: barra de progreso fina y larga (casi todo el ancho), con
          un círculo ● en el segundo en reproducción; entre el borde
          inferior del vídeo y la fila de botones. Se puede agarrar y
          arrastrar para adelantar/atrasar.
        - y_time: tiempos mm:ss / mm:ss pegados al borde derecho, justo
          debajo de la barra (puede ser la misma fila de los botones; en
          ese caso los botones se centran sin invadir la caja de tiempos).
        - y_btn: botones de cambio de capítulo, centrados.
        Devuelve (zonas, barra): zonas = [(x0, x1, acción)...] para el clic
        del ratón; barra = (x0, ancho) para clic de búsqueda por posición."""
        UIB = "\x1b[0;48;2;0;0;0m"      # negro opaco (celdas ya pintadas)
        zones = []
        bar_span = (0, 0)
        W = max(30, int(W or 80))
        te = self._fmt_time(elapsed if elapsed >= 0 else 0)
        td = self._fmt_time(duration)
        # caja de tiempos de ancho fijo (los dígitos avanzan dentro de ella)
        Lb = 2 * len(td) + 3
        tbox = ("%s / %s" % (te, td)).rjust(Lb)
        icon = "||" if paused else "▶"
        icon_c = C.WARN if paused else C.OK
        merged = y_bar is None

        def seg(y, x, sgr, text):
            self.scr.write("\x1b[%d;%dH%s%s%s" % (y, x, UIB, sgr, text))

        frac = 0.0
        if duration > 0 and elapsed >= 0:
            frac = min(1.0, max(0.0, elapsed / duration))

        if merged:
            # una sola fila: icono + barra + tiempos; botones a la derecha
            seg(y_btn, 2, icon_c + C.BOLD, icon)
            half = W // 2
            bar_w = max(6, half - Lb - 2 - 5)
            nf = int(frac * bar_w)
            seg(y_btn, 5, C.OK, "█" * nf)
            seg(y_btn, 5 + nf, C.FG_DIM, "░" * (bar_w - nf))
            bar_span = (5, bar_w)
            seg(y_btn, half - Lb + 1, C.FG_DIM, tbox)
        else:
            # ---- banda superior: icono de estado + título ----
            if y_top:
                seg(y_top, 2, icon_c + C.BOLD, icon)
                seg(y_top, 5, C.BOLD + C.FG,
                    fit(title, max(10, W - 8)))
            # ---- barra fina y larga con círculo indicador ----
            x_bar, bar_w = 2, W - 3          # casi todo el ancho
            if y_bar and bar_w >= 12:
                pos = min(bar_w - 1, int(frac * bar_w))
                if pos:
                    seg(y_bar, x_bar, C.OK, "━" * pos)      # transcurrido
                seg(y_bar, x_bar + pos, C.BOLD + C.FG, "●")  # el segundo
                tail = bar_w - pos - 1
                if tail > 0:
                    seg(y_bar, x_bar + pos + 1, C.FG_DIM, "─" * tail)
                bar_span = (x_bar, bar_w)
            # ---- tiempos justo debajo, pegados al borde derecho ----
            if y_time:
                seg(y_time, W - Lb + 1, C.FG_DIM, tbox)

        # ---- botones de cambio de capítulo ----
        if y_btn is not None and W >= 46:
            if merged:
                parts = [("prev" if has_prev else None, "◀ anterior"),
                         (None, "|"),
                         ("next" if has_next else None, "siguiente ▶")]
            else:
                parts = [("prev" if has_prev else None, "◀ anterior [p]"),
                         ("back", "lista [b]"),
                         ("next" if has_next else None, "siguiente [n] ▶")]
            vs = [C.visible_len(t) for _, t in parts]
            sep = 4
            total = sum(vs) + sep * (len(parts) - 1)
            # si los tiempos comparten la fila de botones no invadir su caja
            room = W - Lb - 3 if (not merged and y_time == y_btn) else W
            x = max(1, (room - total) // 2 + 1)
            if merged:
                x = max(x, W // 2 + 4)
            for (act, text), iv in zip(parts, vs):
                col = C.FG if act else C.DIM
                seg(y_btn, x, col + (C.BOLD if act else ""), text)
                if act:
                    zones.append((x, x + iv - 1, act))
                x += iv + sep
        self.scr.flush()
        return zones, bar_span

    def _clear_player_ui(self, y_top, y_bar, y_time, y_btn, W):
        """Borra la mini-UI: repinta de negro sus filas (auto-ocultar)."""
        UIB = "\x1b[0;48;2;0;0;0m"
        try:
            W = max(1, int(W or 80))
        except (TypeError, ValueError):
            W = 80
        for y in (y_top, y_bar, y_time, y_btn):
            if y:
                self.scr.write("\x1b[%d;1H%s%s" % (y, UIB, " " * W))
        self.scr.flush()

    @staticmethod
    def _hls_aspect(url, referer=""):
        """Resolución (w, h) del mejor variante del playlist HLS, o None.

        Un GET del master m3u8 y se lee RESOLUTION=WxH de las líneas
        #EXT-X-STREAM-INF, con la de mayor área (la misma que elige mpv con
        --hls-bitrate=max). Todo fallo, en silencio."""
        import urllib.request as _ur
        try:
            headers = {"User-Agent": extractors.IOS_UA, "Accept": "*/*"}
            if referer:
                headers["Referer"] = referer
            req = _ur.Request(url, headers=headers)
            with _ur.urlopen(req, timeout=6) as r:
                txt = r.read(262144).decode("utf-8", "replace")
        except Exception:
            return None
        best = None
        for m in re.finditer(r"RESOLUTION=(\d+)x(\d+)", txt):
            try:
                w, h = int(m.group(1)), int(m.group(2))
            except ValueError:
                continue
            if w > 0 and h > 0 and (best is None or w * h > best[0] * best[1]):
                best = (w, h)
        return best

    def _terminal_metrics(self):
        """Tamaño de la terminal en celdas y de la celda en px.

        Devuelve (rows, cols, cell_w, cell_h). Las celdas salen de
        TIOCGWINSZ (o del tamaño que la TUI ya conoce); el tamaño de celda
        en px se deduce de ws_xpixel/ws_ypixel del pty y, si el pty no los
        trae, se consulta al terminal con CSI 16t (kitty/xterm lo responden)."""
        cols, rows = self._terminal()
        try:
            cols = int(cols)
            rows = int(rows)
        except (TypeError, ValueError):
            cols = rows = 0
        cw = ch = 0
        import fcntl as _fcntl
        import struct as _st
        try:
            ws = _st.unpack("HHHH", _fcntl.ioctl(
                0, 0x5413, _st.pack("HHHH", 0, 0, 0, 0)))
            if ws[0]:
                rows = int(ws[0])
            if ws[1]:
                cols = int(ws[1])
            if ws[2] and ws[3] and rows and cols:
                cw, ch = int(ws[2]) // cols, int(ws[3]) // rows
        except OSError:
            pass
        if (cw <= 0 or ch <= 0) and rows > 0 and cols > 0:
            qcw, qch = self._cell_query()
            if qcw > 0 and qch > 0:
                cw, ch = qcw, qch
        if rows <= 0 or cols <= 0:
            return 0, 0, 0, 0
        return rows, cols, max(0, cw), max(0, ch)

    def _cell_query(self):
        """Consulta CSI 16t al terminal y devuelve (ancho_celda, alto_celda).

        kitty (y xterm-compatibles) responden con ESC[6;<alto>;<ancho>t.
        Espera hasta 0.35 s leyendo la entrada cruda; los bytes que no sean
        la respuesta se descartan. Sin respuesta devuelve (0, 0)."""
        import select as _sel
        try:
            os.write(1, b"\x1b[16t")
        except OSError:
            return 0, 0
        deadline = time.time() + 0.35
        buf = b""
        while time.time() < deadline:
            try:
                r, _, _ = _sel.select([0], [], [], deadline - time.time())
            except (OSError, ValueError):
                break
            if not r:
                continue
            try:
                d = os.read(0, 64)
            except OSError:
                break
            if not d:
                break
            buf += d
            m = re.search(b"\x1b\\[6;(\\d+);(\\d+)t", buf)
            if m:
                ch = int(m.group(1))
                cw = int(m.group(2))
                if ch > 0 and cw > 0:
                    return cw, ch
                break
        return 0, 0

    def _paint_black_bg(self):
        """Fondo negro opaco en toda la terminal (antes de lanzar mpv).

        En kitty el fondo por defecto de la ventana es translúcido (se ve el
        escritorio), pero las celdas con color de fondo explícito se dibujan
        opacas: rellenar todas con negro deja el cuadro íntegro en negro y
        el vídeo mpv queda centrado entre dos barras horizontales.
        Se llama tras el cambio a pantalla completa (geometría ya estable)."""
        try:
            size = shutil.get_terminal_size(fallback=self.scr.size)
            W = max(1, int(size.columns))
            H = max(1, int(size.lines))
        except Exception:
            return
        row = " " * W
        out = ["\x1b[?25l"]
        for y in range(1, H + 1):
            out.append("\x1b[%d;1H\x1b[48;2;0;0;0m%s" % (y, row))
        out.append("\x1b[0m\x1b[H")
        self.scr.write("".join(out))
        self.scr.flush()

    @staticmethod
    def _hyprctl_path():
        import shutil as _sh
        tool = _sh.which("hyprctl")
        if tool:
            return tool
        for cand in ("/usr/sbin/hyprctl", "/usr/bin/hyprctl", "/bin/hyprctl"):
            if os.path.exists(cand):
                return cand
        return None

    def _hypr_fullscreen(self, on, entered=False):
        """Pantalla completa del compositor (Hyprland), best-effort.

        Compatible con la sintaxis nueva (Hyprland 0.55+, dispatch Lua:
        hl.dsp.window.fullscreen) y la antigua (hyprlang: fullscreen 0).
        on=True: si la ventana activa ya ocupa la pantalla no se toca; si no,
        se pone a pantalla completa y devuelve True (para restaurarla con
        on=False). Devuelve None si hyprctl no está o nada ha cambiado."""
        import json as _js
        tool = self._hyprctl_path()
        if tool is None:
            return None

        def dispatch(lua, legacy):
            for args in (lua, legacy):
                try:
                    r = subprocess.run([tool, "dispatch"] + args,
                                       capture_output=True, text=True,
                                       timeout=5)
                except (subprocess.SubprocessError, OSError):
                    continue
                if r.returncode == 0 and "error" not in \
                        (((r.stdout or "") + (r.stderr or "")).lower()):
                    return True
            return False

        set_lua = ('hl.dsp.window.fullscreen({ mode = "fullscreen", '
                   'action = "set" })')
        off_lua = ('hl.dsp.window.fullscreen({ mode = "fullscreen", '
                   'action = "unset" })')
        try:
            if on:
                try:
                    out = subprocess.run([tool, "activewindow", "-j"],
                                         capture_output=True, text=True,
                                         timeout=5).stdout or "{}"
                    by_name = _js.loads(out).get("fullscreen") or 0
                    if bool(by_name) is True or int(by_name or 0) != 0:
                        return None      # ya ocupa la pantalla: no se toca
                except (ValueError, TypeError):
                    return None          # sin datos fiables: no tocar
                ok = dispatch([set_lua], ["fullscreen", "0"])
                if ok:
                    time.sleep(0.25)     # deja que la terminal re-escale
                return True if ok else None
            if not entered:
                return None              # nosotros no pusimos fullscreen
            dispatch([off_lua], ["fullscreen", "0"])
        except (subprocess.SubprocessError, OSError):
            pass                 # best-effort: sin compositor no pasa nada
        return None

    def _run_mpv(self, url, referer, prev_ep=None, next_ep=None, title=""):
        """Lanza mpv, dibuja la mini-UI y despacha teclado/ratón por IPC.

        Devuelve (rc, acción): acción None (salida normal), "prev"/"next"
        (capítulo pedido con las teclas p/n) o "back" (volver a la lista).

        - mpv con --input-terminal=no: no consume el teclado (q/Esc sale;
          espacio pausa; ←/→ salto; ↑/↓ volumen; m silencio; n/p capítulo
          siguiente/anterior; b lista de capítulos).
        - En kitty: mini-UI sobre las bandas negras (barra de progreso con
          tiempos + botones), refrescada por IPC (time-pos/duration/pause)
          y con clics de ratón (SGR 1006: bandas para buscar, botones para
          navegar; rueda = salto ±5 s).
        Solo biblioteca estándar."""
        import json as _js
        import select as _sel
        import socket as _sk
        import tempfile as _tf

        via = self._play_via()
        go_full = via in ("kitty", "tct")
        entered_fs = self._hypr_fullscreen(True) if go_full else None
        if go_full:
            self._paint_black_bg()   # tras fullscreen y su re-escalado
        vo_args, geom = self._mpv_video_path(via, url, referer)
        ipc_path = _tf.gettempdir() + "/anilinux-mpv-%d" % os.getpid()
        argv = ["mpv"] + vo_args + [
                    "--hls-bitrate=max", "--msg-level=all=error",
                    "--input-terminal=no", "--input-ipc-server=" + ipc_path,
                    "--referrer=" + (referer or ""),
                    "--user-agent=" + extractors.IOS_UA, url]
        try:
            os.unlink(ipc_path)
        except OSError:
            pass

        # filas de la mini-UI (solo kitty, con última fila reservada)
        y_top = y_bar = y_time = y_btn = None
        W = 0
        if geom and geom.get("reserved"):
            W = int(geom["cols"])
            y_btn = int(geom.get("btn_y") or (int(geom["rows"]) + 1))
            top = geom.get("top")
            bar_y = geom.get("bar_y")
            if top and top >= 2:
                y_top = top - 1          # icono + título (banda superior)
            if bar_y and 1 < bar_y < y_btn:
                y_bar = bar_y            # barra bajo el borde del vídeo
                y_time = y_bar + 1       # tiempos justo debajo de la barra

        proc = None
        sock = None
        act = None
        rc = 0
        rid_map = {}
        rid = [0]
        sockbuf = b""
        elapsed, duration, paused = -1.0, 0.0, False
        drawn = None                     # (seg, pausado, con-tiempo) pintado
        zones, bar_span = [], (0, 0)
        mouse_set = [False, False]
        t_spawn = time.time()
        drag_on = False                  # agarrado a la barra de progreso
        last_motion = t_spawn            # el cursor se mueve: mostrar UI
        last_scrub = [0.0]               # espacia seeks durante el arrastre
        scrub = [-1.0]                   # posición visual del círculo (drag)
        ui_hidden = False                # >2 s sin mover el cursor
        ui_t0 = [None]                   # pág: la cuenta empieza con tiempos
        try:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
            except FileNotFoundError:
                self.alert("mpv no está instalado.", title="mpv")
                return 200, None
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    sock = _sk.socket(_sk.AF_UNIX, _sk.SOCK_STREAM)
                    sock.connect(ipc_path)
                    break
                except OSError:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.08)
            if sock is None:
                proc.terminate()
                return (proc.returncode or 255), None
            sock.setblocking(False)
            poll_t = 0.0
            while proc.poll() is None:
                try:
                    r, _, _ = _sel.select([0, sock], [], [], 0.25)
                except (OSError, ValueError):
                    break
                now = time.time()

                # ---------- respuestas y estado de mpv ----------
                if sock in r:
                    try:
                        while True:
                            d = sock.recv(4096)
                            if not d:
                                break
                            sockbuf += d
                    except (BlockingIOError, InterruptedError):
                        pass
                    except OSError:
                        break                     # IPC muerto: mpv cayó
                    nl = sockbuf.find(b"\n")
                    while nl >= 0:
                        line, sockbuf = sockbuf[:nl], sockbuf[nl + 1:]
                        nl = sockbuf.find(b"\n")
                        if not line:
                            continue
                        try:
                            msg = _js.loads(line.decode("utf-8", "replace"))
                        except ValueError:
                            continue
                        _id = msg.get("request_id")
                        if _id in rid_map and msg.get("error") == "success":
                            name = rid_map.pop(_id)
                            data = msg.get("data")
                            if name == "time-pos" and \
                                    isinstance(data, (int, float)):
                                elapsed = data
                            elif name == "duration" and \
                                    isinstance(data, (int, float)):
                                duration = max(0.0, data)
                            elif name == "pause" and \
                                    isinstance(data, bool):
                                paused = data

                # ---------- sondeo IPC + ratón ----------
                if now >= poll_t:
                    poll_t = now + 0.7
                    for prop in ("time-pos", "pause"):
                        rid[0] += 1
                        rid_map[rid[0]] = prop
                        self._ipc_send(sock, ["get_property", prop], rid[0])
                    if duration <= 0:
                        rid[0] += 1
                        rid_map[rid[0]] = "duration"
                        self._ipc_send(sock, ["get_property", "duration"],
                                       rid[0])
                    if not mouse_set[0] and now - t_spawn > 0.3:
                        mouse_set[0] = True       # quita el 1003 de mpv
                        self.scr.write("\x1b[?1003l\x1b[?1006h\x1b[?1003h")
                        self.scr.flush()
                    if duration > 0 and not mouse_set[1]:
                        mouse_set[1] = True       # por si mpv lo re-armó
                        self.scr.write("\x1b[?1003l\x1b[?1006h\x1b[?1003h")
                        self.scr.flush()

                # ---------- mini-UI: mostrar / ocultar / repintar ----------
                sec = int(elapsed) if elapsed >= 0 else -1
                if y_btn is not None:
                    if ui_t0[0] is None and duration > 0:
                        ui_t0[0] = now            # arranca la cuenta atrás
                    idle = (ui_t0[0] is not None
                            and now - max(last_motion, ui_t0[0] or 0) > 2.0
                            and not paused and not drag_on)
                    if idle and not ui_hidden:
                        ui_hidden = True          # ocultar tras 2 s quieto
                        zones, bar_span, drawn = [], (0, 0), None
                        try:
                            self._clear_player_ui(y_top, y_bar, y_time,
                                                  y_btn, W)
                        except Exception:
                            pass
                    elif not idle and ui_hidden:
                        ui_hidden = False         # el cursor volvió
                        drawn = None
                    want = duration > 0 or elapsed < 0
                    if not ui_hidden and not drag_on and want and \
                            drawn != (sec, paused, duration > 0):
                        drawn = (sec, paused, duration > 0)
                        try:
                            zones, bar_span = self._draw_player_ui(
                                y_top, y_bar, y_time, y_btn, W, elapsed,
                                duration, paused, title,
                                prev_ep is not None, next_ep is not None)
                        except Exception:
                            zones, bar_span = [], (0, 0)

                # ---------- teclado y ratón ----------
                if 0 in r:
                    guard = 0
                    while True:                    # drena toda la ráfaga
                        if not _sel.select([0], [], [], 0)[0]:
                            break
                        guard += 1
                        if guard > 256:            # techo por si acaso
                            break
                        key = tui.read_key(0)
                        if isinstance(key, str) and \
                                key.startswith(("mouse:", "mouseup:")):
                            try:
                                kind, btn_s, x_s, y_s = key.split(":")
                                mbtn, mx, my = int(btn_s), int(x_s), int(y_s)
                            except ValueError:
                                continue
                            last_motion = now   # cualquier gesto cuenta
                            if mbtn in (64, 65):       # rueda: salto ±5 s
                                self._ipc_send(
                                    sock, ["seek", 5 if mbtn == 64 else -5])
                                continue
                            if mbtn >= 32:
                                # botón agarrado: el círculo sigue al cursor
                                if drag_on and y_bar is not None and \
                                        duration > 0 and bar_span[1]:
                                    x0, bw = bar_span
                                    f = min(1.0, max(0.0,
                                                     (mx - x0 + 0.5) / bw))
                                    scrub[0] = duration * f
                                    try:   # fluido: dibuja YA en el punto
                                        zones, bar_span = \
                                            self._draw_player_ui(
                                                y_top, y_bar, y_time, y_btn,
                                                W, scrub[0], duration,
                                                paused, title,
                                                prev_ep is not None,
                                                next_ep is not None)
                                    except Exception:
                                        zones, bar_span = [], (0, 0)
                                    # el vídeo se sincroniza cada 0.5 s
                                    if now - last_scrub[0] >= 0.5:
                                        last_scrub[0] = now
                                        self._ipc_send(
                                            sock, ["seek", scrub[0],
                                                   "absolute"])
                                continue
                            if kind == "mouseup":
                                if drag_on and y_bar is not None and \
                                        duration > 0 and bar_span[1]:
                                    x0, bw = bar_span
                                    f = min(1.0, max(0.0,
                                                     (mx - x0 + 0.5) / bw))
                                    self._ipc_send(
                                        sock, ["seek", duration * f,
                                               "absolute"])   # asiente
                                    scrub[0] = -1.0
                                    drawn = None          # repinta con mpv
                                drag_on = False
                                continue
                            if mbtn > 2:
                                continue
                            # clic: si la UI está oculta solo la saca
                            hidden = ui_hidden
                            if y_bar is not None and my == y_bar and \
                                    duration > 0 and bar_span[1]:
                                if hidden:
                                    continue
                                drag_on = True          # agarrar la barra
                                last_scrub[0] = now
                                x0, bw = bar_span
                                f = min(1.0, max(0.0, (mx - x0 + 0.5) / bw))
                                scrub[0] = duration * f
                                try:       # fluido: dibuja YA en el punto
                                    zones, bar_span = self._draw_player_ui(
                                        y_top, y_bar, y_time, y_btn, W,
                                        scrub[0], duration, paused, title,
                                        prev_ep is not None,
                                        next_ep is not None)
                                except Exception:
                                    zones, bar_span = [], (0, 0)
                                self._ipc_send(sock, ["seek", scrub[0],
                                                      "absolute"])
                                continue
                            if my == y_btn:
                                if hidden:
                                    continue
                                for zx0, zx1, dest in zones:
                                    if zx0 <= mx <= zx1:
                                        act = dest
                                        self._ipc_send(sock, ["quit"])
                                        break
                                continue
                            continue
                        if key in ("q", "esc", "ctrl+c", "eof"):
                            act = None
                            self._ipc_send(sock, ["quit"])
                        elif key == "b":
                            act = "back"
                            self._ipc_send(sock, ["quit"])
                        elif key == "n" and next_ep:
                            act = "next"
                            self._ipc_send(sock, ["quit"])
                        elif key == "p" and prev_ep:
                            act = "prev"
                            self._ipc_send(sock, ["quit"])
                        elif key == "space":
                            paused = not paused
                            drawn = None
                            self._ipc_send(sock, ["cycle", "pause"])
                        elif key in ("right", "left"):
                            drawn = None
                            self._ipc_send(
                                sock, ["seek", 5 if key == "right" else -5])
                        elif key in ("up", "down"):
                            self._ipc_send(
                                sock, ["volume", 5 if key == "up" else -5])
                        elif key == "m":
                            self._ipc_send(sock, ["cycle", "mute"])
                        # el resto de teclas se ignoran durante la
                        # reproducción
                if act is not None:
                    break
            rc = proc.returncode if proc.returncode is not None else 0
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            try:
                os.unlink(ipc_path)
            except OSError:
                pass
            try:
                self.scr.write("\x1b[?1006l\x1b[?1003l")   # ratón fuera
                self.scr.flush()
            except Exception:
                pass
            if go_full:
                self._hypr_fullscreen(False, entered_fs is True)
        return rc, act

    @staticmethod
    def _ipc_send(sock, command, rid=None):
        try:
            import json as _js
            msg = {"command": command}
            if rid is not None:
                msg["request_id"] = rid
            sock.sendall(_js.dumps(msg).encode() + b"\n")
        except OSError:
            pass

    @staticmethod
    def _fmt_time(secs):
        """Segundos → mm:ss (o h:mm:ss si supera la hora)."""
        try:
            secs = int(max(0, secs))
        except (TypeError, ValueError):
            secs = 0
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)

    def mark_episode_viewed(self, anime, ep):
        uid = self.session.user_id
        self.db.mark_episode_viewed(uid, ep["id"], anime["id"])
        self.db.put_progress(uid, ep["id"], anime["id"], ep["number"], completed=True)
        if self.session.token:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            ok = False
            try:
                self.api.add_view(self.session.token, anime["id"])
                self.api.update_progress(self.session.token, ep["id"], anime["id"],
                                         ep["number"], 0, 0, True, now)
                ok = True
            except (ApiError, NetworkError) as exc:
                self.flash(f"Cambio local hecho; servidor sin confirmar ({exc})", "warn")
            if ok:
                self.flash("Episodio marcado como visto ✓ (sincronizado).", "ok")
        else:
            self.flash("Episodio marcado como visto ✓ (local).", "ok")

    def episode_unmark_viewed(self, anime, ep):
        uid = self.session.user_id
        self.db.unmark_episode_viewed(uid, ep["id"])
        if self.session.token:
            try:
                self.api.remove_view(self.session.token, anime["id"])
            except (ApiError, NetworkError) as exc:
                self.flash(f"Servidor sin confirmar ({exc})", "warn")
        self.flash("Episodio desmarcado.", "info")

    # ------------------------------------------------------------------ #
    # últimos episodios y continuar viendo
    # ------------------------------------------------------------------ #
    def scr_recent_episodes(self):
        rows = [dict(r) for r in self.db.con.execute(
            "SELECT e.id AS episode_id, e.number AS episode_number, "
            "a.id AS anime_id, a.name, a.* "
            "FROM EpisodeEntity e INNER JOIN AnimeEntity a ON a.id = e.anime_id "
            "ORDER BY e.id DESC LIMIT 60").fetchall()]
        if not rows:
            self.flash("Sin episodios en el catálogo.", "warn")
            return
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base(None, "Últimos episodios añadidos", HINT_BACK)
            items = [(f"{r['name']} — Ep {r['episode_number']}", None) for r in rows] + \
                    [("Volver", C.FG_DIM)]
            count = min(h - 1, len(items))
            self.menu(y, x + 1, count, w - 3, items, sel, offset)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key == "pgup":
                sel = max(0, sel - count)
            elif key == "pgdn":
                sel = min(len(items) - 1, sel + count)
            elif key in ("esc", "left", "q"):
                return
            elif key in ("enter", "right"):
                if sel == len(rows):
                    return
                r = rows[sel]
                anime = self.db.anime_by_id(r["anime_id"])
                ep = {"id": r["episode_id"], "number": r["episode_number"],
                      "anime_id": r["anime_id"]}
                self.episode_flow(anime, ep)

    def list_continue(self):
        uid = self.session.user_id
        rows = self.db.progress_rows(uid)
        if not rows:
            self.alert('Todavía no hay progreso. Empieza por «Novedades» y '
                       'marca episodios como vistos.', title="Continuar viendo")
            return
        items = []
        for r in rows:
            nxt = r["episode_number"] + (1 if r["completed"] else 0)
            items.append((f"{r['anime_name']} — Ep {nxt}", None))
        items.append(("Volver", C.FG_DIM))
        sel, offset = 0, 0
        while True:
            y, x, h, w = self.draw_base(None, "Continuar viendo", HINT_BACK)
            count = min(h - 1, len(items))
            self.menu(y, x + 1, count, w - 3, items, sel, offset)
            key = tui.read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
            elif key == "down":
                sel = (sel + 1) % len(items)
            elif key in ("esc", "left", "q"):
                return
            elif key in ("enter", "right"):
                if sel == len(rows):
                    return
                anime = self.db.anime_by_id(rows[sel]["anime_id"])
                if anime:
                    self.anime_detail(anime)

    # ================================================================= #
    # PERFIL
    # ================================================================= #
    def scr_perfil(self):
        if not self.session.is_logged:
            self.scr_auth()
            return
        u = self.session.user or {}
        while True:
            y, x, h, w = self.draw_base("perfil", "Perfil · sesión iniciada", HINT_BACK)
            prem = "  ★ PREMIUM" if u.get("is_premium") else ""
            self.scr.move(y, x + 1)
            self.scr.write(C.ACCENT + C.BOLD +
                           fit(f"{u.get('name', '')} <{u.get('email', '')}>{prem}", w - 2) +
                           C.RESET)
            self.scr.move(y + 1, x + 1)
            self.scr.write(C.FG_DIM + fit(
                f"{u.get('points', 0)} Puntos Kawaii · las listas se sincronizan "
                "con tu cuenta", w - 2))
            items = [
                ("★ Favoritos", None),
                ("▶ Viendo", None),
                ("⌛ Ver más tarde", None),
                ("✓ Vistos", None),
                ("⟳ Sincronizar listas con el servidor", None),
                ("Cerrar sesión", C.WARN),
                ("Volver", C.FG_DIM),
            ]
            sel, offset = 0, 0
            while True:
                self.menu(y + 3, x + 2, min(len(items), max(1, h - 4)), w - 4,
                          items, sel, offset)
                key = tui.read_key()
                if key == "up":
                    sel = (sel - 1) % len(items)
                elif key == "down":
                    sel = (sel + 1) % len(items)
                elif key in ("enter", "right"):
                    if sel == 0:
                        self.list_screen("★ Favoritos",
                                         self.db.favorites(self.session.user_id))
                    elif sel == 1:
                        self.list_screen("▶ Viendo",
                                         self.db.watching(self.session.user_id))
                    elif sel == 2:
                        self.list_screen("⌛ Ver más tarde",
                                         self.db.watchlater(self.session.user_id))
                    elif sel == 3:
                        self.list_screen("✓ Vistos",
                                         self.db.viewed(self.session.user_id))
                    elif sel == 4:
                        self.sync_lists()
                    elif sel == 5:
                        if self.confirm("¿Cerrar sesión? (tus listas locales "
                                        "de invitado no se borran)"):
                            try:
                                self.api.logout(self.session.token)
                            except (ApiError, NetworkError):
                                pass
                            self.session.clear()
                            self.flash("Sesión cerrada.", "info")
                    else:
                        return
                    break
                elif key in ("esc", "left"):
                    return

    # ------------------------------------------------------------------ #
    # autenticación
    # ------------------------------------------------------------------ #
    def scr_auth(self):
        while True:
            y, x, h, w = self.draw_base("perfil", "Perfil · invitado", HINT_BACK)
            self.scr.move(y, x + 1)
            self.scr.write(C.FG +
                           fit("No hay sesión iniciada. Puedes usar la app como "
                               "invitado (listas locales) o entrar con tu cuenta "
                               "de Kawaii Animes.", w - 2))
            items = [
                ("Iniciar sesión", None),
                ("Crear cuenta nueva", None),
                ("Recuperar contraseña", None),
                ("Volver", C.FG_DIM),
            ]
            sel = 0
            while True:
                self.menu(y + 4, x + 2, len(items), w - 4, items, sel, 0)
                key = tui.read_key()
                if key == "up":
                    sel = (sel - 1) % len(items)
                elif key == "down":
                    sel = (sel + 1) % len(items)
                elif key in ("enter", "right"):
                    if sel == 0:
                        self.login_flow()
                    elif sel == 1:
                        self.register_flow()
                    elif sel == 2:
                        self.recover_flow()
                    else:
                        return
                    break
                elif key in ("esc", "left"):
                    return

    def login_flow(self):
        email = self.input_box("Iniciar sesión", "Correo electrónico:")
        if email is None:
            return
        password = self.input_box("Iniciar sesión", "Contraseña:", password=True)
        if password is None:
            return
        self.flash("Iniciando sesión…")
        try:
            resp = self.api.login(email, password)
        except (ApiError, NetworkError) as exc:
            self.alert(str(exc), title="No se pudo iniciar sesión")
            return
        token, user = parse_auth(resp)
        if not token:
            self.alert("El servidor respondió, pero sin token. Inténtalo de nuevo.",
                       title="Login")
            return
        self.session.token = token
        self.session.update_user(user)
        self.flash(f"¡Hola, {user.get('name') or user.get('email') or email}!", "ok")
        self.sync_lists(silent=True)

    def register_flow(self):
        name = self.input_box("Crear cuenta", "Tu nombre o nick:")
        if name is None:
            return
        email = self.input_box("Crear cuenta", "Correo electrónico:")
        if email is None:
            return
        password = self.input_box("Crear cuenta", "Contraseña:", password=True)
        if password is None:
            return
        password2 = self.input_box("Crear cuenta", "Repite la contraseña:", password=True)
        if password2 is None:
            return
        if password != password2:
            self.alert("Las contraseñas no coinciden.", title="Registro")
            return
        if len(password) < 6:
            self.alert("La contraseña debe tener al menos 6 caracteres.", title="Registro")
            return
        self.flash("Creando cuenta…")
        try:
            resp = self.api.register(name, email, password)
        except (ApiError, NetworkError) as exc:
            self.alert(str(exc), title="No se pudo crear la cuenta")
            return
        token, user = parse_auth(resp)
        if not token:
            self.alert("El servidor respondió, pero sin token.", title="Registro")
            return
        self.session.token = token
        self.session.update_user(user)
        self.flash("Cuenta creada. ¡Bienvenido!", "ok")
        self.sync_lists(silent=True)

    def recover_flow(self):
        email = self.input_box("Recuperar contraseña",
                               "Correo de la cuenta:")
        if email is None:
            return
        self.flash("Solicitando código…")
        try:
            self.api.request_password_code(email)
        except (ApiError, NetworkError) as exc:
            self.alert(str(exc), title="Recuperar contraseña")
            return
        self.alert("Si el correo está registrado recibirás un código por email.",
                   title="Código enviado")
        code = self.input_box("Recuperar contraseña", "Código recibido:")
        if code is None:
            return
        new = self.input_box("Recuperar contraseña", "Contraseña nueva:", password=True)
        if new is None:
            return
        if len(new) < 6:
            self.alert("La contraseña debe tener al menos 6 caracteres.",
                       title="Recuperar")
            return
        self.flash("Restaurando contraseña…")
        try:
            self.api.restore_password(email, code, new)
        except (ApiError, NetworkError) as exc:
            self.alert(str(exc), title="Recuperar contraseña")
            return
        self.alert("Contraseña cambiada. Ahora inicia sesión con la nueva.",
                   title="Listo")

    def sync_lists(self, silent=False):
        if not self.session.is_logged:
            if not silent:
                self.alert("Inicia sesión primero.", title="Sincronizar listas")
            return
        self.flash("Descargando listas del servidor…", )
        self.draw_base(None, "Sincronizando…", "")
        try:
            resp = self.api.get_all_lists(self.session.token)
            lists = parse_lists(resp)
            self.db.merge_server_lists(self.session.user_id, lists)
        except (ApiError, NetworkError) as exc:
            if not silent:
                self.alert(str(exc), title="Sincronizar listas")
            else:
                self.flash(f"Listas no sincronizadas: {exc}", "warn")
            return
        try:
            fresh = parse_user(self.api.get_user(self.session.token))
            if fresh.get("id"):
                self.session.update_user(fresh)
        except (ApiError, NetworkError):
            pass
        n_f = len(lists.get("favorites", []))
        n_w = len(lists.get("watching", []))
        n_l = len(lists.get("watchlater", []))
        n_v = len(lists.get("views", []))
        n_p = len(lists.get("progress", []))
        self.flash(f"Listas sincronizadas: {n_f} fav · {n_w} viendo · {n_l} luego · "
                   f"{n_v} vistos · {n_p} progreso", "ok")

    # ================================================================= #
    # AJUSTES
    # ================================================================= #
    def scr_ajustes(self):
        while True:
            y, x, h, w = self.draw_base("ajustes", "Ajustes", HINT_BACK)
            items = [
                ("⟳ Sincronizar catálogo (incremental)", None),
                ("⬇ Reconstruir catálogo completo (API)", C.WARN),
                ("⟳ Sincronizar mis listas",
                 None if self.session.is_logged else C.FG_DIM),
                ("📊 Estado del catálogo", None),
                ("ℹ Acerca de", None),
                ("Volver", C.FG_DIM),
            ]
            sel, offset = 0, 0
            while True:
                self.menu(y + 1, x + 2, min(len(items), max(1, h - 2)), w - 4,
                          items, sel, offset)
                key = tui.read_key()
                if key == "up":
                    sel = (sel - 1) % len(items)
                elif key == "down":
                    sel = (sel + 1) % len(items)
                elif key in ("enter", "right"):
                    if sel == 0:
                        self.do_incremental_sync()
                    elif sel == 1:
                        self.do_bootstrap()
                    elif sel == 2:
                        self.sync_lists()
                    elif sel == 3:
                        self.show_catalog_status()
                    elif sel == 4:
                        self.show_about()
                    else:
                        return
                    break
                elif key in ("esc", "left"):
                    return

    def do_incremental_sync(self):
        self.flash("Sincronizando catálogo…")
        self.draw_base(None, "Sincronizando…", "")
        try:
            applied = self.db.incremental_sync(self.api)
        except (ApiError, NetworkError) as exc:
            self.alert(str(exc), title="Sincronización incremental")
            return
        self.db.set_meta("last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.alert(f"Listo: {applied} filas nuevas/actualizadas desde el servidor.",
                   title="Sincronización incremental")

    def do_bootstrap(self):
        if not self.confirm("Esto descarga todo el catálogo por la API y puede "
                            "tardar varios minutos y consumir bastante tráfico. "
                            "¿Continuar?", title="Catálogo completo"):
            return
        def progress(target, last_id):
            self.flash(f"Sincronizando {target}… (id {last_id})", "info")
        try:
            self.db.bootstrap_from_api(self.api, progress)
        except (ApiError, NetworkError) as exc:
            self.alert(str(exc), title="Catálogo completo")
            return
        self.db.set_meta("last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.alert(f"Catálogo completo descargado: {self.db.count_animes()} animes, "
                   f"{self.db.count_episodes()} episodios.",
                   title="Catálogo completo")

    def show_catalog_status(self):
        lines = [
            f"Animes:        {self.db.count_animes()}",
            f"Episodios:     {self.db.count_episodes()}",
            f"Players:       {self.db.count_players()}",
            f"Servidores:    {self.db.count_servers()}",
            "",
            "Cursores de sync:",
            f"  lei  (último episodio):  {self.db.max_episode_id()}",
            f"  laua (animes):           {self.db.max_anime_updated()}",
            f"  lpua (players):          {self.db.max_player_updated()}",
            "",
            "Base de datos: " + self.db.path,
            "Sesión: " + self.session.label + "   (config: " + config.SESSION_PATH + ")",
        ]
        self.alert("\n".join(lines), title="Estado del catálogo")

    def show_about(self):
        lines = [
            "AniLinux Terminal — cliente de terminal para Kawaii Animes.",
            "",
            "Programa nativo para GNU/Linux escrito en Python 3 (sin",
            "dependencias externas). Reimplementa el protocolo del API v4",
            "que usa la app Android: catálogo con sync incremental y",
            "cuentas de usuario (login/registro/listas/progreso).",
            "",
            f"Versión: {config.APP_VERSION}",
            f"API:     {config.BASE_URL}",
            f"Datos:   {config.DATA_DIR}",
            f"Config:  {config.CONFIG_DIR}",
        ]
        self.alert("\n".join(lines), title="Acerca de")
