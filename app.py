#!/usr/bin/env python3
"""Run the local calendar: python app.py --port 8765."""
from __future__ import annotations

import argparse
import datetime as dt
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.parse

from acg_calendar import __version__
from acg_calendar.collector import Collector
from acg_calendar.ical import calendar_bytes
from acg_calendar.parser import ROOT, iso, load_rules, resolve_versions, stamp
from acg_calendar.reminders import ReminderEngine, native_available
from acg_calendar.storage import Store
from acg_calendar.rewards import summarize_rewards


class Application:
    def __init__(self, data_dir: Path, *, auto_sync=True, desktop=True):
        self.data_dir = data_dir
        self.store = Store(data_dir / "calendar.sqlite3")
        self.collector = Collector(self.store, data_dir)
        self.collector.seed()
        self.csrf = secrets.token_urlsafe(32)
        self.reminders = ReminderEngine(self.store)
        self.stop = threading.Event()
        self.auto_sync = auto_sync
        self.desktop = desktop

    def versions(self):
        versions = self.store.versions()
        reward_rules = load_rules().get("rewards")
        for version in versions:
            # Rebuild legacy local snapshots once from their retained official text.
            if "reward_mentions" not in version:
                source = self.data_dir / "sources" / (version["id"] + ".txt")
                if not source.is_file() and version["source"].get("cid") == "6094":
                    source = ROOT / "samples/endfield-cn-6094.txt"
                if source.is_file():
                    try:
                        reparsed = self.collector.parser.parse(source.read_text(encoding="utf-8"), cid=version["source"]["cid"], source_url=version["source"]["url"], title=version["source"]["title"], published_at=version["source"].get("published_at"))
                    except (OSError, ValueError):
                        reparsed = None
                    if reparsed:
                        by_id = {e["id"]: e for e in reparsed["events"]}
                        version["reward_mentions"] = reparsed["reward_mentions"]
                        for event in version["events"]:
                            event["source"]["reward_lines"] = by_id.get(event["id"], {}).get("source", {}).get("reward_lines", [])
            summarize_rewards(version, reward_rules)
        return resolve_versions(versions, self.store.state())

    def state(self):
        versions = sorted(self.versions(), key=lambda v: v["maintenance"].get("start") or "", reverse=True)
        return {
            "app_version": __version__, "server_time": iso(dt.datetime.now(dt.timezone.utc)),
            "csrf_token": self.csrf, "settings": self.store.settings(),
            "versions": [{k: v for k, v in version.items() if k != "events"} for version in versions],
            "events": [{**event, "version_name": version["name"]} for version in versions for event in version["events"]],
            "sync": self.store.get_meta("sync", {}), "notifications": self.store.notifications(),
            "native_available": self.desktop and native_available(),
            "desktop_delivery": self.store.get_meta("desktop_delivery"),
            "briefing_previews": self.store.get_meta("briefing_previews", []),
        }

    def start(self):
        def worker():
            if self.auto_sync:
                self.collector.start(force=True)
            while not self.stop.is_set():
                try:
                    self.reminders.tick()
                    if self.auto_sync:
                        now = dt.datetime.now(dt.timezone.utc)
                        sync = self.store.get_meta("sync", {})
                        last = stamp(sync.get("last_attempt"))
                        interval = self.store.settings()["sync_interval_minutes"] * 60
                        if not last or (now - last).total_seconds() >= interval:
                            self.collector.start()
                except Exception as error:
                    print("[background]", str(error), flush=True)
                self.stop.wait(15)
        threading.Thread(target=worker, daemon=True, name="calendar-scheduler").start()

    def update_event(self, event_id, payload):
        event = next((e for v in self.versions() for e in v["events"] if e["id"] == event_id), None)
        if not event:
            raise LookupError("活动不存在")
        allowed = {"completed", "ignored", "notes", "reminder_minutes", "start_override", "end_override", "clear_overrides"}
        if set(payload) - allowed:
            raise ValueError("不支持的活动字段")
        patch, remove = {}, []
        for flag in ("completed", "ignored"):
            if flag in payload:
                if type(payload[flag]) is not bool:
                    raise ValueError("状态字段必须为布尔值")
                patch[flag] = payload[flag]
        if "notes" in payload:
            if not isinstance(payload["notes"], str) or len(payload["notes"]) > 2000:
                raise ValueError("备注最多 2000 字")
            patch["notes"] = payload["notes"]
        if "reminder_minutes" in payload:
            offsets = payload["reminder_minutes"]
            if offsets is not None and (not isinstance(offsets, list) or len(offsets) > 5 or any(type(n) is not int or not 1 <= n <= 10080 for n in offsets)):
                raise ValueError("提醒提前量需为 1–10080 分钟，最多 5 项")
            patch["reminder_minutes"] = sorted(set(offsets), reverse=True) if offsets is not None else None
        if payload.get("clear_overrides"):
            remove.extend(["start_override", "end_override"])
        for role in ("start", "end"):
            key = role + "_override"
            if key in payload:
                if payload[key] is None:
                    remove.append(key)
                else:
                    if not isinstance(payload[key], str):
                        raise ValueError("校正时间必须为包含时区的 ISO 日期字符串")
                    patch[key] = iso(stamp(payload[key]))
        effective = {role: patch.get(role + "_override") or event[role]["at"] for role in ("start", "end")}
        if not payload.get("clear_overrides") and effective["start"] and effective["end"] and stamp(effective["end"]) <= stamp(effective["start"]):
            raise ValueError("结束时间必须晚于开始时间")
        self.store.update_state(event_id, patch, remove)
        self.reminders.tick()

    def update_settings(self, patch):
        allowed = {"timezone", "view", "reminders_enabled", "reminder_offsets", "native_notifications", "sync_interval_minutes", "hide_completed"}
        if set(patch) - allowed:
            raise ValueError("不支持的设置字段")
        for name in ("reminders_enabled", "native_notifications", "hide_completed"):
            if name in patch and type(patch[name]) is not bool:
                raise ValueError("开关字段必须为布尔值")
        if patch.get("native_notifications") and not (self.desktop and native_available()):
            raise ValueError("此运行环境暂不支持本机系统通知")
        if "timezone" in patch and (not isinstance(patch["timezone"], str) or not re.fullmatch(r"auto|UTC|[A-Za-z_+-]+(?:/[A-Za-z_+0-9-]+)+", patch["timezone"]) or len(patch["timezone"]) > 100):
            raise ValueError("时区格式无效")
        if "view" in patch and patch["view"] not in {"timeline", "month", "list"}:
            raise ValueError("不支持的日历视图")
        if "sync_interval_minutes" in patch and (type(patch["sync_interval_minutes"]) is not int or not 10 <= patch["sync_interval_minutes"] <= 1440):
            raise ValueError("同步间隔需为 10–1440 分钟")
        if "reminder_offsets" in patch:
            offsets = patch["reminder_offsets"]
            if not isinstance(offsets, list) or len(offsets) > 5 or any(type(n) is not int or not 1 <= n <= 10080 for n in offsets):
                raise ValueError("提醒提前量无效")
            patch["reminder_offsets"] = sorted(set(offsets), reverse=True)
        self.store.update_settings(patch)
        self.reminders.tick()


def handler_for(application):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ACGCalendar/" + __version__

        def log_message(self, message, *args):
            if args and str(args[1] if len(args) > 1 else "") != "200":
                super().log_message(message, *args)

        def valid_host(self):
            host = self.headers.get("Host", "").split(":")[0].lower()
            return host in {"127.0.0.1", "localhost"}

        def send(self, data, status=200, content_type="application/json; charset=utf-8", filename=None, cache_control="no-store"):
            encoded = json.dumps(data, ensure_ascii=False).encode("utf-8") if not isinstance(data, bytes) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if not self.valid_host():
                return self.send({"error": "仅允许本机访问"}, 403)
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/api/state":
                    return self.send(application.state())
                if path == "/api/rules":
                    return self.send(load_rules())
                if path == "/api/export":
                    return self.send({"schema_version": 1, "exported_at": iso(dt.datetime.now(dt.timezone.utc)), "rules": load_rules(), "versions": application.versions()}, filename="endfield-calendar.json")
                if path == "/api/calendar.ics":
                    return self.send(calendar_bytes(application.versions(), application.store.settings()), content_type="text/calendar; charset=utf-8", filename="playtime-endfield.ics")
                if path == "/api/health":
                    return self.send({"ok": True, "version": __version__})
                if path.startswith("/api/artwork/"):
                    image = application.collector.artwork.path(path.removeprefix("/api/artwork/"))
                    if image:
                        content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[image.suffix]
                        return self.send(image.read_bytes(), content_type=content_type, cache_control="private, max-age=604800, immutable")
                    return self.send({"error": "图片尚未缓存"}, 404)
                assets = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg", "/assets/oroberyl.png": "assets/oroberyl.png"}
                if path not in assets:
                    return self.send({"error": "页面不存在"}, 404)
                file = ROOT / "web" / assets[path]
                if file.suffix == ".png":
                    return self.send(file.read_bytes(), content_type="image/png", cache_control="private, max-age=86400")
                content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[file.suffix]
                return self.send(file.read_bytes(), content_type=content_type + "; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            self.mutate()

        def do_PATCH(self):
            self.mutate()

        def mutate(self):
            if not self.valid_host():
                return self.send({"error": "仅允许本机访问"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + self.headers.get("Host", "")}:
                return self.send({"error": "请求来源不匹配"}, 403)
            if not secrets.compare_digest(self.headers.get("X-Calendar-Token", ""), application.csrf):
                return self.send({"error": "会话已变化，请刷新页面"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 16000:
                    return self.send({"error": "请求过大"}, 413)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("请求应为 JSON 对象")
                path = urllib.parse.urlsplit(self.path).path
                if path == "/api/sync":
                    application.collector.start()
                elif path == "/api/settings":
                    application.update_settings(payload)
                elif path == "/api/notifications/test":
                    application.reminders.test()
                elif path == "/api/notifications/read":
                    application.store.read_notifications()
                elif match := re.fullmatch(r"/api/events/([a-zA-Z0-9_-]+)", path):
                    application.update_event(match.group(1), payload)
                elif match := re.fullmatch(r"/api/versions/([a-zA-Z0-9_-]+)/maintenance", path):
                    if set(payload) != {"actual_end"} or (payload["actual_end"] is not None and not isinstance(payload["actual_end"], str)):
                        raise ValueError("请明确提供 actual_end 时间或 null")
                    version = next((v for v in application.versions() if v["id"] == match.group(1)), None)
                    if not version:
                        raise LookupError("版本不存在")
                    actual = iso(stamp(payload.get("actual_end")))
                    if actual and version["maintenance"]["start"] and stamp(actual) <= stamp(version["maintenance"]["start"]):
                        raise ValueError("实际开服时间需晚于维护开始")
                    if actual and stamp(actual) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1):
                        raise ValueError("实际开服时间不能是尚未到来的时刻")
                    application.store.update_state("version:" + version["id"], {"actual_end": actual})
                    application.reminders.tick()
                else:
                    return self.send({"error": "接口不存在"}, 404)
                return self.send(application.state())
            except LookupError as error:
                return self.send({"error": str(error)}, 404)
            except (ValueError, TypeError, KeyError) as error:
                return self.send({"error": str(error)}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--no-sync", action="store_true", help="Use cached/snapshot sources without network requests")
    parser.add_argument("--no-desktop", action="store_true", help="Disable native desktop delivery")
    args = parser.parse_args()
    if args.no_desktop:
        os.environ["ACG_DISABLE_DESKTOP"] = "1"
    application = Application(args.data_dir, auto_sync=not args.no_sync, desktop=not args.no_desktop)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(application))
    except OSError as error:
        parser.exit(1, f"无法使用端口 {args.port}：{error}\n可以尝试 --port 8766\n")
    server.daemon_threads = True
    application.start()
    print(f"\n  游时 · 终末地日历 {__version__}\n  Local: http://127.0.0.1:{args.port}\n  Data:  {args.data_dir.resolve()}\n  按 Ctrl+C 停止。\n", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        application.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
