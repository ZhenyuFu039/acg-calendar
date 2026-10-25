"""Small SQLite store; downloaded source and user corrections stay separate."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import json
from pathlib import Path
import sqlite3
import threading

DEFAULT_SETTINGS = {
    "active_game": "endfield-cn",
    "timezone": "auto", "view": "timeline", "reminders_enabled": True,
    "reminder_offsets": [1440, 60], "native_notifications": False,
    "sync_interval_minutes": 1440, "hide_completed": False,
    # all: every installed game (also future ones); follow: the current game combination; custom: listed games.
    # games is null until a custom list is first saved, so an explicitly empty list stays empty.
    "reminder_scope": {"mode": "all", "games": None},
    # Plugin folders the user turned off; applied when the program next starts (F15).
    "disabled_plugins": [],
}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS local_state (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS personal_plans (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, event_id TEXT, created_at TEXT NOT NULL,
                    payload TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0
                );
            """)

    @contextmanager
    def connection(self):
        with self.lock:
            conn = sqlite3.connect(self.path, timeout=10)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def versions(self):
        with self.connection() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT payload FROM versions ORDER BY id")]

    def save_version(self, version):
        with self.connection() as conn:
            conn.execute("INSERT INTO versions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (version["id"], json.dumps(version, ensure_ascii=False)))

    def state(self):
        with self.connection() as conn:
            return {row[0]: json.loads(row[1]) for row in conn.execute("SELECT id,payload FROM local_state")}

    def replace_game_versions(self, game, versions):
        """Replace only a provider's complete snapshot, in one transaction.

        Personal state is never removed, even if a source temporarily omits an ID.
        """
        if any(v.get("game") != game for v in versions):
            raise ValueError("适配器只能写入自己的游戏")
        with self.connection() as conn:
            old = [row[0] for row in conn.execute("SELECT id,payload FROM versions") if json.loads(row[1]).get("game") == game]
            for key in old:
                conn.execute("DELETE FROM versions WHERE id=?", (key,))
            for version in versions:
                existing = conn.execute("SELECT payload FROM versions WHERE id=?", (version["id"],)).fetchone()
                if existing and json.loads(existing[0]).get("game") != game:
                    raise ValueError("插件版本 ID 与其他游戏冲突")
                conn.execute("INSERT INTO versions VALUES (?,?)", (version["id"], json.dumps(version, ensure_ascii=False)))

    def plans(self):
        with self.connection() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT payload FROM personal_plans ORDER BY id")]

    def save_plan(self, plan):
        with self.connection() as conn:
            conn.execute("INSERT INTO personal_plans VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (plan["id"], json.dumps(plan, ensure_ascii=False)))

    def delete_plan(self, key):
        with self.connection() as conn:
            if not conn.execute("DELETE FROM personal_plans WHERE id=?", (key,)).rowcount:
                raise LookupError("个人计划不存在")

    def update_state(self, key, patch, remove=()):
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM local_state WHERE id=?", (key,)).fetchone()
            value = json.loads(row[0]) if row else {}
            value.update(patch)
            for field in remove:
                value.pop(field, None)
            conn.execute("INSERT INTO local_state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (key, json.dumps(value, ensure_ascii=False)))
            return value

    def get_meta(self, key, default=None):
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM meta WHERE id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connection() as conn:
            conn.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return {**DEFAULT_SETTINGS, **self.get_meta("settings", {})}

    def update_settings(self, patch):
        settings = {**self.settings(), **patch}
        self.set_meta("settings", settings)
        return settings

    def add_notification(self, key, event_id, payload, now=None):
        created = now or dt.datetime.now(dt.timezone.utc).isoformat()
        with self.connection() as conn:
            cursor = conn.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,0)", (key, event_id, created, json.dumps(payload, ensure_ascii=False)))
            return cursor.rowcount == 1

    def notifications(self):
        with self.connection() as conn:
            return [{"id": row[0], "event_id": row[1], "created_at": row[2], **json.loads(row[3]), "read": bool(row[4])} for row in conn.execute("SELECT id,event_id,created_at,payload,is_read FROM notifications ORDER BY created_at DESC LIMIT 100")]

    def read_notifications(self):
        with self.connection() as conn:
            conn.execute("UPDATE notifications SET is_read=1")
