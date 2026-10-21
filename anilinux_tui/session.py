# -*- coding: utf-8 -*-
"""Sesión de usuario: token + datos de perfil, persistidos con permisos 0600."""
import json
import os

from . import config


class Session:
    def __init__(self):
        self.token = None
        self.user = None
        self.load()

    # ------------------------------------------------------------------ #
    @property
    def is_logged(self):
        return bool(self.token)

    @property
    def user_id(self):
        if self.is_logged and self.user:
            return int(self.user.get("id") or 0)
        return 0

    @property
    def label(self):
        if not self.is_logged:
            return "Invitado"
        name = (self.user or {}).get("name") or (self.user or {}).get("email") or "Sin nombre"
        return name

    # ------------------------------------------------------------------ #
    def load(self):
        try:
            with open(config.SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.token = data.get("token")
            self.user = data.get("user") or {}
        except Exception:
            self.token = None
            self.user = {}

    def save(self):
        config.ensure_dirs()
        payload = {"token": self.token, "user": self.user or {}}
        with open(config.SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        try:
            os.chmod(config.SESSION_PATH, 0o600)
        except OSError:
            pass

    def clear(self):
        self.token = None
        self.user = {}
        try:
            os.remove(config.SESSION_PATH)
        except OSError:
            pass

    def update_user(self, user):
        self.user = user or {}
        self.save()
