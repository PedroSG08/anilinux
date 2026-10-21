# -*- coding: utf-8 -*-
"""Base de datos local (SQLite) de AniLinux Terminal.

Mismo esquema de catálogo que usa la app Android, de modo que el catálogo
incluido en el APK (assets/databases/kawaii_animes.db) se puede importar
directamente.  Cuando no hay APK disponible, se reconstruye desde el API
público con /bulk-sync (server → anime → episode → player).

Sincronización incremental con POST /sync usando cursores:
  lei   = MAX(EpisodeEntity.id)
  laua  = MAX(AnimeEntity.updated_at)
  lpua  = MAX(PlayerEntity.updated_at)
"""
import os
import sqlite3
import time
import zipfile

from . import api as api_mod

SCHEMA = """
CREATE TABLE IF NOT EXISTS AnimeEntity (
    id INTEGER PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    name_alternative TEXT,
    poster TEXT,
    banner TEXT,
    overview TEXT,
    aired TEXT,
    type TEXT NOT NULL,
    status INTEGER NOT NULL DEFAULT 0,
    genres TEXT,
    rating TEXT,
    vote_average REAL NOT NULL DEFAULT 0.0,
    season TEXT,
    broadcast INTEGER NOT NULL DEFAULT 0,
    prequel INTEGER,
    sequel INTEGER,
    related TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS EpisodeEntity (
    id INTEGER PRIMARY KEY NOT NULL,
    number INTEGER NOT NULL,
    anime_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    ad_rewarded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS PlayerEntity (
    id INTEGER PRIMARY KEY NOT NULL,
    link TEXT NOT NULL,
    language TEXT NOT NULL,
    server_id INTEGER NOT NULL,
    episode_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ServerEntity (
    id INTEGER PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    embed TEXT,
    status INTEGER
);
CREATE TABLE IF NOT EXISTS UserFavorite (
    anime_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (anime_id, user_id));
CREATE TABLE IF NOT EXISTS UserWatching (
    anime_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (anime_id, user_id));
CREATE TABLE IF NOT EXISTS UserView (
    anime_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (anime_id, user_id));
CREATE TABLE IF NOT EXISTS UserWatchLater (
    anime_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (anime_id, user_id));
CREATE TABLE IF NOT EXISTS WatchProgressEntity (
    episode_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
    anime_id INTEGER NOT NULL, episode_number INTEGER NOT NULL,
    position_ms INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (episode_id, user_id));
CREATE TABLE IF NOT EXISTS ViewedEpisodeEntity (
    episode_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
    anime_id INTEGER NOT NULL, viewed_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (episode_id, user_id));
CREATE TABLE IF NOT EXISTS SearchedAnimeEntity (
    anime_id INTEGER PRIMARY KEY NOT NULL, visited_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS _kmeta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_episodes_anime ON EpisodeEntity(anime_id);
CREATE INDEX IF NOT EXISTS idx_players_episode ON PlayerEntity(episode_id);
CREATE INDEX IF NOT EXISTS idx_progress_updated ON WatchProgressEntity(updated_at);
"""

ANIME_COLS = (
    "id, name, name_alternative, poster, banner, overview, aired, type, status, "
    "genres, rating, vote_average, season, broadcast, prequel, sequel, related, "
    "views, updated_at"
)

# Orden del catálogo completo: primero servidores, luego animes/episodios/players
BULK_TARGETS = ("server", "anime", "episode", "player")
BULK_PAGE = 400

SRC_UPSERT = {
    "server": "INSERT OR REPLACE INTO ServerEntity (id, name, position, embed, status) "
              "VALUES (?, ?, ?, ?, ?)",
    "anime": "INSERT OR REPLACE INTO AnimeEntity (id, name, name_alternative, poster, "
             "banner, overview, aired, type, status, genres, rating, vote_average, "
             "season, broadcast, prequel, sequel, related, views, updated_at) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "episode": "INSERT OR REPLACE INTO EpisodeEntity (id, number, anime_id, created_at, ad_rewarded) "
               "VALUES (?, ?, ?, ?, 0)",
    "player": "INSERT OR REPLACE INTO PlayerEntity (id, link, language, server_id, episode_id, updated_at) "
              "VALUES (?, ?, ?, ?, ?, ?)",
}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Catalog:
    """Gestión de la base de datos local."""

    def __init__(self, path):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # infraestructura
    # ------------------------------------------------------------------ #
    def _ensure_schema(self):
        had = self._table_meta("bootstrap_done")
        self.con.executescript(SCHEMA)
        self.con.commit()
        if had is None and self.count_animes() == 0:
            # DB recién creada por el cliente: vale como catálogo descargable
            pass

    def _table_meta(self, key):
        try:
            row = self.con.execute("SELECT v FROM _kmeta WHERE k=?", (key,)).fetchone()
            return row["v"] if row else None
        except sqlite3.Error:
            return None

    def set_meta(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO _kmeta (k, v) VALUES (?, ?)",
                         (key, str(value)))
        self.con.commit()

    def get_meta(self, key, default=None):
        val = self._table_meta(key)
        return default if val is None else val

    def close(self):
        try:
            self.con.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ #
    # importación del catálogo incluido en el APK
    # ------------------------------------------------------------------ #
    def import_from_apk(self, apk_path, progress=None):
        """Extrae la base de datos del APK y la usa como catálogo."""
        with zipfile.ZipFile(apk_path) as zf:
            names = zf.namelist()
            target = None
            for n in names:
                if n == "assets/databases/kawaii_animes.db":
                    target = n
                    break
            if not target:
                for n in names:
                    if n.endswith(".db"):
                        target = n
                        break
            if not target:
                raise FileNotFoundError("El APK no contiene una base de datos.")
            tmp = self.path + ".import"
            with zf.open(target) as src, open(tmp, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
        self.close()
        os.replace(tmp, self.path)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.commit()
        return self.count_animes()

    # ------------------------------------------------------------------ #
    # catálogo completo desde el API (fallback si no hay APK)
    # ------------------------------------------------------------------ #
    def bootstrap_from_api(self, api_ref, progress=None):
        """Descarga el catálogo completo paginando POST /bulk-sync."""
        cursor = 0
        for target in BULK_TARGETS:
            last_id = 0
            while True:
                resp = api_ref.bulk_sync(target, last_id, BULK_PAGE)
                self.apply_sync_payload(resp)
                new_last = int(resp.get("li", last_id) or last_id)
                has_more = bool(resp.get("hm"))
                # li es el id máximo del paquete; avanza la paginación
                if target == "server":
                    last_id = new_last
                elif not has_more and new_last <= last_id:
                    last_id = new_last
                else:
                    last_id = new_last
                if progress:
                    progress(target, last_id)
                if not has_more:
                    break
            cursor = max(cursor, 0)
        self.set_meta("bootstrap_done", "1")

    # ------------------------------------------------------------------ #
    # aplicación de paquetes de sincronización
    # ------------------------------------------------------------------ #
    @staticmethod
    def _anime_row(d):
        return (d.get("id"), d.get("n"), d.get("na"), d.get("p"), d.get("b"),
                d.get("o"), d.get("ai"), d.get("t", "TV"), int(d.get("s", 0) or 0),
                d.get("g"), d.get("r"), float(d.get("va", 0) or 0), d.get("sn"),
                int(d.get("bc", 0) or 0), d.get("pq"), d.get("sq"), d.get("rl"),
                int(d.get("v", 0) or 0), d.get("ua", ""))

    @staticmethod
    def _episode_row(d):
        return (d.get("id"), int(d.get("n", 0) or 0), d.get("aid"), d.get("ca", ""))

    @staticmethod
    def _player_row(d):
        return (d.get("id"), d.get("lk", ""), str(d.get("lg", "0")),
                int(d.get("sid", 0) or 0), int(d.get("eid", 0) or 0), d.get("ua", ""))

    @staticmethod
    def _server_row(d):
        return (d.get("id"), d.get("n", ""), int(d.get("po", 0) or 0),
                d.get("em"), d.get("st"))

    def _upsert(self, target, rows):
        if not rows:
            return 0
        stmt = SRC_UPSERT[target]
        args = [self._server_row(d) for d in rows] if target == "server" else \
               [self._anime_row(d) for d in rows] if target == "anime" else \
               [self._episode_row(d) for d in rows] if target == "episode" else \
               [self._player_row(d) for d in rows]
        self.con.executemany(stmt, args)
        self.con.commit()
        return len(args)

    def apply_sync_payload(self, payload):
        """Procesa un SyncResponseDto: {'a':[], 'e':[], 'p':[], 's':[], ...}."""
        n = 0
        n += self._upsert("anime", payload.get("a") or [])
        n += self._upsert("episode", payload.get("e") or [])
        n += self._upsert("player", payload.get("p") or [])
        n += self._upsert("server", payload.get("s") or [])
        return n

    def incremental_sync(self, api_ref, progress=None):
        """POST /sync con los cursores guardados.  Devuelve filas aplicadas."""
        lei = int(self.max_episode_id() or 0)
        laua = self.max_anime_updated() or "1970-01-01"
        lpua = self.max_player_updated() or "1970-01-01"
        total = 0
        guard = 0
        while guard < 200:
            guard += 1
            resp = api_ref.sync(lei, laua, lpua)
            applied = self.apply_sync_payload(resp)
            total += applied
            if progress:
                progress(applied)
            if not resp.get("hm"):
                break
            new_lei = self.max_episode_id()
            new_laua = self.max_anime_updated()
            new_lpua = self.max_player_updated()
            if applied == 0 and (new_lei == lei and new_laua == laua and new_lpua == lpua):
                break
            lei, laua, lpua = int(new_lei or lei), new_laua, new_lpua
        return total

    # ------------------------------------------------------------------ #
    # estadísticas / cursores
    # ------------------------------------------------------------------ #
    def _scalar(self, sql, params=()):
        try:
            row = self.con.execute(sql, params).fetchone()
            if row is None:
                return None
            return row[0]
        except sqlite3.Error:
            return None

    def count_animes(self):
        return int(self._scalar("SELECT COUNT(*) FROM AnimeEntity") or 0)

    def count_episodes(self):
        return int(self._scalar("SELECT COUNT(*) FROM EpisodeEntity") or 0)

    def count_players(self):
        return int(self._scalar("SELECT COUNT(*) FROM PlayerEntity") or 0)

    def count_servers(self):
        return int(self._scalar("SELECT COUNT(*) FROM ServerEntity") or 0)

    def max_episode_id(self):
        return self._scalar("SELECT MAX(id) FROM EpisodeEntity") or 0

    def max_anime_updated(self):
        return self._scalar("SELECT MAX(updated_at) FROM AnimeEntity") or "1970-01-01"

    def max_player_updated(self):
        return self._scalar("SELECT MAX(updated_at) FROM PlayerEntity") or "1970-01-01"

    # ------------------------------------------------------------------ #
    # consultas de catálogo (misma semántica que la app)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _anime_sql(extra=""):
        return ("SELECT a.id, a.name, a.name_alternative, a.poster, a.banner, a.overview, "
                "a.aired, a.type, a.status, a.genres, a.rating, a.vote_average, a.season, "
                "a.broadcast, a.prequel, a.sequel, a.related, a.views, a.updated_at "
                "FROM AnimeEntity a WHERE EXISTS "
                "(SELECT 1 FROM EpisodeEntity e WHERE e.anime_id = a.id)" + " " + extra)

    def _rows(self, sql, params=()):
        return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def new_animes(self, limit=60):
        return self._rows(self._anime_sql("ORDER BY a.id DESC LIMIT ?"), (limit,))

    def popular_animes(self, limit=60):
        return self._rows(self._anime_sql("ORDER BY a.views DESC LIMIT ?"), (limit,))

    def top_rated(self, limit=60):
        return self._rows(self._anime_sql("AND a.vote_average > 0 "
                                          "ORDER BY a.vote_average DESC LIMIT ?"), (limit,))

    def on_air(self, limit=60):
        return self._rows(self._anime_sql("AND a.status = 1 ORDER BY a.id DESC LIMIT ?"), (limit,))

    def finished(self, limit=60):
        return self._rows(self._anime_sql("AND a.status = 0 ORDER BY a.id DESC LIMIT ?"), (limit,))

    def by_type(self, t, limit=60):
        return self._rows(self._anime_sql("AND a.type = ? ORDER BY a.id DESC LIMIT ?"), (t, limit))

    def all_with_episodes(self):
        return self._rows(self._anime_sql("ORDER BY a.name ASC"))

    def with_language(self, lang, limit=60):
        return self._rows(
            self._anime_sql("AND p.language = ? GROUP BY a.id ORDER BY MAX(p.id) DESC LIMIT ?")
            .replace(
                "SELECT a.id, a.name, a.name_alternative, a.poster, a.banner, a.overview, "
                "a.aired, a.type, a.status, a.genres, a.rating, a.vote_average, a.season, "
                "a.broadcast, a.prequel, a.sequel, a.related, a.views, a.updated_at ",
                "SELECT a.id, a.name, a.name_alternative, a.poster, a.banner, a.overview, "
                "a.aired, a.type, a.status, a.genres, a.rating, a.vote_average, a.season, "
                "a.broadcast, a.prequel, a.sequel, a.related, a.views, a.updated_at "),
            (lang, limit)) if False else self._rows(
            "SELECT a.id, a.name, a.name_alternative, a.poster, a.banner, a.overview, "
            "a.aired, a.type, a.status, a.genres, a.rating, a.vote_average, a.season, "
            "a.broadcast, a.prequel, a.sequel, a.related, a.views, a.updated_at "
            "FROM AnimeEntity a INNER JOIN EpisodeEntity e ON e.anime_id = a.id "
            "INNER JOIN PlayerEntity p ON p.episode_id = e.id WHERE p.language = ? "
            "GROUP BY a.id ORDER BY MAX(p.id) DESC LIMIT ?", (lang, limit))

    def search(self, query, limit=80):
        q = f"%{query.strip()}%"
        return self._rows(self._anime_sql(
            "AND (a.name LIKE ? OR a.name_alternative LIKE ?) ORDER BY a.name ASC"),
            (q, q))[:limit]

    def by_genre(self, genre, limit=200):
        return self._rows(self._anime_sql("AND a.genres LIKE ? ORDER BY a.name ASC"),
                          (f"%{genre}%",))

    def calendar(self, broadcast_day):
        return self._rows(self._anime_sql(
            "AND a.broadcast = ? AND a.status = 1 ORDER BY a.aired DESC"),
            (int(broadcast_day),))

    def by_ids(self, ids):
        ids = [int(i) for i in ids if i]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self._rows(
            "SELECT a.* FROM AnimeEntity a WHERE a.id IN (%s) ORDER BY a.name ASC" % marks,
            ids)

    def anime_by_id(self, anime_id):
        row = self.con.execute("SELECT * FROM AnimeEntity WHERE id = ?",
                               (int(anime_id),)).fetchone()
        return dict(row) if row else None

    def episodes_for_anime(self, anime_id):
        return [dict(r) for r in self.con.execute(
            "SELECT id, number, anime_id, created_at FROM EpisodeEntity "
            "WHERE anime_id = ? ORDER BY number ASC", (int(anime_id),)).fetchall()]

    def players_for_episode(self, episode_id):
        return [dict(r) for r in self.con.execute(
            "SELECT p.id AS player_id, p.link, p.language, p.server_id, p.episode_id, "
            "s.name AS server_name, s.embed, s.position "
            "FROM PlayerEntity p LEFT JOIN ServerEntity s ON s.id = p.server_id "
            "WHERE p.episode_id = ? ORDER BY s.position ASC, p.id ASC",
            (int(episode_id),)).fetchall()]

    def languages_for_episode(self, episode_id):
        rows = self.con.execute(
            "SELECT DISTINCT language FROM PlayerEntity WHERE episode_id = ? "
            "ORDER BY language ASC", (int(episode_id),)).fetchall()
        return [r[0] for r in rows]

    def build_player_url(self, player):
        """URL de página real de reproducción (igual que la app): los links
        del catálogo van revertidos; la página es {embed}e/{link invertido}."""
        url = (player.get("link") or "").strip()
        if url.startswith("http"):
            return url
        embed = (player.get("embed") or "").strip()
        if embed:
            if embed.endswith("/"):
                return embed + "e/" + url[::-1]
            return embed + "/e/" + url[::-1]
        return url

    def genres(self):
        counts = {}
        rows = self.con.execute(
            "SELECT genres FROM AnimeEntity WHERE genres IS NOT NULL AND genres != ''"
        ).fetchall()
        for (g,) in rows:
            for slug in g.split(","):
                slug = slug.strip()
                if slug:
                    counts[slug] = counts.get(slug, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    # ------------------------------------------------------------------ #
    # listas de usuario (locales: user_id 0 = invitado)
    # ------------------------------------------------------------------ #
    def _user_list(self, table, user_id, limit):
        return self._rows(
            f"SELECT a.* FROM {table} l INNER JOIN AnimeEntity a ON a.id = l.anime_id "
            f"WHERE l.user_id = ? AND EXISTS (SELECT 1 FROM EpisodeEntity e WHERE e.anime_id = a.id) "
            f"ORDER BY l.updated_at DESC LIMIT ?", (int(user_id), limit))

    def favorites(self, user_id, limit=200):
        return self._user_list("UserFavorite", user_id, limit)

    def watching(self, user_id, limit=200):
        return self._user_list("UserWatching", user_id, limit)

    def watchlater(self, user_id, limit=200):
        return self._user_list("UserWatchLater", user_id, limit)

    def viewed(self, user_id, limit=200):
        return self._user_list("UserView", user_id, limit)

    def has_list(self, table, user_id, anime_id):
        return bool(self._scalar(
            f"SELECT COUNT(*) FROM {table} WHERE anime_id = ? AND user_id = ?",
            (int(anime_id), int(user_id))))

    def add_list(self, table, user_id, anime_id):
        self.con.execute(
            f"INSERT OR REPLACE INTO {table} (anime_id, user_id, updated_at) VALUES (?, ?, ?)",
            (int(anime_id), int(user_id), _now()))
        self.con.commit()

    def remove_list(self, table, user_id, anime_id):
        self.con.execute(
            f"DELETE FROM {table} WHERE anime_id = ? AND user_id = ?",
            (int(anime_id), int(user_id)))
        self.con.commit()

    # ------------------------------------------------------------------ #
    # progreso y episodios vistos
    # ------------------------------------------------------------------ #
    def progress_rows(self, user_id, limit=80):
        return self._rows(
            "SELECT p.anime_id, p.episode_id, p.episode_number, p.position_ms, "
            "p.duration_ms, p.completed, p.updated_at, "
            "a.name AS anime_name, a.type AS anime_type "
            "FROM WatchProgressEntity p INNER JOIN AnimeEntity a ON a.id = p.anime_id "
            "WHERE p.user_id = ? ORDER BY p.updated_at DESC LIMIT ?", (int(user_id), limit))

    def recent_episode_of_anime(self, user_id, anime_id):
        row = self.con.execute(
            "SELECT * FROM WatchProgressEntity WHERE user_id = ? AND anime_id = ? "
            "ORDER BY updated_at DESC LIMIT 1", (int(user_id), int(anime_id))).fetchone()
        return dict(row) if row else None

    def viewed_episodes(self, user_id, anime_id):
        rows = self.con.execute(
            "SELECT episode_id FROM ViewedEpisodeEntity WHERE user_id = ? AND anime_id = ?",
            (int(user_id), int(anime_id))).fetchall()
        return {int(r[0]) for r in rows}

    def progress_for_anime(self, user_id, anime_id):
        rows = self.con.execute(
            "SELECT episode_id, episode_number, completed FROM WatchProgressEntity "
            "WHERE user_id = ? AND anime_id = ?", (int(user_id), int(anime_id))).fetchall()
        return {int(r[0]): dict(ep=int(r[1]), completed=bool(r[2])) for r in rows}

    def mark_episode_viewed(self, user_id, episode_id, anime_id):
        self.con.execute(
            "INSERT OR REPLACE INTO ViewedEpisodeEntity "
            "(episode_id, user_id, anime_id, viewed_at) VALUES (?, ?, ?, ?)",
            (int(episode_id), int(user_id), int(anime_id), _now()))
        self.con.commit()

    def unmark_episode_viewed(self, user_id, episode_id):
        self.con.execute(
            "DELETE FROM ViewedEpisodeEntity WHERE episode_id = ? AND user_id = ?",
            (int(episode_id), int(user_id)))
        self.con.commit()

    def put_progress(self, user_id, episode_id, anime_id, episode_number,
                     position_ms=0, duration_ms=0, completed=True):
        self.con.execute(
            "INSERT OR REPLACE INTO WatchProgressEntity "
            "(episode_id, user_id, anime_id, episode_number, position_ms, duration_ms, "
            "updated_at, completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(episode_id), int(user_id), int(anime_id), int(episode_number),
             int(position_ms), int(duration_ms), _now(), 1 if completed else 0))
        self.con.commit()

    def delete_progress_anime(self, user_id, anime_id):
        self.con.execute(
            "DELETE FROM WatchProgressEntity WHERE user_id = ? AND anime_id = ?",
            (int(user_id), int(anime_id)))
        self.con.commit()

    def is_episode_viewed(self, user_id, episode_id):
        return bool(self._scalar(
            "SELECT COUNT(*) FROM ViewedEpisodeEntity WHERE episode_id = ? AND user_id = ?",
            (int(episode_id), int(user_id))))

    # ------------------------------------------------------------------ #
    # historial de búsqueda
    # ------------------------------------------------------------------ #
    def push_search(self, anime_id):
        self.con.execute(
            "INSERT OR REPLACE INTO SearchedAnimeEntity (anime_id, visited_at) VALUES (?, ?)",
            (int(anime_id), _now()))
        self.con.commit()

    def search_history(self, limit=20):
        return self._rows(
            "SELECT s.anime_id AS id FROM SearchedAnimeEntity s "
            "ORDER BY s.visited_at DESC LIMIT ?", (int(limit),))

    # ------------------------------------------------------------------ #
    # sincronización de listas del servidor (solo descarga)
    # ------------------------------------------------------------------ #
    def merge_server_lists(self, user_id, lists):
        """Aplica GET /auth/lists/all a las tablas locales del usuario."""
        self.con.execute("DELETE FROM UserFavorite WHERE user_id = ?", (user_id,))
        self.con.execute("DELETE FROM UserWatching WHERE user_id = ?", (user_id,))
        self.con.execute("DELETE FROM UserWatchLater WHERE user_id = ?", (user_id,))
        self.con.execute("DELETE FROM UserView WHERE user_id = ?", (user_id,))
        self.con.execute("DELETE FROM WatchProgressEntity WHERE user_id = ?", (user_id,))
        data = ((lists.get("favorites"), "UserFavorite"),
                (lists.get("watching"), "UserWatching"),
                (lists.get("watchlater"), "UserWatchLater"),
                (lists.get("views"), "UserView"))
        for rows, table in data:
            for it in rows:
                self.con.execute(
                    f"INSERT OR IGNORE INTO {table} (anime_id, user_id, updated_at) "
                    "VALUES (?, ?, ?)", (it["id"], user_id, it["ua"] or _now()))
        for it in lists.get("progress", []):
            if it["eid"] and it["aid"]:
                self.put_progress(user_id, it["eid"], it["aid"], it["en"],
                                  it["pos"], it["dur"], it["c"])
        self.con.commit()

    def local_list_ids(self, user_id):
        out = {}
        for table in ("UserFavorite", "UserWatching", "UserWatchLater", "UserView"):
            rows = self.con.execute(f"SELECT anime_id FROM {table} WHERE user_id = ?",
                                    (user_id,)).fetchall()
            out[table] = {int(r[0]) for r in rows}
        return out
