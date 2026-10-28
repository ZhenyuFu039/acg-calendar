#!/usr/bin/env python3
"""Run the local calendar: python app.py --port 8765."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
from acg_calendar import backup
from acg_calendar.artwork import ArtworkCache
from acg_calendar.ical import calendar_bytes
from acg_calendar.parser import ROOT, iso, resolve_versions, stamp
from acg_calendar.reminders import ReminderEngine, check_event_offsets, native_available
from acg_calendar.storage import DEFAULT_SETTINGS, Store
from acg_calendar.providers import create_registry
from acg_calendar.workspace import WorkspaceService, timeline, sort_events
from acg_calendar.network import NetworkManager, check_policy
from acg_calendar.scheduling import SyncSchedule
from acg_calendar.presentation import context, local_instant


def plugin_folder(name):
    """A single plugin folder name as found in data/plugins (any language, spaces allowed)."""
    if not isinstance(name, str) or not 1 <= len(name) <= 100 or name in {".", ".."} or name != name.strip() or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name):
        raise ValueError("停用插件列表无效")
    return name


SETTING_FIELDS = {"active_game", "timezone", "view", "reminders_enabled", "reminder_offsets", "native_notifications", "sync_interval_minutes", "hide_completed", "reminder_scope", "disabled_plugins"}


class Application:
    def __init__(self, data_dir: Path, *, auto_sync=True, desktop=True, registry=None):
        self.data_dir = data_dir
        self.store = Store(data_dir / "calendar.sqlite3")
        self.upgrade_backup = self.backup_before_upgrade()
        self.network = NetworkManager(self.store)
        self.store.network = self.network
        self.registry = registry or create_registry(self.store, data_dir)
        self.collectors = {key: p.collector for key, p in self.registry.providers.items()}
        # A new process has no sync in flight; clear stages left by one that exited mid-sync.
        for provider in self.registry.providers.values():
            status = self.store.get_meta(provider.sync_key) or {}
            if status.get("running"):
                self.store.set_meta(provider.sync_key, {**status, "running": False, "stage": None, "waiting": None, "message": "上次同步在程序退出时中断，保留已入库的日历"})
        for collector in self.collectors.values():
            collector.seed()
        # Compatibility for existing integrations; host services use the registry.
        self.collector = self.collectors.get("endfield-cn")
        self.arknights = self.collectors.get("arknights-cn")
        self.artwork = ArtworkCache(data_dir)
        self.workspace = WorkspaceService(self.store, self.registry)
        self.probes = {key: probe for p in self.registry.providers.values() for key, probe in getattr(p, "probes", {}).items()}
        self.csrf = secrets.token_urlsafe(32)
        self.reminders = ReminderEngine(self.store, game_names={g["id"]: g["name"] for g in self.registry.games}, enabled_games=self.registry.ids, scope_games=self.reminder_games)
        self.stop = threading.Event()
        self.auto_sync = auto_sync
        self.desktop = desktop
        self.schedule = SyncSchedule(self.store)
        for provider in self.registry.providers.values():
            self.schedule.prepare(provider.sync_key, self.store.settings()['sync_interval_minutes'], startup=True)
        for meta, _ in self.probes.values():
            self.schedule.prepare(meta, 1440, startup=True)

    def versions(self):
        versions = [v for v in self.store.versions() if v.get("game") in self.registry.ids]
        for version in versions:
            self.registry.providers[version["game"]].prepare(version)
        return resolve_versions(versions, self.store.state())

    def state(self):
        versions = sorted(self.versions(), key=lambda v: v["maintenance"].get("start") or "", reverse=True)
        workspace = self.workspace.get()
        events = [{**event, "version_name": version["name"], "game": version["game"], "game_name": self.registry.name(version["game"])} for version in versions for event in version["events"]]
        return {
            "games": self.registry.games,
            "provider_api_version": 1, "plugin_errors": self.registry.errors,
            "workspace": workspace, "plans": self.store.plans(),
            "presentation": context(versions, workspace['view']),
            "workspace_defaults": self.workspace.defaults(), "network": self.network.snapshot(),
            "event_order": [e["id"] for e in sort_events(events, workspace["view"])],
            "app_version": __version__, "server_time": iso(dt.datetime.now(dt.timezone.utc)),
            "csrf_token": self.csrf, "settings": self.store.settings(),
            "versions": [{k: v for k, v in version.items() if k != "events"} for version in versions],
            "events": events,
            "sync": self.store.get_meta("sync", {}), "notifications": self.store.notifications(),
            "native_available": self.desktop and native_available(),
            "desktop_delivery": self.store.get_meta("desktop_delivery"),
            "desktop_window_pending": self.store.get_meta("desktop_window_pending"),
            "briefing_previews": self.store.get_meta("briefing_previews", []),
            "sync_by_game": {g: self.store.get_meta(p.sync_key, {}) for g, p in self.registry.providers.items()},
            "sync_schedule": {g: {**self.schedule.snapshot(p.sync_key, self.store.settings()['sync_interval_minutes']), 'enabled': self.auto_sync} for g, p in self.registry.providers.items()},
            "source_checks": {key: self.store.get_meta(meta) for key, (meta, _) in self.probes.items()},
        }

    def scoped_versions(self, game):
        if game == "selected":
            selected = self.workspace.get()["view"]["selected_games"]
            return [v for v in self.versions() if v["game"] in selected]
        if game not in self.registry.ids | {"all"}:
            raise ValueError("不支持的游戏")
        return [v for v in self.versions() if game == "all" or v["game"] == game]

    def start(self):
        def worker():
            while not self.stop.is_set():
                try:
                    self.reminders.tick()
                    self.sync_due()
                except Exception as error:
                    print("[background]", str(error), flush=True)
                self.stop.wait(15)
        thread = threading.Thread(target=worker, daemon=True, name="calendar-scheduler")
        thread.start()
        return thread

    def sync_due(self):
        if not self.auto_sync or self.network.paused:
            return
        interval = self.store.settings()['sync_interval_minutes']
        for game, collector in self.collectors.items():
            if self.schedule.claim(self.registry.providers[game].sync_key, interval):
                collector.start()
        for key, (meta, probe) in self.probes.items():
            if self.store.get_meta(meta, {}).get('status') == 'device_validation_required':
                continue
            if self.schedule.claim(meta, 1440):
                threading.Thread(target=probe.check, daemon=True, name=key + '-availability').start()

    def update_event(self, event_id, payload):
        event = next((e for v in self.versions() for e in v["events"] if e["id"] == event_id), None)
        if not event:
            raise LookupError("活动不存在")
        allowed = {"completed", "ignored", "starred", "notes", "reminder_minutes", "start_override", "end_override", "clear_overrides",
                   "start_local", "end_local", "timezone", "fold_start", "fold_end"}
        if set(payload) - allowed:
            raise ValueError("不支持的活动字段")
        # Corrections may be typed in the display time zone; repeated or missing local times
        # get the same checks as personal plans. Only the endpoints sent here change.
        for role in ("start", "end"):
            if role + "_local" in payload:
                if role + "_override" in payload:
                    raise ValueError("当地时间和 UTC 校正不能同时填写")
                local = payload[role + "_local"]
                payload = {**payload, role + "_override": None if local is None else local_instant(local, payload.get("timezone"), payload.get("fold_" + role))}
        patch, remove = {}, []
        for flag in ("completed", "ignored", "starred"):
            if flag in payload:
                if type(payload[flag]) is not bool:
                    raise ValueError("状态字段必须为布尔值")
                patch[flag] = payload[flag]
        if "notes" in payload:
            if not isinstance(payload["notes"], str) or len(payload["notes"]) > 2000:
                raise ValueError("备注最多 2000 字")
            patch["notes"] = payload["notes"]
        if "reminder_minutes" in payload:
            patch["reminder_minutes"] = check_event_offsets(payload["reminder_minutes"])
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

    def open_plugin_directory(self):
        directory = (self.data_dir / 'plugins').resolve()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            if not hasattr(os, 'startfile'):
                raise ValueError('当前平台无法直接打开文件管理器，插件目录：' + str(directory))
            os.startfile(str(directory))
        except OSError as error:
            raise ValueError('无法打开插件目录：' + str(directory)) from error
        return {'directory': str(directory), 'opened': True}

    def backup_before_upgrade(self):
        """Copy personal data once whenever the program version changes, before any migration."""
        previous = self.store.get_meta("backup:app-version")
        if previous == __version__:
            return None
        result = None
        if backup.has_personal_data(backup.snapshot(self.store)):
            result = backup.create(self.store, self.data_dir, "upgrade", app_version=__version__, note=(previous or "未记录版本") + " → " + __version__)
        self.store.set_meta("backup:app-version", __version__)
        return result

    def create_backup(self):
        return backup.create(self.store, self.data_dir, "manual", app_version=__version__, plugins=self.registry.games)

    def restore_backup(self, payload):
        if set(payload) not in ({"name"}, {"backup"}):
            raise ValueError("请提供备份文件名 name，或完整的备份内容 backup")
        document = backup.read(self.data_dir, payload["name"]) if "name" in payload else payload["backup"]
        warnings = []
        try:
            contents, network = self.check_restore(document, warnings)
        except ValueError as error:
            raise ValueError("备份未通过检查，当前数据未改变：" + str(error)) from None
        window = backup.desktop_window(document.get("desktop_window"))
        pending = {"id": secrets.token_urlsafe(24), "preferences": window, "restored_at": iso(dt.datetime.now(dt.timezone.utc))} if window is not None else None
        # Every personal write goes through Store.lock; holding it for replace, check and
        # rollback means no edit from another window can land in between and be lost.
        # Nothing under the lock touches NetworkManager, whose condition is taken before Store.lock.
        with self.store.lock:
            safety = backup.create(self.store, self.data_dir, "before-restore", app_version=__version__, plugins=self.registry.games, note="恢复其他备份前的当前数据")
            current = backup.snapshot(self.store)
            previous_pending = self.store.get_meta("desktop_window_pending")
            backup.apply(self.store, contents, desktop_window_pending=pending)
            try:
                self.verify_personal_data()
            except Exception as error:
                backup.apply(self.store, current, desktop_window_pending=previous_pending)
                raise ValueError("恢复后的数据无法读取，已回到恢复前的状态：" + str(error)) from None
        # The desktop app rewrites its window file while running, so the backed-up window
        # preferences are handed over through the state and applied by the desktop itself.
        if window is not None:
            warnings.append("桌面窗口位置与外观会在桌面版下次刷新时应用")
        elif document.get("desktop_window") is not None:
            warnings.append("备份中的桌面窗口偏好格式无效，已跳过")
        if network is not None:
            try:
                self.network.update({"policy": network.get("policy", {}), "paused": network.get("paused", False)})
            except Exception as error:
                warnings.append("个人数据已恢复，但采集设置未能应用：" + str(error))
        try:
            self.reminders.tick()
        except Exception as error:
            warnings.append("个人数据已恢复，但提醒检查失败：" + str(error))
        return {"restored": backup.counts(contents), "from": document.get("created_at"), "safety_backup": safety["name"], "warnings": warnings}

    def acknowledge_window(self, payload):
        """Only consume the exact hand-over applied by the desktop, including across processes."""
        if not isinstance(payload, dict) or set(payload) not in ({"id"}, {"restored_at"}):
            raise ValueError("请提供桌面窗口恢复 ID")
        with self.store.connection() as conn:
            row = conn.execute("SELECT payload FROM meta WHERE id='desktop_window_pending'").fetchone()
            pending = json.loads(row[0]) if row else None
            # Older, already queued hand-overs had only a timestamp. They can still finish,
            # but a timestamp must never acknowledge a new hand-over that has a unique ID.
            key = "id" if pending and pending.get("id") else "restored_at"
            if not pending or set(payload) != {key} or not isinstance(payload[key], str) or not payload[key] or pending.get(key) != payload[key]:
                raise ValueError("没有匹配的待应用桌面窗口偏好")
            if not conn.execute("DELETE FROM meta WHERE id='desktop_window_pending' AND payload=?", (row[0],)).rowcount:
                raise ValueError("桌面窗口偏好已被新的恢复替换")

    def game_records(self, payload):
        """Personal records of one game: marks on its activities and versions, and plans tied to it.
        Uses the stored snapshots, so a disabled or removed plugin's game can still be cleared."""
        if not isinstance(payload, dict) or set(payload) != {"game"} or not isinstance(payload["game"], str):
            raise ValueError("请提供游戏 ID game")
        game = payload["game"]
        versions = [v for v in self.store.versions() if v.get("game") == game]
        if not versions:
            raise LookupError("没有这个游戏的日历记录")
        keys = {e["id"] for v in versions for e in v.get("events", [])} | {"version:" + v["id"] for v in versions}
        state = {k: v for k, v in self.store.state().items() if k in keys}
        plans = [p for p in self.store.plans() if p.get("game_id") == game]
        name = next((g["name"] for g in self.registry.games if g["id"] == game), game)
        return game, name, state, plans

    def preview_clear(self, payload):
        game, name, state, plans = self.game_records(payload)
        return {"game": game, "name": name, "marks": len(state), "starred": sum(bool(s.get("starred")) for s in state.values()),
                "completed": sum(bool(s.get("completed")) for s in state.values()), "notes": sum(bool(s.get("notes")) for s in state.values()),
                "corrections": sum(bool(s.get("start_override") or s.get("end_override") or s.get("actual_end")) for s in state.values()),
                "plans": len(plans)}

    def clear_game_records(self, payload):
        """Delete one game's personal records after an automatic backup; nothing else changes."""
        with self.store.lock:
            game, name, state, plans = self.game_records(payload)
            summary = self.preview_clear(payload)
            safety = backup.create(self.store, self.data_dir, "before-clear", app_version=__version__, plugins=self.registry.games, note="清除「" + name + "」个人记录之前")
            with self.store.connection() as conn:
                conn.executemany("DELETE FROM local_state WHERE id=?", [(k,) for k in state])
                conn.executemany("DELETE FROM personal_plans WHERE id=?", [(p["id"],) for p in plans])
        self.reminders.tick()
        return {**summary, "safety_backup": safety["name"]}

    def preview_restore(self, payload):
        """What a restore would change, from the same checks as restore_backup; nothing is written."""
        if set(payload) not in ({"name"}, {"backup"}):
            raise ValueError("请提供备份文件名 name，或完整的备份内容 backup")
        document = backup.read(self.data_dir, payload["name"]) if "name" in payload else payload["backup"]
        warnings = []
        try:
            incoming, network = self.check_restore(document, warnings)
        except ValueError as error:
            raise ValueError("备份未通过检查：" + str(error)) from None
        current = backup.snapshot(self.store)
        titles = {e["id"]: e["title"] for v in self.versions() for e in v["events"]}
        now, then = current["local_state"], incoming["local_state"]
        changes = {}
        for key, label in (("starred", "收藏"), ("completed", "完成状态"), ("ignored", "忽略状态")):
            on = sorted(i for i in then.keys() | now.keys() if bool(then.get(i, {}).get(key)) and not now.get(i, {}).get(key))
            off = sorted(i for i in then.keys() | now.keys() if bool(now.get(i, {}).get(key)) and not then.get(i, {}).get(key))
            changes[key] = {"label": label, "added": len(on), "removed": len(off), "examples": [titles.get(i, i) for i in (on + off)[:5]]}
        for key, label, fields in (("notes", "备注", ("notes",)), ("corrections", "日期校正", ("start_override", "end_override", "actual_end")),
                                   ("event_reminders", "单活动提醒", ("reminder_minutes",))):
            differ = sorted(i for i in then.keys() | now.keys() if any(then.get(i, {}).get(f) != now.get(i, {}).get(f) for f in fields))
            changes[key] = {"label": label, "changed": len(differ), "examples": [titles.get(i, i.removeprefix("version:")) for i in differ[:5]]}
        plans_now, plans_then = {p["id"]: p for p in current["personal_plans"]}, {p["id"]: p for p in incoming["personal_plans"]}
        changes["plans"] = {"label": "个人计划", "added": len(plans_then.keys() - plans_now.keys()), "removed": len(plans_now.keys() - plans_then.keys()),
                            "changed": sum(plans_then[k] != plans_now[k] for k in plans_then.keys() & plans_now.keys()),
                            "examples": [p["title"] for k, p in {**plans_now, **plans_then}.items() if plans_then.get(k) != plans_now.get(k)][:5]}
        settings_now = self.store.settings()
        # A backup taken before any setting was saved restores the defaults.
        settings_then = incoming["settings"] if incoming["settings"] is not None else DEFAULT_SETTINGS
        changes["settings"] = {"label": "显示与提醒设置", "keys": sorted(k for k in SETTING_FIELDS if settings_then.get(k, settings_now.get(k)) != settings_now.get(k))}
        changes["workspace"] = {"label": "布局与主题", "changed": (incoming["workspace"] or {}) != (current["workspace"] or {})}
        changes["desktop_window"] = {"label": "桌面窗口位置与外观", "included": backup.desktop_window(document.get("desktop_window")) is not None}
        network_now = self.network.snapshot()
        network_keys = []
        if network is not None:
            network_keys = sorted(k for k, v in network.get("policy", {}).items() if network_now["policy"].get(k) != v)
            if network_now["paused"] != network.get("paused", False):
                network_keys.append("paused")
        changes["network_policy"] = {"label": "采集设置", "keys": network_keys}
        return {"from": document.get("created_at"), "app_version": document.get("app_version"), "changes": changes, "warnings": warnings}

    def check_restore(self, document, warnings):
        """All checks that need the running app; nothing is written here."""
        contents = backup.validate(document)
        if contents["settings"] is not None:
            settings = {k: v for k, v in contents["settings"].items() if k in SETTING_FIELDS}
            if "active_game" in settings and settings["active_game"] not in self.registry.ids | {"all"}:
                warnings.append("备份中的当前游戏未安装，保留现有游戏选择")
                settings["active_game"] = self.store.settings()["active_game"]
            native = settings.pop("native_notifications", None)
            if native is not None and type(native) is not bool:
                raise ValueError("开关字段必须为布尔值")
            if native and not (self.desktop and native_available()):
                # Delivery already skips unavailable environments; keep the choice for the desktop app.
                warnings.append("当前运行方式不发送系统通知；该选项按备份保留，在桌面版中生效")
            contents["settings"] = {**self.store.settings(), **self.check_settings(settings), **({} if native is None else {"native_notifications": native})}
        network = contents["network_policy"]
        if network is not None:
            check_policy(network.get("policy", {}))
            if type(network.get("paused", False)) is not bool:
                raise ValueError("暂停开关必须为布尔值")
        missing = sorted({p["game_id"] for p in contents["personal_plans"] if p.get("game_id") and p["game_id"] not in self.registry.ids})
        if missing:
            warnings.append("以下游戏未安装，相关计划已保留：" + "、".join(missing))
        return contents, network

    def verify_personal_data(self):
        """Read restored records the way /api/state does, without the network snapshot."""
        versions = self.versions()
        workspace = self.workspace.get()
        events = [{**event, "game": version["game"]} for version in versions for event in version["events"]]
        context(versions, workspace["view"])
        sort_events(events, workspace["view"])
        self.store.settings()
        self.store.plans()

    def reminder_games(self):
        """Games that get deadline reminders; independent of page filters unless set to follow them."""
        scope = self.store.settings()["reminder_scope"]
        if scope["mode"] == "follow":
            return set(self.workspace.get()["view"]["selected_games"])
        if scope["mode"] == "custom":
            return set(scope["games"] or [])
        return None

    def check_settings(self, patch):
        if set(patch) - SETTING_FIELDS:
            raise ValueError("不支持的设置字段")
        if "active_game" in patch and (not isinstance(patch["active_game"], str) or patch["active_game"] not in self.registry.ids | {"all"}):
            raise ValueError("不支持的游戏")
        for name in ("reminders_enabled", "native_notifications", "hide_completed"):
            if name in patch and type(patch[name]) is not bool:
                raise ValueError("开关字段必须为布尔值")
        if patch.get("native_notifications") and not (self.desktop and native_available()):
            raise ValueError("此运行环境暂不支持本机系统通知")
        if "timezone" in patch and (not isinstance(patch["timezone"], str) or not re.fullmatch(r"auto|UTC|[A-Za-z_+-]+(?:/[A-Za-z_+0-9-]+)+", patch["timezone"]) or len(patch["timezone"]) > 100):
            raise ValueError("时区格式无效")
        if "view" in patch and patch["view"] not in {"timeline", "month", "list", "upcoming"}:
            raise ValueError("不支持的日历视图")
        if "sync_interval_minutes" in patch and (type(patch["sync_interval_minutes"]) is not int or not 1080 <= patch["sync_interval_minutes"] <= 1440):
            raise ValueError("自动复查间隔需为 18–24 小时，另加 0–2 小时随机错峰")
        if "reminder_offsets" in patch:
            offsets = patch["reminder_offsets"]
            if not isinstance(offsets, list) or len(offsets) > 5 or any(type(n) is not int or not 1 <= n <= 10080 for n in offsets):
                raise ValueError("提醒提前量无效")
            patch["reminder_offsets"] = sorted(set(offsets), reverse=True)
        if "reminder_scope" in patch:
            scope = patch["reminder_scope"]
            games = scope.get("games") if isinstance(scope, dict) else None
            # Uninstalled games may stay listed so a reinstall restores reminders.
            if not isinstance(scope, dict) or set(scope) != {"mode", "games"} or scope["mode"] not in {"all", "follow", "custom"} or (games is not None and (not isinstance(games, list) or len(games) > 100 or len(set(games)) != len(games) or any(not isinstance(g, str) or not g or len(g) > 100 for g in games))):
                raise ValueError("提醒游戏范围无效")
            patch["reminder_scope"] = {"mode": scope["mode"], "games": None if games is None else list(games)}
        if "disabled_plugins" in patch:
            folders = patch["disabled_plugins"]
            # Folder names only; removed folders may stay listed and simply have no effect.
            if not isinstance(folders, list) or len(folders) > 200 or len(set(folders)) != len(folders):
                raise ValueError("停用插件列表无效")
            patch["disabled_plugins"] = sorted(plugin_folder(f) for f in folders)
        return patch

    def set_plugin_enabled(self, payload):
        """Turn one plugin folder on or off; read-modify-write under the store lock so
        quick successive toggles of different plugins never overwrite each other."""
        if not isinstance(payload, dict) or set(payload) != {"folder", "enabled"} or type(payload["enabled"]) is not bool:
            raise ValueError("请提供插件文件夹 folder 和布尔值 enabled")
        folder = plugin_folder(payload["folder"])
        with self.store.lock:
            disabled = set(self.store.settings()["disabled_plugins"])
            if payload["enabled"]:
                disabled.discard(folder)
            else:
                disabled.add(folder)
            self.update_settings({"disabled_plugins": sorted(disabled)})

    def plugin_report(self):
        """Installed plugins as loaded at startup, plus what changes after the next restart."""
        disabled = set(self.store.settings()["disabled_plugins"])
        rows = []
        for row in self.registry.plugins:
            row = dict(row)
            wanted_off = row["folder"] in disabled
            row["enabled_setting"] = not wanted_off
            row["restart_required"] = (row["status"] == "disabled") != wanted_off and row["status"] in {"loaded", "disabled", "error"}
            rows.append(row)
        builtins = [{"id": g["id"], "name": g["name"], "server": g.get("server"), "source_label": g.get("source_label")} for g in self.registry.games if g.get("plugin_kind") == "builtin"]
        return {"api_version": 1, "directory": str((self.data_dir / "plugins").resolve()), "plugins": rows, "builtins": builtins, "games": self.registry.games, "errors": self.registry.errors}

    def update_settings(self, patch):
        self.store.update_settings(self.check_settings(patch))
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

        def send(self, data, status=200, content_type="application/json; charset=utf-8", filename=None, cache_control="no-store", etag=None):
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
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(encoded)

        def send_state(self):
            # The tag covers everything but server_time, so a poll that already holds this
            # state gets an empty 304 and skips parsing and merging it again.
            data = application.state()
            server_time = data.pop("server_time")
            etag = '"' + hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()[:32] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            data["server_time"] = server_time
            self.send(data, etag=etag)

        def do_GET(self):
            if not self.valid_host():
                return self.send({"error": "仅允许本机访问"}, 403)
            path = urllib.parse.urlsplit(self.path).path
            try:
                game = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("game", [application.store.settings()["active_game"]])[0]
                if game not in application.registry.ids | {"all", "selected"}:
                    game = "all" if "game" not in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query) else game
                if game not in application.registry.ids | {"all", "selected"}:
                    raise ValueError("不支持的游戏")
                if path == "/api/state":
                    return self.send_state()
                if path == "/api/workspace":
                    return self.send(application.workspace.get())
                if path == "/api/network":
                    return self.send(application.network.snapshot())
                if path == "/api/plugins":
                    return self.send(application.plugin_report())
                if path == "/api/plans":
                    return self.send({"plans": application.store.plans()})
                if path == "/api/backups":
                    return self.send({"backups": backup.listing(application.data_dir), "directory": str(backup.directory(application.data_dir).resolve())})
                if path.startswith("/api/backups/"):
                    name = path.removeprefix("/api/backups/")
                    backup.read(application.data_dir, name)
                    return self.send((backup.directory(application.data_dir) / name).read_bytes(), filename=name)
                if path == "/api/timeline":
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    workspace = application.workspace.get()
                    if "games" in query:
                        selected = query["games"][0].split(",") if query["games"][0] else []
                        if set(selected) - application.registry.ids:
                            raise ValueError("不支持的游戏组合")
                        workspace["view"]["selected_games"] = selected
                    timezone = query.get("timezone", [application.store.settings()["timezone"]])[0]
                    if timezone == "auto":
                        timezone = "UTC"  # Clients pass their actual IANA zone.
                    return self.send(timeline(application.versions(), workspace, application.store.plans(), timezone=timezone, hide_completed=application.store.settings()["hide_completed"]))
                if path == "/api/rules":
                    return self.send(application.registry.rules("all" if game == "selected" else game))
                if path == "/api/export":
                    config = application.registry.rules("all" if game == "selected" else game)
                    return self.send({"schema_version": 1, "exported_at": iso(dt.datetime.now(dt.timezone.utc)), "rules": config, "versions": application.scoped_versions(game)}, filename=game + "-calendar.json")
                if path == "/api/calendar.ics":
                    return self.send(calendar_bytes(application.scoped_versions(game), application.store.settings(), games=application.registry.games), content_type="text/calendar; charset=utf-8", filename="playtime-" + ("endfield" if game == "endfield-cn" else game) + ".ics")
                if path == "/api/health":
                    return self.send({"ok": True, "version": __version__})
                if path.startswith("/api/artwork/"):
                    image = application.artwork.path(path.removeprefix("/api/artwork/"))
                    if image:
                        content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[image.suffix]
                        return self.send(image.read_bytes(), content_type=content_type, cache_control="private, max-age=604800, immutable")
                    return self.send({"error": "图片尚未缓存"}, 404)
                assets = {"/": "index.html", "/app.js": "app.js", "/workspace.js": "workspace.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg", "/assets/oroberyl.png": "assets/oroberyl.png", "/assets/arknights-logo.png": "assets/arknights-logo.png", "/assets/endfield-icon-zh.svg": "assets/endfield-icon-zh.svg"}
                assets.update({"/customize.js": "customize.js", "/customize.css": "customize.css", "/design.css": "design.css", "/assets/arknights-app-icon.png": "assets/arknights-app-icon.png", "/assets/endfield-app-icon.png": "assets/endfield-app-icon.png"})
                assets.update({'/planner.js':'planner.js', '/planner.css':'planner.css', '/plan-drawing.js':'plan-drawing.js'})
                assets.update({'/navigation.js':'navigation.js', '/ux.css':'ux.css'})
                if path not in assets:
                    return self.send({"error": "页面不存在"}, 404)
                file = ROOT / "web" / assets[path]
                if file.suffix == ".png":
                    return self.send(file.read_bytes(), content_type="image/png", cache_control="private, max-age=86400")
                content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[file.suffix]
                return self.send(file.read_bytes(), content_type=content_type + "; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError):
                return
            except LookupError as error:
                return self.send({"error": str(error)}, 404)
            except ValueError as error:
                return self.send({"error": str(error)}, 400)

        def do_POST(self):
            self.mutate()

        def do_PATCH(self):
            self.mutate()

        def do_DELETE(self):
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
                path = urllib.parse.urlsplit(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= (backup.MAX_BYTES if path in {"/api/backups/restore", "/api/backups/preview"} else 16000):
                    return self.send({"error": "请求过大"}, 413)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ValueError("请求内容不是有效的 JSON") from None
                if not isinstance(payload, dict):
                    raise ValueError("请求应为 JSON 对象")
                if self.command == "DELETE":
                    match = re.fullmatch(r"/api/plans/(plan-[a-f0-9]{32})", path)
                    if not match:
                        return self.send({"error": "接口不存在"}, 404)
                    application.store.delete_plan(match.group(1))
                    return self.send(application.state())
                if path == "/api/plugins/state":
                    if self.command != "POST":
                        raise ValueError("切换插件需使用 POST")
                    application.set_plugin_enabled(payload)
                    return self.send({**application.state(), "plugins_report": application.plugin_report()})
                if path == '/api/plugins/open-directory':
                    if self.command != 'POST' or payload:
                        raise ValueError('此入口仅用于打开当前插件目录，不接受路径参数')
                    return self.send(application.open_plugin_directory())
                if path == "/api/backups":
                    if self.command != "POST" or payload:
                        raise ValueError("创建备份不接受参数")
                    return self.send({"backup": application.create_backup(), "backups": backup.listing(application.data_dir)})
                if path == "/api/desktop/window-applied":
                    if self.command != "POST":
                        raise ValueError("确认桌面恢复需使用 POST")
                    application.acknowledge_window(payload)
                    return self.send({"ok": True})
                if path == "/api/games/records/preview":
                    return self.send(application.preview_clear(payload))
                if path == "/api/games/records/clear":
                    cleared = application.clear_game_records(payload)
                    return self.send({**application.state(), "cleared": cleared})
                if path == "/api/backups/preview":
                    if self.command != "POST":
                        raise ValueError("预览恢复需使用 POST")
                    return self.send(application.preview_restore(payload))
                if path == "/api/backups/restore":
                    if self.command != "POST":
                        raise ValueError("恢复备份需使用 POST")
                    return self.send({"result": application.restore_backup(payload), "backups": backup.listing(application.data_dir)})
                if path == "/api/sync":
                    game = payload.get("game", application.store.settings()["active_game"])
                    if not isinstance(game, str) or game not in application.registry.ids | {"all", "selected"}:
                        raise ValueError("不支持的游戏")
                    for key, collector in application.collectors.items():
                        if game in {key, "all"} or (game == "selected" and key in application.workspace.get()["view"]["selected_games"]):
                            collector.start()
                elif path == "/api/network":
                    application.network.update(payload)
                elif path == "/api/workspace":
                    application.workspace.update(payload)
                elif path == "/api/plans":
                    application.workspace.plan(payload)
                elif match := re.fullmatch(r"/api/plans/(plan-[a-f0-9]{32})", path):
                    application.workspace.plan(payload, match.group(1))
                elif path == "/api/settings":
                    application.update_settings(payload)
                elif path == "/api/notifications/test":
                    application.reminders.test()
                elif path == "/api/notifications/read":
                    application.store.read_notifications()
                elif match := re.fullmatch(r"/api/events/([a-zA-Z0-9_-]+)", path):
                    application.update_event(match.group(1), payload)
                elif match := re.fullmatch(r"/api/versions/([a-zA-Z0-9_-]+)/maintenance", path):
                    if "actual_local" in payload:
                        if set(payload) - {"actual_local", "timezone", "fold"}:
                            raise ValueError("请提供 actual_local、timezone，可选 fold")
                        payload = {"actual_end": None if payload["actual_local"] is None else local_instant(payload["actual_local"], payload.get("timezone"), payload.get("fold"))}
                    if set(payload) != {"actual_end"} or (payload["actual_end"] is not None and not isinstance(payload["actual_end"], str)):
                        raise ValueError("请明确提供 actual_end 时间或 null")
                    version = next((v for v in application.versions() if v["id"] == match.group(1)), None)
                    if not version:
                        raise LookupError("版本不存在")
                    if not version["maintenance"].get("start"):
                        raise ValueError("这个日历没有版本维护记录")
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
    print(f"\n  游时 · 游戏活动日历 {__version__}\n  Local: http://127.0.0.1:{args.port}\n  Data:  {args.data_dir.resolve()}\n  按 Ctrl+C 停止。\n", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        application.stop.set()
        application.network.stop()
        server.server_close()


if __name__ == "__main__":
    main()
