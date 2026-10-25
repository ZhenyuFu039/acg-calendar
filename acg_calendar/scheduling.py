"""Persisted, staggered source checks. Event clocks never depend on this timer."""
import datetime as dt
import json
import random
import time


class SyncSchedule:
    def __init__(self, store, *, clock=time.time, uniform=random.uniform):
        self.store, self.clock, self.uniform = store, clock, uniform
        # Upgrade the former 1/3/6-hour presets once, preserving unrelated settings.
        with store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not self._get(conn, 'conservative-sync-v1'):
                saved = self._get(conn, 'settings') or {}
                if saved.get('sync_interval_minutes', 1440) < 1080:
                    saved['sync_interval_minutes'] = 1440
                    self._put(conn, 'settings', saved)
                self._put(conn, 'conservative-sync-v1', True)

    @staticmethod
    def _get(conn, key):
        row = conn.execute('SELECT payload FROM meta WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _put(conn, key, value):
        conn.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (key, json.dumps(value)))

    @staticmethod
    def _timestamp(value):
        try:
            return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else 0
        except (ValueError, TypeError, AttributeError):
            return 0

    def _prepare(self, conn, key, minutes, startup=False):
        now = self.clock()
        observed = self._get(conn, key) or {}
        signature = [observed.get(k) for k in ('last_attempt', 'last_success', 'checked_at')]
        identity = 'auto-schedule:' + key
        saved = self._get(conn, identity)
        if not saved or saved['signature'] != signature or saved['minutes'] != minutes:
            anchor = max(self._timestamp(t) for t in signature)
            due = anchor + minutes * 60 + self.uniform(0, 7200) if anchor else now + self.uniform(60, 600)
            if due < now:
                due = now + self.uniform(60, 600)
            saved = {'signature': signature, 'minutes': minutes, 'next_at': due}
            self._put(conn, identity, saved)
        elif startup and saved['next_at'] < now:
            saved['next_at'] = now + self.uniform(60, 600)
            self._put(conn, identity, saved)
        return saved

    def prepare(self, key, minutes, *, startup=False):
        with self.store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return dict(self._prepare(conn, key, minutes, startup))

    def claim(self, key, minutes):
        # Two services sharing the same database cannot claim the same check.
        with self.store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._prepare(conn, key, minutes)
            if row['next_at'] > self.clock():
                return False
            row['next_at'] = self.clock() + minutes * 60 + self.uniform(0, 7200)
            self._put(conn, 'auto-schedule:' + key, row)
            return True

    def snapshot(self, key, minutes):
        row = self.prepare(key, minutes)
        return {'next_check_at': dt.datetime.fromtimestamp(row['next_at'], dt.timezone.utc).isoformat(), 'interval_minutes': minutes, 'jitter_max_minutes': 120}
