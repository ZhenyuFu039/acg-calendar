import datetime as dt
import unittest

from extract_probe_events import normalize_window


class TimeExtractionTests(unittest.TestCase):
    def test_chinese_range_and_utc_conversion(self):
        row = normalize_window("活动时间：10月16日 16:00 - 10月30日 03:59", 2026, 8)
        self.assertEqual(row["start"]["utc"], "2026-10-16T08:00:00+00:00")
        self.assertEqual(row["end"]["utc"], "2026-10-29T19:59:00+00:00")
        self.assertIn("year inferred from announcement publication year", row["review_reasons"])

    def test_explicit_cross_year_end_is_preserved(self):
        row = normalize_window("10月26日 04:00 - 2027年01月25日 03:59", 2026, 8)
        self.assertEqual(row["end"]["source"], "2027-01-25T03:59:00+08:00")

    def test_maintenance_start_is_not_invented(self):
        row = normalize_window("活动时间：版本更新后 - 2026/11/05 11:59（服务器时间）", 2026, 8)
        self.assertIsNone(row["start"])
        self.assertEqual(row["end"]["source"], "2026-11-05T11:59:00+08:00")

    def test_maintenance_only_range_stays_unresolved(self):
        row = normalize_window("活动时间10月15日维护后~11月5日维护", 2026, 8)
        self.assertIsNone(row["start"])
        self.assertIsNone(row["end"])

    def test_english_seconds_and_explicit_offset(self):
        row = normalize_window("From October 17, 2026, 5:00:00 to October 19, 2026, 4:59:59 (UTC+9)", 2026, 9)
        self.assertEqual(row["start"]["utc"], "2026-10-16T20:00:00+00:00")
        self.assertEqual(row["end"]["utc"], "2026-10-18T19:59:59+00:00")
        self.assertEqual(row["review_reasons"], [])

    def test_reverse_english_end_format(self):
        row = normalize_window("From the end of the October 8, 2026 maintenance to 23:59:59, October 28, 2026 (UTC+9)", 2026, 9)
        self.assertIsNone(row["start"])
        self.assertEqual(row["end"]["source"], "2026-10-28T23:59:59+09:00")

    def test_new_york_changes_offset_across_dst(self):
        row = normalize_window("2026/10/29 12:00 - 2026/11/19 11:59（UTC+8）", 2026, 8)
        self.assertEqual(row["start"]["new_york"], "2026-10-29T00:00:00-04:00")
        self.assertEqual(row["end"]["new_york"], "2026-11-18T22:59:00-05:00")

    def test_explicit_reversed_range_is_rejected(self):
        row = normalize_window("2026/11/19 12:00 - 2026/10/29 04:00（UTC+8）", 2026, 8)
        self.assertIsNone(row["start"])
        self.assertIsNone(row["end"])
        self.assertIn("non-positive interval", row["review_reasons"])


if __name__ == "__main__":
    unittest.main()
