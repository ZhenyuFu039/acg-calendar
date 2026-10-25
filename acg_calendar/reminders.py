"""Persistent deadline notifications with optional native desktop delivery."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading

from .parser import iso, resolve_versions, stamp
from .games import game_name


def check_event_offsets(offsets):
    """Per-event reminder minutes: None follows the global setting."""
    if offsets is not None and (not isinstance(offsets, list) or len(offsets) > 5 or any(type(n) is not int or not 1 <= n <= 10080 for n in offsets)):
        raise ValueError("提醒提前量需为 1–10080 分钟，最多 5 项")
    return sorted(set(offsets), reverse=True) if offsets is not None else None


def native_available():
    if os.environ.get("ACG_DISABLE_DESKTOP") == "1":
        return False
    return sys.platform == "win32" or bool(shutil.which("osascript") if sys.platform == "darwin" else shutil.which("notify-send"))


def native_notify(title, body):
    """Argument arrays / stdin keep announcement strings out of shell code."""
    if not native_available():
        return False
    if sys.platform == "win32":
        code = "$p=[Console]::In.ReadToEnd()|ConvertFrom-Json;Add-Type -AssemblyName System.Windows.Forms;Add-Type -AssemblyName System.Drawing;$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;$n.ShowBalloonTip(7000,$p.title,$p.body,[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep -Seconds 8;$n.Dispose()"
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", code]
        process = subprocess.run(command, input=json.dumps({"title": title, "body": body}, ensure_ascii=True), encoding="ascii", capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    elif sys.platform == "darwin":
        command = ["osascript", "-e", "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run", title, body]
        process = subprocess.run(command, capture_output=True, timeout=10)
    else:
        process = subprocess.run(["notify-send", "--app-name=游时", title, body], capture_output=True, timeout=10)
    return process.returncode == 0


class ReminderEngine:
    def __init__(self, store, sender=native_notify, *, game_names=None, enabled_games=None, scope_games=None):
        self.store = store
        self.sender = sender
        self.game_names = game_names or {}
        self.enabled_games = enabled_games
        # Callable returning the games chosen in the reminder settings, or None for all games.
        self.scope_games = scope_games
        self.delivery_lock = threading.Lock()

    def tick(self, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        settings = self.store.settings()
        if not settings["reminders_enabled"]:
            return []
        created = []
        chosen = self.scope_games() if self.scope_games else None
        for version in resolve_versions(self.store.versions(), self.store.state()):
            if self.enabled_games is not None and version.get("game") not in self.enabled_games:
                continue
            if chosen is not None and version.get("game") not in chosen:
                continue
            for event in version["events"]:
                if event["completed"] or event["ignored"] or event.get("invalid_interval") or event["time_kind"] == "point":
                    continue
                endpoint = event["end"]
                if not endpoint.get("at") or endpoint["state"] not in {"exact", "confirmed", "manual"}:
                    continue
                remaining = (stamp(endpoint["at"]) - now).total_seconds()
                offsets = event["reminder_minutes"] if event["reminder_minutes"] is not None else settings["reminder_offsets"]
                if not offsets:
                    continue
                eligible = [minutes for minutes in offsets if remaining > 0 and remaining <= minutes * 60]
                threshold = min(eligible) if eligible else (0 if -21600 <= remaining <= 0 else None)
                if threshold is None:
                    continue
                key = hashlib.sha256(f"{event['id']}|{endpoint['at']}|{threshold}".encode()).hexdigest()
                payload = {"title": event["title"], "game": version.get("game", "endfield-cn"), "game_name": self.game_names.get(version.get("game"), game_name(version.get("game", "endfield-cn"))), "message": "活动已到截止时间" if threshold == 0 else "活动即将结束", "end_at": endpoint["at"], "threshold_minutes": threshold, "kind": "deadline"}
                if self.store.add_notification(key, event["id"], payload, iso(now)):
                    created.append(payload)
        if created and settings["native_notifications"]:
            self.deliver(created)
        return created

    def deliver(self, notices):
        def run():
            with self.delivery_lock:
                title = "游时 · " + (notices[0]["message"] if len(notices) == 1 else f"{len(notices)} 项活动提醒")
                body = "\n".join((notice.get("game_name", "") + " · " if notice.get("game_name") else "") + notice["title"] for notice in notices[:4])
                try:
                    sent = self.sender(title, body)
                    self.store.set_meta("desktop_delivery", {"at": iso(dt.datetime.now(dt.timezone.utc)), "ok": sent, "message": "已交给系统通知服务" if sent else "系统通知不可用，请查看提醒收件箱"})
                except Exception as error:
                    self.store.set_meta("desktop_delivery", {"at": iso(dt.datetime.now(dt.timezone.utc)), "ok": False, "message": str(error)[:200]})
        threading.Thread(target=run, daemon=True, name="desktop-notification").start()

    def test(self):
        now = dt.datetime.now(dt.timezone.utc)
        payload = {"title": "提醒系统测试", "message": "这是一条测试提醒，真实活动日期没有改变。", "kind": "test", "end_at": None}
        self.store.add_notification("test-" + iso(now), None, payload, iso(now))
        if self.store.settings()["native_notifications"]:
            self.deliver([payload])
        return payload
