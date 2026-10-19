from __future__ import annotations

import copy
import datetime as dt
from html import escape
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from app import Application, handler_for
from acg_calendar.collector import Collector
from acg_calendar.parser import AnnouncementParser, ROOT, html_to_text, resolve_versions, stamp
from acg_calendar.reminders import ReminderEngine
from acg_calendar.storage import Store

TEXT = (ROOT / "samples/endfield-cn-6094.txt").read_text(encoding="utf-8")
META = json.loads((ROOT / "samples/source.json").read_text(encoding="utf-8"))


def version():
    return AnnouncementParser().parse(TEXT, **META, origin="snapshot")


def find_event(data, name):
    return next(e for e in data["events"] if name in e["title"])


class TempStoreTest(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "logs" / "unit-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.directory = Path(self.temporary.name).resolve()
        # TemporaryDirectory cleanup is recursive; verify the exact owned target.
        self.assertTrue(self.directory.is_relative_to(parent.resolve()))
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(self.directory / "calendar.sqlite3")


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.data = version()

    def test_version_hierarchy_has_24_distinct_entries(self):
        self.assertEqual(self.data["name"], "丹青渡")
        self.assertEqual(len(self.data["events"]), 24)
        self.assertEqual(len({e["id"] for e in self.data["events"]}), 24)
        self.assertFalse(self.data["analysis"]["ai_used"])

    def test_photo_activity_has_its_own_exact_window(self):
        event = find_event(self.data, "卡莉的芳菲童话")
        self.assertEqual(event["start"]["at"], "2026-10-22T04:00:00Z")
        self.assertEqual(event["end"]["at"], "2026-11-04T20:00:00Z")
        self.assertIn("拍照任务", event["description"])

    def test_maintenance_references_are_estimates(self):
        event = find_event(self.data, "万物有灵")
        self.assertEqual(event["start"]["state"], "estimated")
        self.assertEqual(event["start"]["reference"], "maintenance_end")
        self.assertEqual(event["start"]["at"], "2026-10-15T04:00:00Z")

    def test_cycles_are_not_converted_to_guessed_dates(self):
        event = find_event(self.data, "篆玉申领")
        self.assertIsNone(event["end"]["at"])
        self.assertEqual(event["end"]["reference"], "recruitment_cycles")

    def test_next_maintenance_is_unresolved_until_available(self):
        event = find_event(self.data, "烟火生缘")
        self.assertIsNone(event["end"]["at"])
        self.assertEqual(event["end"]["reference"], "next_maintenance_start")

    def test_phase_does_not_replace_activity_deadline(self):
        event = find_event(self.data, "百趣演武")
        self.assertEqual(event["end"]["at"], "2026-11-18T20:00:00Z")
        self.assertEqual(event["phases"][0]["at"], "2026-11-10T20:00:00Z")

    def test_exchange_is_a_separate_deadline(self):
        activity = find_event(self.data, "浸蓝测试作战")
        shop = find_event(self.data, "机密圣所")
        self.assertIsNone(activity["end"]["at"])
        self.assertEqual(shop["category"], "exchange")
        self.assertEqual(shop["end"]["at"], "2026-12-02T20:00:00Z")
        self.assertEqual(shop["parent_title"], activity["title"])

    def test_nested_seasons_and_inline_activity_have_own_entries(self):
        self.assertEqual(sum("战争回响" in e["title"] for e in self.data["events"]), 2)
        self.assertEqual(find_event(self.data, "丰碑留名")["end"]["at"], "2026-11-04T20:00:00Z")

    def test_point_update_has_no_invented_end(self):
        event = find_event(self.data, "密境行者")
        self.assertEqual(event["time_kind"], "point")
        self.assertIsNone(event["end"]["at"])

    def test_ids_survive_page_chrome_and_line_changes(self):
        changed = AnnouncementParser().parse("导航信息\n" + TEXT, **META)
        self.assertEqual([e["id"] for e in changed["events"]], [e["id"] for e in self.data["events"]])

    def test_html_inline_spans_do_not_split_dates(self):
        self.assertEqual(html_to_text('<p>活动时间：<span>2026/10/22</span> <b>12:00</b></p><script>bad()</script>'), '活动时间：2026/10/22 12:00')

    def test_reversed_interval_is_flagged(self):
        parser = AnnouncementParser()
        start, end, warnings = parser.window("2026/11/05 12:00 - 2026/10/22 04:00", 2026, {})
        self.assertIsNone(end["at"])
        self.assertEqual(end["reference"], "invalid_interval")
        self.assertTrue(warnings)

    def test_invalid_date_keeps_raw_evidence(self):
        start, end, warnings = AnnouncementParser().window("2026/02/30 12:00 - 2026/03/01 04:00", 2026, {})
        self.assertIsNone(start["at"])
        self.assertIn("02/30", start["raw"])
        self.assertTrue(warnings)

    def test_unrecognized_document_is_rejected(self):
        with self.assertRaises(ValueError):
            AnnouncementParser().parse("网站改版了", cid="1", source_url="https://endfield.hypergryph.com/news/1")


class ResolutionTests(unittest.TestCase):
    def test_actual_opening_updates_references_without_mutating_source(self):
        original = version()
        result = resolve_versions([original], {"version:" + original["id"]: {"actual_end": "2026-10-15T04:15:00Z"}})[0]
        self.assertEqual(find_event(result, "万物有灵")["start"]["state"], "confirmed")
        self.assertEqual(find_event(result, "万物有灵")["start"]["at"], "2026-10-15T04:15:00Z")
        self.assertEqual(find_event(original, "万物有灵")["start"]["state"], "estimated")

    def test_new_version_resolves_previous_relative_deadline(self):
        original = version()
        following = copy.deepcopy(original)
        following.update(id="endfield-cn-next", events=[])
        following["maintenance"]["start"] = "2026-12-01T22:00:00Z"
        result = resolve_versions([following, original], {})
        previous = next(v for v in result if v["id"] == original["id"])
        self.assertEqual(find_event(previous, "烟火生缘")["end"]["at"], "2026-12-01T22:00:00Z")

    def test_manual_correction_wins_over_announced_time(self):
        original = version()
        event = find_event(original, "卡莉")
        result = resolve_versions([original], {event["id"]: {"end_override": "2026-11-06T00:00:00Z", "completed": True}})[0]
        updated = find_event(result, "卡莉")
        self.assertEqual(updated["end"]["state"], "manual")
        self.assertTrue(updated["completed"])


class PersistenceTests(TempStoreTest):
    def test_refresh_and_restart_preserve_user_state(self):
        data = version()
        event = find_event(data, "卡莉")
        self.store.save_version(data)
        self.store.update_state(event["id"], {"completed": True, "notes": "周末做", "end_override": "2026-11-06T00:00:00Z"})
        self.store.save_version(version())
        restarted = Store(self.directory / "calendar.sqlite3")
        updated = find_event(resolve_versions(restarted.versions(), restarted.state())[0], "卡莉")
        self.assertTrue(updated["completed"])
        self.assertEqual(updated["notes"], "周末做")
        self.assertEqual(updated["end"]["state"], "manual")

    def test_notifications_deduplicate_and_read_state_persists(self):
        self.assertTrue(self.store.add_notification("key", "event", {"title": "hello"}))
        self.assertFalse(self.store.add_notification("key", "event", {"title": "hello"}))
        self.store.read_notifications()
        self.assertTrue(Store(self.directory / "calendar.sqlite3").notifications()[0]["read"])


class CollectorTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.collector = Collector(self.store, self.directory)
        self.collector.seed()

    def test_public_list_and_html_create_live_source(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            if "api/bulletin" in url:
                return json.dumps({"code": 0, "data": {"list": [{"cid": "6094", "title": META["title"], "displayTime": 1792026000}]}})
            return "<article>" + "".join("<p>" + escape(line) + "</p>" for line in TEXT.splitlines()) + "</article>"
        self.collector.fetcher = fetcher
        result = self.collector.collect()
        self.assertEqual(len(calls), 2)
        self.assertEqual(result[0]["source"]["origin"], "live")
        self.assertEqual(len(result[0]["events"]), 24)
        self.assertEqual(self.store.get_meta("sync")["mode"], "live")

    def test_changed_list_does_not_overwrite_snapshot(self):
        old = self.store.versions()
        self.collector.fetcher = lambda url: '{"code":0,"data":{"changed":true}}'
        with self.assertRaises(ValueError):
            self.collector.collect()
        self.assertEqual(self.store.versions(), old)

    def test_empty_article_does_not_overwrite_snapshot(self):
        def fetcher(url):
            return json.dumps({"code": 0, "data": {"list": [{"cid": "6094", "title": META["title"]}]}}) if "api/bulletin" in url else "<html>结构变化</html>"
        self.collector.fetcher = fetcher
        old = self.store.versions()
        with self.assertRaises(ValueError):
            self.collector.collect()
        self.assertEqual(self.store.versions(), old)


class ReminderTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.data = version()
        self.event = find_event(self.data, "卡莉")
        self.data["events"] = [self.event]
        self.store.save_version(self.data)
        self.engine = ReminderEngine(self.store)
        self.before = stamp("2026-11-04T19:30:00Z")

    def test_only_most_urgent_threshold_fires_and_deduplicates(self):
        notices = self.engine.tick(self.before)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["threshold_minutes"], 60)
        self.assertEqual(self.engine.tick(self.before), [])

    def test_completed_activity_is_muted(self):
        self.store.update_state(self.event["id"], {"completed": True})
        self.assertEqual(self.engine.tick(self.before), [])

    def test_per_event_disable_is_respected(self):
        self.store.update_state(self.event["id"], {"reminder_minutes": []})
        self.assertEqual(self.engine.tick(self.before), [])

    def test_unknown_deadline_does_not_fire(self):
        self.event["end"].update(at=None, state="unknown")
        self.store.save_version(self.data)
        self.assertEqual(self.engine.tick(self.before), [])

    def test_estimated_deadline_does_not_fire(self):
        self.event["end"]["state"] = "estimated"
        self.store.save_version(self.data)
        self.assertEqual(self.engine.tick(self.before), [])

    def test_global_disable_is_respected(self):
        self.store.update_settings({"reminders_enabled": False})
        self.assertEqual(self.engine.tick(self.before), [])

    def test_recent_expired_deadline_is_caught_once(self):
        after = stamp("2026-11-04T20:10:00Z")
        self.assertEqual(self.engine.tick(after)[0]["threshold_minutes"], 0)
        self.assertEqual(self.engine.tick(after), [])

    def test_old_deadline_does_not_create_stale_alert(self):
        self.assertEqual(self.engine.tick(stamp("2026-11-05T20:10:00Z")), [])

    def test_changed_deadline_can_be_reminded_again(self):
        self.engine.tick(self.before)
        self.store.update_state(self.event["id"], {"end_override": "2026-11-04T20:15:00Z"})
        self.assertEqual(len(self.engine.tick(self.before)), 1)


class HttpTests(TempStoreTest):
    def setUp(self):
        super().setUp()
        self.app = Application(self.directory, auto_sync=False, desktop=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None, *, token=True, origin=None):
        headers = {}
        if token:
            headers["X-Calendar-Token"] = self.app.csrf
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_state_and_export_have_real_structured_entries(self):
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len([e for e in state["events"] if e["game"] == "endfield-cn"]), 24)
        status, exported = self.request("/api/export")
        self.assertEqual(status, 200)
        self.assertNotIn("csrf_token", exported)
        self.assertEqual(exported["rules"]["game_id"], "endfield-cn")

    def test_mutations_require_session_token(self):
        self.assertEqual(self.request("/api/settings", {"timezone": "UTC"}, token=False)[0], 403)
        self.assertEqual(self.app.store.settings()["timezone"], "auto")

    def test_ical_download_is_a_snapshot_attachment(self):
        with urllib.request.urlopen(self.base + "/api/calendar.ics") as response:
            self.assertEqual(response.headers.get_content_type(), "text/calendar")
            self.assertIn("attachment", response.headers["Content-Disposition"])
            content = response.read().decode()
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertNotIn(self.app.csrf, content)
        self.assertIn("BEGIN:VEVENT", content)

    def test_cross_origin_mutation_is_rejected(self):
        self.assertEqual(self.request("/api/settings", {"timezone": "UTC"}, origin="https://other.example")[0], 403)

    def test_valid_settings_are_saved(self):
        status, state = self.request("/api/settings", {"timezone": "Asia/Shanghai"})
        self.assertEqual(status, 200)
        self.assertEqual(state["settings"]["timezone"], "Asia/Shanghai")

    def test_invalid_time_type_is_a_validation_error(self):
        event = self.app.state()["events"][1]
        self.assertEqual(self.request("/api/events/" + event["id"], {"end_override": 123})[0], 400)

    def test_path_traversal_cannot_read_project_files(self):
        self.assertEqual(self.request("/../app.py")[0], 404)

    def test_test_reminder_is_written_to_inbox(self):
        status, state = self.request("/api/notifications/test", {})
        self.assertEqual(status, 200)
        self.assertEqual(state["notifications"][0]["kind"], "test")

    def test_future_actual_opening_is_rejected(self):
        self.assertEqual(self.request("/api/versions/endfield-cn-6094/maintenance", {"actual_end": "2099-10-15T04:00:00Z"})[0], 400)


if __name__ == "__main__":
    unittest.main()
