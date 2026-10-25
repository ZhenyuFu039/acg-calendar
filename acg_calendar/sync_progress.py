"""Stage-by-stage sync status shared by every collector (F12).

Collectors report named stages instead of percentages, publish the calendar as soon as
activities and dates validate, and fill images afterwards. Everything lives in the same
`meta[sync_key]` record the UI already reads; unknown fields are ignored by old clients.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt

from .network import reporting
from .parser import iso

STAGES = {
    "fetch": "读取公告与来源",
    "parse": "解析活动与日期",
    "images": "补齐活动图片",
}


def _now():
    return iso(dt.datetime.now(dt.timezone.utc))


def calendar_changes(old_versions, new_versions):
    """Added / updated / removed activities by stable ID; personal state is not involved."""
    def index(versions):
        return {e["id"]: e for v in versions for e in v.get("events", [])}
    def shape(e):
        # Phases (e.g. a challenge closing early) are deadlines too; images and fetch times are not.
        phases = tuple(sorted((p.get("title") or "", p.get("at") or "", p.get("state") or "") for p in e.get("phases") or []))
        return (e.get("title"), (e.get("start") or {}).get("at"), (e.get("start") or {}).get("state"),
                (e.get("end") or {}).get("at"), (e.get("end") or {}).get("state"), e.get("category"), phases)
    old, new = index(old_versions), index(new_versions)
    added = sorted(new.keys() - old.keys())
    # added_ids lets the page explain when a new activity is hidden by the current filters (F16).
    return {"added": len(added), "removed": len(old.keys() - new.keys()),
            "updated": sum(shape(new[k]) != shape(old[k]) for k in new.keys() & old.keys()), "total": len(new),
            "added_ids": added[:200]}


def artwork_count(versions):
    return sum(bool(e.get("artwork") or e.get("image")) for v in versions for e in v.get("events", []))


class SyncProgress:
    def __init__(self, store, key):
        self.store, self.key = store, key
        self.result, self.saved_at = {}, None

    def _write(self, **fields):
        self.store.set_meta(self.key, {**self.store.get_meta(self.key, {}), **fields})

    def stage(self, name, detail=None):
        self._write(running=True, stage={"name": name, "label": STAGES[name], "detail": detail, "since": _now()}, waiting=None)

    def wait(self, site, seconds):
        """Called by the network layer when a request has to wait for the per-site interval."""
        self._write(waiting={"site": site, "seconds": max(1, round(seconds)), "reason": "遵守站点请求间隔", "since": _now()})

    @contextmanager
    def active(self):
        with reporting(self.wait):
            yield self

    def calendar_saved(self, old_versions, new_versions):
        """Record the published calendar before images, so the UI can show it right away."""
        self.result = {"calendar": calendar_changes(old_versions, new_versions), "images": {"ready": artwork_count(new_versions)}}
        self.saved_at = _now()
        self._write(result=self.result, calendar_saved_at=self.saved_at)

    def images_done(self, versions, failed=0, notes=()):
        """failed counts image files that could not be loaded; notes name image sources that were
        unavailable when the number of affected images cannot be known."""
        before = self.result.get("images", {}).get("ready", 0)
        ready = artwork_count(versions)
        self.result["images"] = {"ready": ready, "added": max(0, ready - before), "failed": failed, "notes": list(notes)}
        self._write(result=self.result)

    def finish(self, fields):
        """Final status: the collector's own fields plus stage cleanup and this round's result."""
        return {**fields, "stage": None, "waiting": None, "result": self.result, "calendar_saved_at": self.saved_at}
