"""Personal-data backup, restore and the automatic copy before upgrades."""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
import urllib.error
import urllib.request

import app as app_module
from app import Application, handler_for
from acg_calendar import backup
from acg_calendar.parser import iso, stamp
from test_calendar import TempStoreTest

import datetime as dt


class BackupTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.event = self.app.versions()[0]["events"][0]

    def personalise(self):
        end = iso(stamp(self.event["end"]["at"]) + dt.timedelta(days=1))
        self.app.update_event(self.event["id"], {"starred": True, "completed": True, "notes": "周末清体力", "end_override": end})
        plan = self.app.workspace.plan({"title": "推进剧情", "start": "2026-10-26T12:00:00Z", "end": "2026-10-26T14:00:00Z", "timezone": "Asia/Shanghai"})
        self.app.update_settings({"timezone": "Asia/Tokyo", "reminder_offsets": [4320, 60]})
        layout = self.app.workspace.get()["layout"]
        layout["sidebar"]["width"] = 300
        self.app.workspace.update({"layout": layout, "theme": {"preset": "dark"}})
        self.app.network.update({"policy": {"interval_seconds": 30}})
        return end, plan

    def personal(self):
        return {"state": self.store.state(), "plans": self.store.plans(), "settings": self.store.settings(),
                "workspace": self.app.workspace.get(), "policy": self.app.network.snapshot()["policy"]}

    def test_roundtrip_restores_every_personal_record(self):
        self.personalise()
        before = self.personal()
        made = self.app.create_backup()
        self.assertEqual(made["reason"], "manual")
        self.assertEqual(made["counts"], {"starred": 1, "completed": 1, "notes": 1, "corrections": 1, "plans": 1})
        # Change everything after the backup.
        self.app.update_event(self.event["id"], {"starred": False, "notes": "改掉了", "clear_overrides": True})
        self.app.workspace.plan({"title": "新计划", "start": "2026-11-01T00:00:00Z", "end": "2026-11-01T01:00:00Z", "timezone": "UTC"})
        self.app.update_settings({"timezone": "UTC"})
        self.app.workspace.update({"theme": {"preset": "light"}})
        self.app.network.update({"policy": {"interval_seconds": 50}})
        changed = self.personal()
        result = self.app.restore_backup({"name": made["name"]})
        self.assertEqual(self.personal(), before)
        self.assertEqual(result["warnings"], [])
        # The data replaced by the restore is itself kept and can be restored back.
        safety = backup.read(self.directory, result["safety_backup"])
        self.assertEqual(safety["reason"], "before-restore")
        self.app.restore_backup({"name": result["safety_backup"]})
        self.assertEqual(self.personal(), changed)

    def test_official_data_and_site_cooldowns_are_not_part_of_restore(self):
        self.personalise()
        versions = self.store.versions()
        made = self.app.create_backup()
        document = backup.read(self.directory, made["name"])
        self.assertNotIn("versions", document["contents"])
        self.assertEqual(set(document["contents"]["network_policy"]), {"policy", "paused"})
        site = self.app.network._site("example.org")
        site.update(cooldown_until=9_999_999_999, requests=7)
        self.app.restore_backup({"name": made["name"]})
        self.assertEqual(self.app.network.sites["example.org"]["cooldown_until"], 9_999_999_999)
        self.assertEqual(self.store.versions(), versions)

    def test_invalid_backups_are_rejected_before_anything_is_written(self):
        self.personalise()
        before = self.personal()
        export = {"schema_version": 1, "exported_at": "2026-10-25T00:00:00Z", "rules": {}, "versions": []}
        good = backup.read(self.directory, self.app.create_backup()["name"])
        newer = {**good, "schema_version": 2}
        bad_plan = json.loads(json.dumps(good))
        bad_plan["contents"]["personal_plans"][0]["end"] = bad_plan["contents"]["personal_plans"][0]["start"]
        bad_setting = json.loads(json.dumps(good))
        bad_setting["contents"]["settings"]["timezone"] = "../../etc"
        bad_policy = json.loads(json.dumps(good))
        bad_policy["contents"]["network_policy"]["policy"]["interval_seconds"] = 1
        count = len(backup.listing(self.directory))
        for document, message in ((export, "不是游时个人数据备份"), (newer, "更新的版本"), (bad_plan, "结束时间需晚于开始"),
                                  (bad_setting, "时区格式无效"), (bad_policy, "请求间隔"), ([], "不是游时个人数据备份")):
            with self.assertRaisesRegex(ValueError, message):
                self.app.restore_backup({"backup": document})
        self.assertEqual(self.personal(), before)
        self.assertEqual(len(backup.listing(self.directory)), count)
        for name in ("../calendar.sqlite3", "playtime-backup-x.json", None):
            with self.assertRaises(ValueError):
                self.app.restore_backup({"name": name})

    def test_failed_verification_rolls_back_to_current_data(self):
        self.personalise()
        made = self.app.create_backup()
        self.app.update_event(self.event["id"], {"notes": "当前备注"})
        before = self.personal()
        with patch.object(Application, "verify_personal_data", side_effect=RuntimeError("模拟读取失败")):
            with self.assertRaisesRegex(ValueError, "已回到恢复前的状态"):
                self.app.restore_backup({"name": made["name"]})
        self.assertEqual(self.personal(), before)

    def concurrent_edit(self, verification):
        """Start a restore, and while it checks the restored data, save an edit from another window."""
        self.app.update_event(self.event["id"], {"notes": "恢复前"})
        document = backup.read(self.directory, self.app.create_backup()["name"])
        document["contents"]["local_state"][self.event["id"]]["notes"] = "来自备份"
        entered, outcome = threading.Event(), {}

        def checking(application):
            entered.set()
            time.sleep(0.4)  # The other window's request arrives during this check.
            verification()

        def other_window():
            entered.wait(5)
            self.app.update_event(self.event["id"], {"notes": "另一窗口已保存"})
            outcome["saved"] = True
        worker = threading.Thread(target=other_window)
        worker.start()
        with patch.object(Application, "verify_personal_data", checking):
            try:
                self.app.restore_backup({"backup": document})
            except ValueError as error:
                outcome["error"] = str(error)
        worker.join(10)
        self.assertTrue(outcome.get("saved"))
        return outcome

    def test_edit_saved_during_failed_restore_is_not_rolled_back(self):
        def fail():
            raise RuntimeError("模拟读取失败")
        outcome = self.concurrent_edit(fail)
        self.assertIn("已回到恢复前的状态", outcome["error"])
        self.assertEqual(self.store.state()[self.event["id"]]["notes"], "另一窗口已保存")

    def test_edit_saved_during_successful_restore_lands_after_it(self):
        outcome = self.concurrent_edit(lambda: None)
        self.assertNotIn("error", outcome)
        self.assertEqual(self.store.state()[self.event["id"]]["notes"], "另一窗口已保存")

    def test_personal_fields_use_normal_write_rules_before_any_write(self):
        self.personalise()
        good = backup.read(self.directory, self.app.create_backup()["name"])
        before, count = self.personal(), len(backup.listing(self.directory))
        cases = (({"reminder_minutes": ["bad"]}, "提醒提前量"), ({"reminder_minutes": [0]}, "提醒提前量"),
                 ({"reminder_minutes": list(range(1, 7))}, "提醒提前量"), ({"unknown": 1}, "不支持的字段"),
                 ({"start_override": "2026-11-02T00:00:00Z", "end_override": "2026-11-01T00:00:00Z"}, "结束时间必须晚于"))
        for fields, message in cases:
            document = json.loads(json.dumps(good))
            document["contents"]["local_state"][self.event["id"]].update(fields)
            with self.assertRaisesRegex(ValueError, "当前数据未改变.*" + message):
                self.app.restore_backup({"backup": document})
        self.assertEqual(self.personal(), before)
        self.assertEqual(len(backup.listing(self.directory)), count, "no safety backup for a rejected file")
        self.app.reminders.tick()

    def test_missing_sections_are_rejected_but_explicit_empty_ones_restore(self):
        self.personalise()
        good = backup.read(self.directory, self.app.create_backup()["name"])
        before = self.personal()
        for section in backup.SECTIONS:
            document = json.loads(json.dumps(good))
            del document["contents"][section]
            with self.assertRaisesRegex(ValueError, "缺少：" + section):
                self.app.restore_backup({"backup": document})
        with self.assertRaisesRegex(ValueError, "内容不完整"):
            self.app.restore_backup({"backup": {"format": backup.FORMAT, "schema_version": 1, "contents": {}}})
        self.assertEqual(self.personal(), before)
        empty = json.loads(json.dumps(good))
        empty["contents"].update(local_state={}, personal_plans=[])
        self.app.restore_backup({"backup": empty})
        self.assertEqual((self.store.state(), self.store.plans()), ({}, []))

    def test_simultaneous_backups_in_one_second_keep_separate_files(self):
        self.personalise()
        moment = dt.datetime(2026, 10, 25, tzinfo=dt.timezone.utc)
        for link in (os.link, None):
            barrier, names, errors = threading.Barrier(4), [], []
            original = Path.write_text

            def synchronized(path, *args, **kwargs):
                if path.suffix == ".tmp":
                    barrier.wait(timeout=5)  # Every writer has chosen its paths before any finishes.
                return original(path, *args, **kwargs)

            def create(reason):
                try:
                    names.append(backup.create(self.store, self.directory, reason, app_version="t", now=moment)["name"])
                except Exception as error:
                    errors.append(error)
            unsupported = patch.object(backup.os, "link", side_effect=OSError("no hard links")) if link is None else patch.object(backup.os, "link", link)
            with patch.object(Path, "write_text", synchronized), unsupported:
                threads = [threading.Thread(target=create, args=(r,)) for r in ("manual", "manual", "before-restore", "before-restore")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(10)
            self.assertEqual(errors, [])
            self.assertEqual(len(set(names)), 4, names)
            for name in names:
                self.assertEqual(backup.read(self.directory, name)["format"], backup.FORMAT)
            self.assertEqual(list(backup.directory(self.directory).glob("*.tmp")), [])
            for name in names:
                (backup.directory(self.directory) / name).unlink()

    def test_missing_plugins_and_unsupported_notifications_only_warn(self):
        self.personalise()
        document = backup.read(self.directory, self.app.create_backup()["name"])
        document["contents"]["personal_plans"][0]["game_id"] = "removed-game"
        document["contents"]["settings"].update(active_game="removed-game", native_notifications=True)
        result = self.app.restore_backup({"backup": document})
        self.assertEqual(len(result["warnings"]), 3)
        self.assertEqual(self.store.plans()[0]["game_id"], "removed-game")
        self.assertTrue(self.store.settings()["native_notifications"], "kept for the desktop app")
        self.assertIn(self.store.settings()["active_game"], self.app.registry.ids | {"all"})

    def test_version_change_creates_one_backup_before_migration(self):
        fresh = backup.listing(self.directory)
        self.assertEqual(fresh, [], "an empty install has nothing to back up")
        self.assertEqual(self.store.get_meta("backup:app-version"), app_module.__version__)
        self.personalise()
        self.store.set_meta("backup:app-version", "0.0.7")
        upgraded = Application(self.directory, auto_sync=False, desktop=False)
        self.assertEqual(upgraded.upgrade_backup["reason"], "upgrade")
        self.assertEqual(upgraded.upgrade_backup["note"], "0.0.7 → " + app_module.__version__)
        self.assertEqual(upgraded.upgrade_backup["counts"]["plans"], 1)
        again = Application(self.directory, auto_sync=False, desktop=False)
        self.assertIsNone(again.upgrade_backup)
        self.assertEqual(len(backup.listing(self.directory)), 1)

    def test_automatic_backups_are_pruned_but_manual_ones_are_kept(self):
        self.personalise()
        moment = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for i in range(3):
            backup.create(self.store, self.directory, "manual", app_version="t", now=moment)
        for i in range(backup.KEEP_AUTOMATIC + 5):
            backup.create(self.store, self.directory, "before-restore", app_version="t", now=moment + dt.timedelta(seconds=i))
        rows = backup.listing(self.directory)
        self.assertEqual(sum(r["reason"] == "manual" for r in rows), 3)
        self.assertEqual(sum(r["reason"] == "before-restore" for r in rows), backup.KEEP_AUTOMATIC)


class BackupHttpTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None, token=True):
        headers = {"X-Calendar-Token": self.app.csrf} if token else {}
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=headers)) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_create_list_download_and_restore_large_upload(self):
        events = self.app.versions()[0]["events"][:3]
        event = events[0]
        self.assertEqual(self.request("/api/backups", {}, token=False)[0], 403)
        for item in events:
            self.app.update_event(item["id"], {"notes": "长备注" * 600})
        status, body, _ = self.request("/api/backups", {})
        self.assertEqual(status, 200)
        name = json.loads(body)["backup"]["name"]
        status, body, headers = self.request("/api/backups")
        self.assertEqual([r["name"] for r in json.loads(body)["backups"]], [name])
        status, raw, headers = self.request("/api/backups/" + name)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        document = json.loads(raw)
        self.app.update_event(event["id"], {"notes": ""})
        # Uploads larger than the normal 16 KB request limit are accepted for restore only.
        self.assertGreater(len(json.dumps({"backup": document})), 16000)
        status, body, _ = self.request("/api/backups/restore", {"backup": document})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.app.store.state()[event["id"]]["notes"], "长备注" * 600)
        self.assertEqual(self.request("/api/settings", {"timezone": "UTC", "pad": "x" * 17000})[0], 413)

    def test_download_rejects_paths_outside_backup_folder(self):
        for path in ("/api/backups/..%2Fcalendar.sqlite3", "/api/backups/calendar.sqlite3", "/api/backups/playtime-backup-20260101-000000-manual.json"):
            status, _, _ = self.request(path)
            self.assertIn(status, {400, 404}, path)
