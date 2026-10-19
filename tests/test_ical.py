import datetime as dt
import json
import unittest

from acg_calendar.ical import calendar_bytes
from acg_calendar.parser import ROOT, resolve_versions


class CalendarExportTests(unittest.TestCase):
    def setUp(self):
        self.version = resolve_versions([json.loads((ROOT / "samples/endfield-cn-6094.parsed.json").read_text(encoding="utf-8"))], {})[0]
        self.event = next(e for e in self.version["events"] if "卡莉的芳菲童话" in e["title"])
        self.version["events"] = [self.event]
        self.settings = {"reminders_enabled": True, "reminder_offsets": [1440, 60]}

    def export(self):
        return calendar_bytes([self.version], self.settings, dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)).decode()

    def test_real_event_uses_utc_dates_and_end_relative_alarms(self):
        value = self.export()
        self.assertIn("DTSTART:20261022T040000Z", value)
        self.assertIn("DTEND:20261104T200000Z", value)
        self.assertIn("TRIGGER;RELATED=END:-PT60M", value)
        self.assertIn("TRANSP:TRANSPARENT", value)

    def test_unknown_or_estimated_windows_are_not_invented(self):
        for end in ({"at": None, "state": "unknown"}, {"at": self.event["end"]["at"], "state": "estimated"}):
            self.event["end"] = end
            value = self.export()
            self.assertNotIn("BEGIN:VEVENT", value)
            self.assertIn("X-PLAYTIME-SKIPPED:1", value)

    def test_completed_and_ignored_events_are_excluded(self):
        for flag in ("completed", "ignored"):
            self.event[flag] = True
            self.assertNotIn("BEGIN:VEVENT", self.export())
            self.event[flag] = False

    def test_unicode_folding_and_text_escape_prevent_property_injection(self):
        self.event["title"] = "长标题" * 60 + ",;\\\nBEGIN:VEVENT"
        raw = self.export()
        self.assertTrue(all(len(line.encode()) <= 75 for line in raw.split("\r\n")))
        unfolded = raw.replace("\r\n ", "")
        self.assertIn("SUMMARY:" + "长标题" * 60 + "\\,\\;\\\\\\nBEGIN:VEVENT", unfolded)
        self.assertEqual(sum(line == "BEGIN:VEVENT" for line in raw.split("\r\n")), 1)

    def test_manual_correction_keeps_uid_and_respects_reminder_override(self):
        first = self.export()
        self.event["end"].update(at="2026-11-05T20:00:00Z", state="manual")
        self.event["reminder_minutes"] = []
        changed = self.export()
        uid = next(line for line in first.splitlines() if line.startswith("UID:"))
        self.assertIn(uid, changed)
        self.assertIn("DTEND:20261105T200000Z", changed)
        self.assertNotIn("VALARM", changed)

    def test_point_update_has_no_fictional_duration_or_deadline_alarm(self):
        self.event.update(time_kind="point")
        self.event["end"].update(at=None, state="unknown")
        value = self.export()
        self.assertIn("BEGIN:VEVENT", value)
        self.assertNotIn("DTEND", value)
        self.assertNotIn("VALARM", value)


