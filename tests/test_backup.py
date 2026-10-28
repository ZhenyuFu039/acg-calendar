"""Personal-data backup, restore and the automatic copy before upgrades."""
from http.server import ThreadingHTTPServer
from contextlib import contextmanager
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
from acg_calendar.storage import Store
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

    def test_clear_records_returns_state_after_the_deletion(self):
        version = self.app.versions()[0]
        event = version["events"][0]
        self.app.update_event(event["id"], {"starred": True, "notes": "待清除"})
        plan = self.app.workspace.plan({"title": "待清除计划", "start": "2026-11-01T00:00:00Z", "end": "2026-11-01T01:00:00Z", "timezone": "UTC", "game_id": version["game"]})
        status, raw, _ = self.request("/api/games/records/clear", {"game": version["game"]})
        self.assertEqual(status, 200)
        response = json.loads(raw)
        cleared = next(e for e in response["events"] if e["id"] == event["id"])
        self.assertFalse(cleared["starred"])
        self.assertEqual(cleared["notes"], "")
        self.assertNotIn(plan["id"], [p["id"] for p in response["plans"]])
        self.assertEqual(response["cleared"]["starred"], 1)
        self.assertEqual(response["cleared"]["plans"], 1)


class RestorePreviewTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.events = [e for v in self.app.versions() for e in v["events"]][:3]

    def test_preview_counts_changes_without_writing(self):
        a, b, c = self.events
        self.app.update_event(a["id"], {"starred": True, "notes": "备份里的备注"})
        plan = self.app.workspace.plan({"title": "备份里的计划", "start": "2026-11-01T00:00:00Z", "end": "2026-11-01T01:00:00Z", "timezone": "UTC"})
        made = self.app.create_backup()
        # After the backup: unstar a, star b, change a's note, correct c, drop the plan, add another, change the time zone.
        self.app.update_event(a["id"], {"starred": False, "notes": "现在的备注"})
        self.app.update_event(b["id"], {"starred": True, "completed": True})
        self.app.update_event(c["id"], {"end_local": "2027-01-01T08:00", "timezone": "UTC"})
        self.app.store.delete_plan(plan["id"])
        self.app.workspace.plan({"title": "新计划", "start": "2026-12-01T00:00:00Z", "end": "2026-12-01T01:00:00Z", "timezone": "UTC"})
        self.app.update_settings({"timezone": "Asia/Tokyo"})
        before = (self.store.state(), self.store.plans(), self.store.settings(), len(backup.listing(self.directory)))
        preview = self.app.preview_restore({"name": made["name"]})
        self.assertEqual(before, (self.store.state(), self.store.plans(), self.store.settings(), len(backup.listing(self.directory))), "preview writes nothing, makes no safety backup")
        c_ = preview["changes"]
        self.assertEqual((c_["starred"]["added"], c_["starred"]["removed"]), (1, 1))
        self.assertIn(a["title"], c_["starred"]["examples"])
        self.assertEqual((c_["completed"]["added"], c_["completed"]["removed"]), (0, 1))
        self.assertEqual(c_["notes"]["changed"], 1)
        self.assertEqual(c_["corrections"]["changed"], 1)
        self.assertEqual((c_["plans"]["added"], c_["plans"]["removed"], c_["plans"]["changed"]), (1, 1, 0))
        self.assertEqual(sorted(c_["plans"]["examples"]), ["备份里的计划", "新计划"])
        self.assertIn("timezone", c_["settings"]["keys"])

    def test_preview_uses_the_restore_checks(self):
        made = self.app.create_backup()
        document = backup.read(self.directory, made["name"])
        del document["contents"]["personal_plans"]
        with self.assertRaisesRegex(ValueError, "备份未通过检查"):
            self.app.preview_restore({"backup": document})
        self.assertEqual(self.app.preview_restore({"name": made["name"]})["changes"]["plans"], {"label": "个人计划", "added": 0, "removed": 0, "changed": 0, "examples": []})

    def test_preview_includes_ignored_and_individual_reminders(self):
        event = self.events[0]
        self.app.update_event(event["id"], {"ignored": True, "reminder_minutes": []})
        made = self.app.create_backup()
        self.app.update_event(event["id"], {"ignored": False, "reminder_minutes": [60]})
        changes = self.app.preview_restore({"name": made["name"]})["changes"]
        self.assertEqual((changes["ignored"]["added"], changes["ignored"]["removed"]), (1, 0))
        self.assertEqual(changes["event_reminders"]["changed"], 1)
        self.assertEqual(changes["event_reminders"]["examples"], [event["title"]])
        self.app.restore_backup({"name": made["name"]})
        restored = self.store.state()[event["id"]]
        self.assertTrue(restored["ignored"])
        self.assertEqual(restored["reminder_minutes"], [])

    def test_preview_includes_network_changes_without_touching_limits(self):
        self.app.network.update({"policy": {"interval_seconds": 60}, "paused": True})
        made = self.app.create_backup()
        self.app.network.update({"policy": {"interval_seconds": 10}, "paused": False})
        before = self.app.network.snapshot()
        changes = self.app.preview_restore({"name": made["name"]})["changes"]
        self.assertEqual(changes["network_policy"]["keys"], ["interval_seconds", "paused"])
        self.assertEqual(self.app.network.snapshot(), before)
        self.app.restore_backup({"name": made["name"]})
        self.assertTrue(self.app.network.snapshot()["paused"])
        self.assertEqual(self.app.network.snapshot()["policy"]["interval_seconds"], 60)

    def test_preview_network_absent_or_partial_matches_restore_semantics(self):
        document = backup.read(self.directory, self.app.create_backup()["name"])
        self.app.network.update({"policy": {"interval_seconds": 60}, "paused": True})
        document["contents"]["network_policy"] = None
        self.assertEqual(self.app.preview_restore({"backup": document})["changes"]["network_policy"]["keys"], [])
        self.app.restore_backup({"backup": document})
        self.assertTrue(self.app.network.snapshot()["paused"])
        document["contents"]["network_policy"] = {"policy": {"hourly_limit": 20}, "paused": True}
        self.assertEqual(self.app.preview_restore({"backup": document})["changes"]["network_policy"]["keys"], ["hourly_limit"])
        self.app.restore_backup({"backup": document})
        self.assertEqual(self.app.network.snapshot()["policy"]["interval_seconds"], 60)


class DesktopWindowRestoreTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)

    def test_window_preferences_are_handed_to_the_desktop(self):
        (self.directory / "desktop-window.json").write_text(json.dumps({"left": 40, "top": 50, "card_opacity": 0.6, "bubble_mode": True, "game": "endfield-cn"}), encoding="utf-8")
        made = self.app.create_backup()
        (self.directory / "desktop-window.json").write_text(json.dumps({"left": 900}), encoding="utf-8")
        self.assertTrue(self.app.preview_restore({"name": made["name"]})["changes"]["desktop_window"]["included"])
        result = self.app.restore_backup({"name": made["name"]})
        self.assertIn("桌面窗口位置与外观会在桌面版下次刷新时应用", result["warnings"])
        pending = self.app.state()["desktop_window_pending"]
        self.assertEqual(pending["preferences"]["card_opacity"], 0.6)
        # The file itself is left to the desktop app, which may be running and rewriting it.
        self.assertEqual(json.loads((self.directory / "desktop-window.json").read_text(encoding="utf-8")), {"left": 900})

    def test_invalid_window_preferences_are_skipped(self):
        made = self.app.create_backup()
        document = backup.read(self.directory, made["name"])
        document["desktop_window"] = {"left": {"nested": True}}
        result = self.app.restore_backup({"backup": document})
        self.assertIn("备份中的桌面窗口偏好格式无效，已跳过", result["warnings"])
        self.assertIsNone(self.app.state()["desktop_window_pending"])

    def document(self, opacity):
        document = backup.read(self.directory, self.app.create_backup()["name"])
        document["desktop_window"] = {"card_opacity": opacity}
        return document

    def test_same_second_restores_have_distinct_ids_and_reject_old_ack(self):
        first, second = self.document(.4), self.document(.8)
        with patch.object(app_module, "iso", return_value="2026-10-26T00:00:00Z"):
            self.app.restore_backup({"backup": first})
            a = self.store.get_meta("desktop_window_pending")
            self.app.restore_backup({"backup": second})
            b = self.store.get_meta("desktop_window_pending")
        self.assertEqual(a["restored_at"], b["restored_at"])
        self.assertNotEqual(a["id"], b["id"])
        for old in ({"id": a["id"]}, {"restored_at": a["restored_at"]}):
            with self.assertRaises(ValueError):
                self.app.acknowledge_window(old)
        self.assertEqual(self.store.get_meta("desktop_window_pending"), b)
        self.app.acknowledge_window({"id": b["id"]})
        self.assertIsNone(self.store.get_meta("desktop_window_pending"))

    def test_personal_data_and_window_pending_are_committed_together(self):
        document = self.document(.4)
        document["contents"]["settings"] = {**self.store.settings(), "timezone": "Asia/Tokyo"}
        # Read from another Store as soon as the restore reaches validation: both sections
        # must already belong to the same restore, not an old pending/new personal-data mix.
        reader = Store(self.store.path)
        def verify():
            self.assertEqual(reader.settings()["timezone"], "Asia/Tokyo")
            self.assertEqual(reader.get_meta("desktop_window_pending")["preferences"], {"card_opacity": .4})
        with patch.object(self.app, "verify_personal_data", side_effect=verify):
            self.app.restore_backup({"backup": document})

    def test_failed_validation_restores_previous_pending(self):
        self.app.restore_backup({"backup": self.document(.4)})
        previous = self.store.get_meta("desktop_window_pending")
        document = self.document(.8)
        with patch.object(self.app, "verify_personal_data", side_effect=RuntimeError("读取失败")):
            with self.assertRaisesRegex(ValueError, "已回到恢复前"):
                self.app.restore_backup({"backup": document})
        self.assertEqual(self.store.get_meta("desktop_window_pending"), previous)

    def test_windowless_restore_cancels_an_older_pending_handover(self):
        document = self.document(.4)
        self.app.restore_backup({"backup": document})
        document["desktop_window"] = None
        self.app.restore_backup({"backup": document})
        self.assertIsNone(self.store.get_meta("desktop_window_pending"))

    def test_concurrent_restores_keep_personal_data_and_pending_paired(self):
        first, second = self.document(.4), self.document(.8)
        first["contents"]["settings"] = {**self.store.settings(), "timezone": "Asia/Tokyo"}
        second["contents"]["settings"] = {**self.store.settings(), "timezone": "UTC"}
        entered, release, second_started, second_done = (threading.Event() for _ in range(4))
        errors = []
        original_apply = backup.apply
        def paused_apply(*args, **kwargs):
            original_apply(*args, **kwargs)
            if threading.current_thread().name == "restore-first":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("restore test timed out")
        def restore(document, started=None, done=None):
            try:
                if started:
                    started.set()
                self.app.restore_backup({"backup": document})
            except Exception as error:
                errors.append(error)
            finally:
                if done:
                    done.set()
        a = threading.Thread(target=restore, args=(first,), name="restore-first")
        b = threading.Thread(target=restore, args=(second, second_started, second_done))
        with patch.object(backup, "apply", side_effect=paused_apply):
            a.start()
            try:
                self.assertTrue(entered.wait(5))
                b.start()
                self.assertTrue(second_started.wait(5))
                self.assertFalse(second_done.wait(.1), "the second restore must wait for the first critical section")
            finally:
                release.set()
                a.join(5)
                if b.ident is not None:
                    b.join(5)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.store.settings()["timezone"], "UTC")
        self.assertEqual(self.store.get_meta("desktop_window_pending")["preferences"], {"card_opacity": .8})

    def test_ack_does_not_clear_a_replacement_from_another_store(self):
        self.app.restore_backup({"backup": self.document(.4)})
        pending = self.store.get_meta("desktop_window_pending")
        replacement = {**pending, "id": "different-id", "preferences": {"card_opacity": .8}}
        other = Store(self.store.path)
        original_connection = self.app.store.connection
        @contextmanager
        def interleaved_connection():
            with original_connection() as conn:
                class Interleaved:
                    def execute(_, sql, args=()):
                        if sql.startswith("DELETE FROM meta WHERE id='desktop_window_pending'"):
                            other.set_meta("desktop_window_pending", replacement)
                        return conn.execute(sql, args)
                yield Interleaved()
        with patch.object(self.app.store, "connection", interleaved_connection):
            with self.assertRaisesRegex(ValueError, "新的恢复替换"):
                self.app.acknowledge_window({"id": pending["id"]})
        self.assertEqual(self.store.get_meta("desktop_window_pending"), replacement)


class DesktopWindowAppliedHttpTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, payload):
        request = urllib.request.Request(self.base + "/api/desktop/window-applied", data=json.dumps(payload).encode(), headers={"X-Calendar-Token": self.app.csrf})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_only_the_matching_hand_over_is_cleared(self):
        self.app.store.set_meta("desktop_window_pending", {"preferences": {"left": 1}, "restored_at": "2026-10-25T00:00:00Z"})
        self.assertEqual(self.post({"restored_at": "2026-10-24T00:00:00Z"}), 400)
        self.assertIsNotNone(self.app.store.get_meta("desktop_window_pending"))
        self.assertEqual(self.post({"restored_at": "2026-10-25T00:00:00Z"}), 200)
        self.assertIsNone(self.app.store.get_meta("desktop_window_pending"))

    def test_new_handovers_require_the_matching_unique_id(self):
        self.store.set_meta("desktop_window_pending", {"id": "new-id", "preferences": {"left": 1}, "restored_at": "2026-10-26T00:00:00Z"})
        for payload in ({"id": "old-id"}, {"restored_at": "2026-10-26T00:00:00Z"}, {"id": None}, {}):
            self.assertEqual(self.post(payload), 400)
            self.assertIsNotNone(self.store.get_meta("desktop_window_pending"))
        self.assertEqual(self.post({"id": "new-id"}), 200)
        self.assertIsNone(self.store.get_meta("desktop_window_pending"))
        self.assertEqual(self.post({"id": "new-id"}), 400)
