# -*- coding: utf-8 -*-
"""Primitivas de terminal (ANSI) para la TUI de AniLinux Terminal.

Teclado en modo crudo (termios), parsing de flechas y widgets simples:
cajas, menús navegables, entradas de texto y decoración.
"""
import os
import re
import select
import shutil
import sys
import termios
import textwrap
import tty

# ---------------------------------------------------------------------- #
# paleta (256 colores)
# ---------------------------------------------------------------------- #
class C:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    FG = "\x1b[38;5;250m"
    FG_DIM = "\x1b[38;5;243m"
    ACCENT = "\x1b[38;5;213m"     # rosa kawaii
    ACCENT2 = "\x1b[38;5;153m"    # azul claro
    OK = "\x1b[38;5;114m"
    WARN = "\x1b[38;5;214m"
    ERR = "\x1b[38;5;210m"
    BORDER = "\x1b[38;5;60m"
    SEL_BG = "\x1b[48;5;236m"
    TITLE_BG = "\x1b[48;5;54m"
    RAIL_BG = "\x1b[48;5;234m"
    STATUS_BG = "\x1b[48;5;236m"

    @staticmethod
    def strip(s):
        return re.sub(_ESC_RE, "", s)

    @staticmethod
    def visible_len(s):
        return len(C.strip(s))


# ---------------------------------------------------------------------- #
# pantalla
# ---------------------------------------------------------------------- #
class Screen:
    def __init__(self):
        self.size = (100, 30)
        self._out = sys.stdout

    def enter(self):
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
        sys.stdout.flush()
        self.size = self.poll_size()

    def leave(self):
        sys.stdout.write("\x1b[0m\x1b[2J\x1b[H\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()

    def poll_size(self):
        try:
            size = shutil.get_terminal_size(fallback=(100, 30))
            return size.columns, size.lines
        except Exception:
            return self.size

    def write(self, s):
        self._out.write(s)

    def flush(self):
        self._out.flush()

    def clear(self):
        self.write("\x1b[2J\x1b[H")

    def move(self, y, x):
        self.write(f"\x1b[{y + 1};{x + 1}H")

    def fill(self, y, x, w, bg):
        self.write(f"\x1b[{y + 1};{x + 1}H\x1b[{bg}{'' if bg else ''}")
        self.write(" " * w + C.RESET)


# ---------------------------------------------------------------------- #
# geometría y texto
# ---------------------------------------------------------------------- #
def fit(text, width):
    """Recorta al ancho visible (respeta códigos ANSI)."""
    text = text or ""
    if C.visible_len(text) <= width:
        return text
    out, used = [], 0
    i = 0
    while i < len(text) and used < width:
        ch = text[i]
        if ch == "\x1b":
            m = re.match("\x1b\\[[0-9;]*m", text[i:])
            if m:
                out.append(m.group(0))
                i += len(m.group(0))
                continue
        w = _char_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
        i += 1
    return "".join(out)


_ESC_RE = re.compile("\x1b\\[[0-9;]*m")


def _char_width(ch):
    o = ord(ch)
    if o == 0:
        return 0
    if o < 32:
        return 0
    # apróx. doble ancho para CJK/emoji comunes
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or
            0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or
            0xFE30 <= o <= 0xFE6F or 0xFF00 <= o <= 0xFF60 or
            0xFFE0 <= o <= 0xFFE6 or 0x1F300 <= o <= 0x1F9FF):
        return 2
    return 1


def visible_len(s):
    """Longitud visible de una cadena (ignora ANSI y mide ancho CJK)."""
    return sum(_char_width(ch) for ch in C.strip(s))


def pad(text, width):
    missing = width - visible_len(text)
    return text + " " * max(0, missing)


def wrap(text, width):
    if width <= 4:
        return []
    clean = C.strip(text or "")
    return textwrap.wrap(clean, width=width, break_long_words=True,
                         break_on_hyphens=False) or [""]


# ---------------------------------------------------------------------- #
# caja con título
# ---------------------------------------------------------------------- #
def draw_box(scr, y0, x0, h, w, title="", color=C.BORDER, bg="", title_color=None):
    if h <= 1 or w <= 2:
        return
    top = "╭" + "─" * (w - 2) + "╮"
    bot = "╰" + "─" * (w - 2) + "╯"
    for dy, content in ((0, top), (h - 1, bot)):
        scr.move(y0 + dy, x0)
        scr.write(color + content + C.RESET)
    for dy in range(1, h - 1):
        scr.move(y0 + dy, x0)
        scr.write(color + "│" + C.RESET)
        scr.move(y0 + dy, x0 + w - 1)
        scr.write(color + "│" + C.RESET)
    if title:
        scr.move(y0, x0 + 2)
        t = fit(title, w - 4)
        tc = title_color or color
        scr.write(tc + C.BOLD + t + C.RESET + " " + color)


# ---------------------------------------------------------------------- #
# teclado
# ---------------------------------------------------------------------- #
_KEYBUF = b""
_CSI_TRY = re.compile(rb"\x1b\[([0-9;<]*)([A-Za-z~M])")


def _poll_read(fd, wait):
    r, _, _ = select.select([fd], [], [], wait)
    if r:
        return os.read(fd, 256)
    return b""


def _map_single(ch):
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\t":
        return "tab"
    if ch in ("\x7f", "\x08"):
        return "backspace"
    if ch == " ":
        return "space"
    if ch < " ":
        names = {"\x01": "ctrl+a", "\x02": "ctrl+b", "\x03": "ctrl+c",
                 "\x04": "ctrl+d", "\x05": "ctrl+e", "\x06": "ctrl+f",
                 "\x07": "ctrl+g", "\x08": "ctrl+h", "\x0c": "ctrl+l",
                 "\x0e": "ctrl+n", "\x10": "ctrl+p", "\x11": "ctrl+q",
                 "\x13": "ctrl+s", "\x14": "ctrl+t", "\x15": "ctrl+u",
                 "\x17": "ctrl+w", "\x1a": "ctrl+z"}
        return names.get(ch, "ctrl+?")
    return ch


def _flush_out():
    """Vuelca cualquier marco dibujado pendiente antes de bloquear por input.

    Sin esto, en una terminal real (stdout con buffering de línea y los
    marcos escritos sin '\n') el primer fotograma puede quedarse en el
    buffer: el usuario ve la pantalla alternativa totalmente en blanco.
    """
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def read_key(fd=0):
    """Lee una tecla (bloqueante) preservando los bytes que no formen parte
    de la secuencia: soporta ráfagas (pegar texto, teclas pegadas)."""
    global _KEYBUF
    while True:
        if not _KEYBUF:
            _flush_out()
            chunk = os.read(fd, 1)
            if not chunk:
                return "eof"
            _KEYBUF = chunk
        data = _KEYBUF
        b0 = data[:1]
        if b0 != b"\x1b":
            # un carácter simple (con soporte UTF-8 multibyte)
            first = data[0]
            need = 1
            if first >= 0xF0:
                need = 4
            elif first >= 0xE0:
                need = 3
            elif first >= 0xC0:
                need = 2
            while len(data) < need:
                more = _poll_read(fd, 0.05)
                if not more:
                    break
                data += more
                _KEYBUF = data
            if len(data) >= need:
                _KEYBUF = data[need:]
                return _map_single(data[:need].decode("utf-8", "replace"))
            _KEYBUF = b""
            return _map_single(data.decode("utf-8", "replace"))
        # posible secuencia de escape: traer más bytes si hace falta
        if len(data) == 1:
            data += _poll_read(fd, 0.04)
            _KEYBUF = data
        m = _CSI_TRY.match(_KEYBUF)
        if not m:
            if data[:2] == b"\x1b[":
                # CSI incompleto: esperar del resto
                data += _poll_read(fd, 0.05)
                _KEYBUF = data
                m = _CSI_TRY.match(_KEYBUF)
                if not m:
                    _KEYBUF = b""
                    return "esc"
            elif len(_KEYBUF) >= 2 and _KEYBUF[1:2] in (b"O", b"P", b"N"):
                ss = _KEYBUF[1:3]
                _KEYBUF = _KEYBUF[3:]
                if ss[0] == 79:  # 'O'
                    return {b"A": "up", b"B": "down", b"C": "right",
                            b"D": "left"}.get(ss[1:], "??")
                return "other"
            else:
                # ESC a secas o alt+tecla → Esc (descartando el modificador)
                _KEYBUF = _KEYBUF[1:]
                return "esc"
        if not m:
            continue
        _KEYBUF = _KEYBUF[m.end():]
        params, final = m.group(1), m.group(2)
        if final == b"A":
            return "up"
        if final == b"B":
            return "down"
        if final == b"C":
            return "right"
        if final == b"D":
            return "left"
        if final == b"H" and not params:
            return "home"
        if final == b"F" and not params:
            return "end"
        if final == b"~":
            n = int(params or b"0")
            return {1: "home", 2: "insert", 3: "delete", 4: "end",
                    5: "pgup", 6: "pgdn"}.get(n, "??")
        if final == b"M" and not params.startswith(b"<"):
            # ratón X10: ignorar
            continue
        if final in (b"M", b"m") and params.startswith(b"<"):
            # ratón SGR (1006): <botón;x;y  (M=pulsar, m=soltar)
            ps = params[1:].split(b";")
            if len(ps) == 3:
                try:
                    return (("mouse:%d:%d:%d" if final == b"M"
                             else "mouseup:%d:%d:%d")
                            % (int(ps[0]), int(ps[1]), int(ps[2])))
                except ValueError:
                    pass
            continue
        return "??"


def _is_partial(text):
    return text.endswith("�")


def raw_mode():
    fd = 0
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    class _Raw:
        def __enter__(self):
            return old

        def __exit__(self, *x):
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return _Raw()


def make_raw():
    """Activa modo crudo y devuelve (callable_restore)."""
    fd = 0
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def restore():
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except termios.error:
            pass

    return restore


def can_raw():
    return hasattr(termios, "tcgetattr") and sys.stdin is not None and sys.stdin.isatty()
