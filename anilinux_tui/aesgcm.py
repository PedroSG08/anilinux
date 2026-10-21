# -*- coding: utf-8 -*-
"""AES-GCM puro Python (solo stdlib) para descifrar el blob `playback` de Byse.

Implementación directa y correcta por encima de todo: AES 128/192/256 byte a
byte (column-major FIPS-197) + contador CTR (J0 = IV || 0x00000001, inc32) +
GHASH en GF(2^128) (polinomio R = 0xE1 << 120). Los blobs son pequeños.
"""

# ------------------------------------------------------------- GF(2^8) -- #
def _mul8(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return r


def _build_sbox():
    fwd = [0] * 256
    for x in range(1, 256):
        for i in range(1, 256):
            if _mul8(x, i) == 1:
                break
        s = i
        r = i
        for _ in range(4):
            r = ((r << 1) | (r >> 7)) & 0xFF
            s ^= r
        fwd[x] = s ^ 0x63
    fwd[0] = 0x63
    return fwd


SBOX = _build_sbox()
ISBOX = [0] * 256
for _x, _s in enumerate(SBOX):
    ISBOX[_s] = _x

RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
        0x6C, 0xD8, 0xAB, 0x4D)


def _x2(b):
    b <<= 1
    return (b ^ 0x1B) & 0xFF if b & 0x100 else b


# --------------------------------------------------------------- AES ---- #
class AES:
    def __init__(self, key):
        if len(key) not in (16, 24, 32):
            raise ValueError("la clave AES debe medir 16/24/32 bytes")
        nk = len(key) // 4
        self.rounds = nk + 6
        words = [int.from_bytes(key[i * 4:i * 4 + 4], "big") for i in range(nk)]
        total = 4 * (self.rounds + 1)
        for i in range(nk, total):
            w = words[i - 1]
            if i % nk == 0:
                w = ((w << 8) & 0xFFFFFFFF) | (w >> 24)        # rotword
                w = (SBOX[(w >> 24) & 0xFF] << 24 | SBOX[(w >> 16) & 0xFF] << 16 |
                     SBOX[(w >> 8) & 0xFF] << 8 | SBOX[w & 0xFF])
                w ^= RCON[i // nk - 1] << 24
            elif nk > 6 and i % nk == 4:
                w = (SBOX[(w >> 24) & 0xFF] << 24 | SBOX[(w >> 16) & 0xFF] << 16 |
                     SBOX[(w >> 8) & 0xFF] << 8 | SBOX[w & 0xFF])
            words.append(words[i - nk] ^ w)
        self.rkb = []
        for w in words:
            self.rkb += [(w >> 24) & 0xFF, (w >> 16) & 0xFF,
                         (w >> 8) & 0xFF, w & 0xFF]

    def encrypt_block(self, block):
        if len(block) != 16:
            raise ValueError
        s = bytearray(block)
        for i in range(16):
            s[i] ^= self.rkb[i]
        for rnd in range(1, self.rounds + 1):
            # SubBytes + ShiftRows (estado column-major: s[r + 4c])
            for r in range(4):
                row = [SBOX[s[r + 4 * c]] for c in range(4)]
                row = row[r:] + row[:r]
                for c in range(4):
                    s[r + 4 * c] = row[c]
            if rnd < self.rounds:
                # MixColumns columna a columna
                for c in range(4):
                    b = s[4 * c:4 * c + 4]
                    x3 = lambda v: _x2(v) ^ v
                    s[4 * c] = _x2(b[0]) ^ x3(b[1]) ^ b[2] ^ b[3]
                    s[4 * c + 1] = b[0] ^ _x2(b[1]) ^ x3(b[2]) ^ b[3]
                    s[4 * c + 2] = b[0] ^ b[1] ^ _x2(b[2]) ^ x3(b[3])
                    s[4 * c + 3] = x3(b[0]) ^ b[1] ^ b[2] ^ _x2(b[3])
            off = rnd * 16
            for i in range(16):
                s[i] ^= self.rkb[off + i]
        return bytes(s)


# -------------------------------------------------------------- GHASH -- #
_R = 0xE1 << 120


def _gmul(x, y):
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _R
        else:
            v >>= 1
    return z


def _blocks(data, blocklen=16):
    n = len(data)
    full = n // blocklen
    for i in range(full):
        yield data[i * blocklen:(i + 1) * blocklen]
    rest = n - full * blocklen
    if rest:
        yield data[full * blocklen:] + b"\0" * (blocklen - rest)


def _pad_block(b):
    if len(b) % 16:
        b += b"\0" * (16 - len(b) % 16)
    return b


def ghash(h_int, aad, data):
    y = 0
    for blk in _blocks(_pad_block(aad) if aad else b""):
        y ^= int.from_bytes(blk, "big")
        y = _gmul(y, h_int)
    for blk in _blocks(_pad_block(data) if data else b""):
        y ^= int.from_bytes(blk, "big")
        y = _gmul(y, h_int)
    lb = (len(aad) * 8).to_bytes(8, "big") + (len(data) * 8).to_bytes(8, "big")
    y ^= int.from_bytes(lb, "big")
    y = _gmul(y, h_int)
    return y.to_bytes(16, "big")


# ---------------------------------------------------------------- GCM --- #
def _xor(a, b):
    return bytes(u ^ v for u, v in zip(a, b))


def gcm_decrypt(key, iv, data, tag, aad=b"", tag_len=16):
    """Devuelve texto claro o lanza ValueError si el tag no coincide."""
    aes = AES(key)
    h_int = int.from_bytes(aes.encrypt_block(b"\0" * 16), "big")
    if len(tag) != tag_len:
        raise ValueError("tag de longitud inesperada")
    ctr_base = iv + (1).to_bytes(4, "big")   # J0
    s = ghash(h_int, aad, data)
    expected = _xor(aes.encrypt_block(ctr_base), s)
    if expected[:tag_len] != tag:
        raise ValueError("GCM: tag inválido")
    # CTR desde el contador 2 (sin rellenar: el último bloque se recorta solo)
    counter = 2
    out = bytearray()
    for i in range(0, len(data), 16):
        cblock = aes.encrypt_block(iv + counter.to_bytes(4, "big"))
        out += _xor(data[i:i + 16], cblock)
        counter = (counter + 1) & 0xFFFFFFFF if counter < 0xFFFFFFFF else 0
    return bytes(out)
