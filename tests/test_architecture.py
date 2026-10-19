"""Contract tests: third provider, migration, projection, and personal data."""
import copy
import datetime as dt
from http.server import ThreadingHTTPServer
import json
import shutil
import threading
import urllib.error
import urllib.request

from app import Application, handler_for
from acg_calendar.ical import calendar_bytes
from acg_calendar.parser import ROOT, stamp
from acg_calendar.providers import ProviderRegistry, JsonProvider, create_registry
from acg_calendar.reminders import ReminderEngine
from acg_calendar.workspace import WorkspaceService, timeline
from test_calendar import TempStoreTest

NOW = stamp("2026-10-19T12:00:00Z")


class ArchitectureTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.plugin = self.directory / "plugins/sample-game"
        shutil.copytree(ROOT / "examples/plugins/sample-game", self.plugin)
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.workspace = self.app.workspace

    def write_manifest(self, patch):
        path = self.plugin / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(patch)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_third_provider_loaded_without_host_changes(self):
        state = self.app.state()
        self.assertEqual(len(state["games"]), 3)
        sample = next(g for g in state["games"] if g["id"] == "sample-game")
        self.assertNotIn("currency", sample)
        events = [e for e in state["events"] if e["game"] == "sample-game"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["start"]["at"], "2026-10-15T14:00:00Z")
        self.assertEqual(events[0]["end"]["at"], "2026-11-02T15:00:00Z")
        self.assertEqual(events[1]["parent_id"], events[0]["id"])

    def test_game_combinations_and_scoped_export(self):
        for combo in (["endfield-cn", "sample-game"], ["endfield-cn", "arknights-cn"]):
            self.workspace.update({"view": {"selected_games": combo}})
            result = timeline(self.app.versions(), self.workspace.get(), now=NOW)
            self.assertEqual({e["game"] for e in result["events"]}, set(combo))
            self.assertEqual({v["game"] for v in self.app.scoped_versions("selected")}, set(combo))
        self.workspace.update({"view": {"selected_games": []}})
        self.assertEqual(timeline(self.app.versions(), self.workspace.get(), now=NOW)["events"], [])

    def test_single_game_custom_scope_and_empty_selection_survive_restart(self):
        for games in (["sample-game"], []):
            self.workspace.update({"view": {"selected_games": games, "scope": "selected"}})
            restored = WorkspaceService(self.store, self.app.registry).get()
            self.assertEqual(restored["view"]["scope"], "selected")
            self.assertEqual(restored["view"]["selected_games"], games)

    def test_state_order_is_the_same_order_used_by_projection(self):
        self.workspace.update({"view": {"selected_games": ["endfield-cn", "sample-game"], "sort": "remaining"}})
        sample = self.app.scoped_versions("sample-game")[0]["events"][1]
        self.app.update_event(sample["id"], {"starred": True})
        state = self.app.state()
        projected = timeline(self.app.versions(), self.workspace.get())["events"]
        ids = {e["id"] for e in projected}
        self.assertEqual([key for key in state["event_order"] if key in ids], [e["id"] for e in projected])

    def test_sync_and_restart_preserve_existing_ids_and_personal_state(self):
        old_ids = [e["id"] for v in self.app.versions() for e in v["events"]]
        event = self.app.versions()[0]["events"][0]
        self.app.update_event(event["id"], {"starred": True, "completed": True, "notes": "保留备注", "end_override": "2026-12-01T00:00:00Z"})
        self.workspace.update({"view": {"selected_games": ["sample-game", "endfield-cn"], "sort": "remaining"}})
        again = Application(self.directory, auto_sync=False, desktop=False)
        self.assertEqual(old_ids, [e["id"] for v in again.versions() for e in v["events"]])
        resolved = next(e for v in again.versions() for e in v["events"] if e["id"] == event["id"])
        self.assertTrue(resolved["starred"] and resolved["completed"])
        self.assertEqual(resolved["notes"], "保留备注")
        self.assertEqual(resolved["end"]["state"], "manual")
        self.assertEqual(again.workspace.get()["view"]["sort"], "remaining")

    def test_bad_refresh_does_not_destroy_successful_snapshot(self):
        before = self.store.versions()
        (self.plugin / "events.json").write_text('{"schema_version":1,"versions":[{"id":"bad"}]}', encoding="utf-8")
        self.assertFalse(self.app.collectors["sample-game"].start())
        self.assertEqual(before, self.store.versions())
        self.assertTrue(self.store.get_meta("sync:sample-game")["error"])

    def test_duplicate_or_unsupported_plugins_are_isolated(self):
        self.write_manifest({"api_version": 999})
        registry = create_registry(self.store, self.directory)
        self.assertEqual(registry.ids, {"endfield-cn", "arknights-cn"})
        self.assertEqual(len(registry.errors), 1)
        self.write_manifest({"api_version": 1, "game": {"id": "endfield-cn", "name": "x", "short_name": "x", "server": "x", "timezone": "UTC", "source_label": "x"}})
        self.assertEqual(len(create_registry(self.store, self.directory).errors), 1)

    def test_plugin_deactivation_retains_cache_and_selection(self):
        self.workspace.update({"view": {"selected_games": ["sample-game"]}})
        self.write_manifest({"enabled": False})
        again = Application(self.directory, auto_sync=False, desktop=False)
        self.assertNotIn("sample-game", again.registry.ids)
        self.assertTrue(any(v["game"] == "sample-game" for v in self.store.versions()))
        self.assertEqual(again.workspace.get()["view"]["selected_games"], ["sample-game"])
        again.workspace.update({"view": {"sort": "remaining"}})
        self.assertEqual(again.workspace.get()["view"]["selected_games"], ["sample-game"])
        self.assertNotIn("sample-game", {v["game"] for v in again.versions()})

    def test_host_does_not_require_endfield_provider(self):
        registry = ProviderRegistry()
        registry.add(self.app.registry.providers["sample-game"])
        minimal = Application(self.directory, registry=registry, auto_sync=False, desktop=False)
        self.assertEqual(len(minimal.state()["events"]), 2)
        self.assertIsNone(minimal.collector)
        self.assertEqual(set(minimal.state()["sync_by_game"]), {"sample-game"})

    def test_plugin_ids_survive_period_moves_and_do_not_collide(self):
        provider = self.app.registry.providers["sample-game"]
        data = json.loads((self.plugin / "events.json").read_text(encoding="utf-8"))
        original = provider.normalize(data)[0]["events"][0]["id"]
        data["versions"][0]["id"] = "different-period"
        self.assertEqual(original, provider.normalize(data)[0]["events"][0]["id"])
        data["versions"][0]["events"].append(copy.deepcopy(data["versions"][0]["events"][0]))
        with self.assertRaisesRegex(ValueError, "重复"):
            provider.normalize(data)

    def test_parent_cycles_and_unsafe_source_links_are_rejected(self):
        provider = self.app.registry.providers["sample-game"]
        data = json.loads((self.plugin / "events.json").read_text(encoding="utf-8"))
        data["versions"][0]["events"][0]["parent_id"] = "demo-shop"
        with self.assertRaisesRegex(ValueError, "循环"):
            provider.normalize(data)
        data["versions"][0]["events"][0]["parent_id"] = None
        data["versions"][0]["events"][0]["source_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            provider.normalize(data)

    def test_view_filters_never_change_reminder_scope_or_times(self):
        before = self.app.versions()
        self.workspace.update({"view": {"selected_games": [], "hide_future": True}})
        self.store.update_settings({"reminder_offsets": [10080]})
        reminders = self.app.reminders.tick(stamp("2026-11-01T15:00:00Z"))
        self.assertTrue(any(n["game"] == "sample-game" for n in reminders))
        self.assertEqual(before, self.app.versions())

    def test_star_priority_remaining_sort_and_hide_future(self):
        self.workspace.update({"view": {"selected_games": ["endfield-cn", "sample-game"], "sort": "remaining", "hide_future": True, "group_by": "none"}})
        sample = self.app.scoped_versions("sample-game")[0]["events"][1]
        self.app.update_event(sample["id"], {"starred": True})
        result = timeline(self.app.versions(), self.workspace.get(), now=NOW)
        self.assertEqual(result["events"][0]["id"], sample["id"])
        self.assertTrue(all(stamp(e["start"]["at"]) <= NOW for e in result["events"]))
        self.assertEqual(result["groups"][0]["id"], "all")

    def test_density_uses_display_zone_and_excludes_completed_uncertain(self):
        self.workspace.update({"view": {"selected_games": ["sample-game"]}})
        versions = self.app.scoped_versions("sample-game")
        event = versions[0]["events"][0]
        event["end"]["at"] = "2026-11-02T01:00:00Z"
        result = timeline(versions, self.workspace.get(), now=NOW, timezone="America/New_York")
        self.assertEqual(result["deadline_density"]["2026-11-01"]["activities"], 1)
        event["completed"] = True
        result = timeline(versions, self.workspace.get(), now=NOW, timezone="America/New_York")
        self.assertNotIn("2026-11-01", result["deadline_density"])

    def test_plans_are_separate_and_survive_source_refresh(self):
        original = self.app.versions()
        plan = self.workspace.plan({"title": "周末游玩", "start": "2026-10-25T10:00:00-04:00", "end": "2026-10-25T12:00:00-04:00", "timezone": "America/New_York", "game_id": "sample-game"})
        self.app.collectors["sample-game"].start()
        self.assertEqual(self.store.plans(), [plan])
        self.assertEqual(original, self.app.versions())
        self.workspace.plan({"title": "调整标题"}, plan["id"])
        self.assertEqual(self.store.plans()[0]["title"], "调整标题")
        self.store.delete_plan(plan["id"])
        self.assertEqual(self.store.plans(), [])

    def test_plan_bad_ranges_and_cross_game_links_rejected(self):
        plan = {"title": "计划", "start": "2026-10-25T14:00:00Z", "end": "2026-10-25T13:00:00Z"}
        with self.assertRaises(ValueError): self.workspace.plan(plan)
        plan["end"] = "2026-10-25T15:00:00Z"
        plan.update(game_id="arknights-cn", event_id=self.app.scoped_versions("sample-game")[0]["events"][0]["id"])
        with self.assertRaises(ValueError): self.workspace.plan(plan)

    def test_layout_theme_and_focus_are_independent_versioned_preferences(self):
        self.workspace.update({"theme": {"preset": "dark", "accent": "#8873AE"}, "view": {"focus": {"date": "2026-10-25", "event_id": None}}})
        layout = self.workspace.get()["layout"]
        layout["modules"].reverse()
        self.workspace.update({"layout": layout})
        self.assertEqual(self.workspace.get()["theme"]["preset"], "dark")
        self.assertEqual(self.workspace.get()["view"]["focus"]["date"], "2026-10-25")
        with self.assertRaises(ValueError): self.workspace.update({"theme": {"accent": "url(evil)"}})
        with self.assertRaises(ValueError): self.workspace.update({"schema_version": 2})

    def test_ics_uses_plugin_identity_without_imaginary_cn_server(self):
        data = calendar_bytes(self.app.scoped_versions("sample-game"), self.store.settings(), games=self.app.registry.games).decode()
        self.assertIn("示例游戏（虚构） · 测试服", data)
        self.assertNotIn("国服", data)

    def test_http_workspace_plans_and_csrf(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        def request(path, value=None, method=None, token=True):
            headers = {"Content-Type": "application/json"}
            if token: headers["X-Calendar-Token"] = self.app.csrf
            req = urllib.request.Request(base + path, data=json.dumps(value).encode() if value is not None else None, headers=headers, method=method)
            with urllib.request.urlopen(req) as response: return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                request("/api/workspace", {"view": {"selected_games": []}}, "PATCH", token=False)
            self.assertEqual(denied.exception.code, 403)
            request("/api/workspace", {"view": {"selected_games": ["sample-game", "arknights-cn"]}}, "PATCH")
            self.assertEqual({e["game"] for e in request("/api/timeline")["events"]}, {"sample-game", "arknights-cn"})
            request("/api/plans", {"title": "游玩", "start": "2026-10-25T14:00:00Z", "end": "2026-10-25T15:00:00Z"}, "POST")
            plan = request("/api/plans")["plans"][0]
            request("/api/plans/" + plan["id"], {"notes": "个人备注"}, "PATCH")
            request("/api/plans/" + plan["id"], {}, "DELETE")
            self.assertEqual(request("/api/plans")["plans"], [])
            self.assertEqual(request("/api/plugins")["api_version"], 1)
        finally:
            server.shutdown(); server.server_close(); thread.join()
