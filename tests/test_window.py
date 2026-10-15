"""收录时间窗口测试：边界、时区、缺失值、超出当前时间。"""

from __future__ import annotations

import unittest
from datetime import datetime

from zhixianji.window import CN_TZ, TimeWindow, parse_site_time

AS_OF = datetime(2026, 10, 15, 19, 0, tzinfo=CN_TZ)


class ParseSiteTimeTest(unittest.TestCase):
    def test_iso_with_offset(self) -> None:
        self.assertEqual(
            parse_site_time("2026-10-15T18:06:10+08:00"),
            datetime(2026, 10, 15, 18, 6, 10, tzinfo=CN_TZ),
        )

    def test_date_only_is_start_of_day_in_site_timezone(self) -> None:
        self.assertEqual(parse_site_time("2026-10-10"), datetime(2026, 10, 10, 0, 0, tzinfo=CN_TZ))

    def test_naive_datetime_assumes_site_timezone(self) -> None:
        self.assertEqual(parse_site_time("2026-10-10T08:00:00"), datetime(2026, 10, 10, 8, 0, tzinfo=CN_TZ))

    def test_missing_or_broken_values_return_none(self) -> None:
        for value in (None, "", "   ", "不是日期", "2026-13-45"):
            self.assertIsNone(parse_site_time(value), msg=repr(value))


class TimeWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = TimeWindow("first_seen_at", 30, as_of=AS_OF)

    def test_bounds(self) -> None:
        self.assertEqual(self.window.start, datetime(2026, 9, 15, 19, 0, tzinfo=CN_TZ))

    def test_inside_window(self) -> None:
        decision = self.window.evaluate({"first_seen_at": "2026-10-01T10:00:00+08:00"})
        self.assertTrue(decision.in_window)
        self.assertEqual(decision.reason, "in_window")

    def test_exactly_at_start_is_inclusive(self) -> None:
        decision = self.window.evaluate({"first_seen_at": "2026-09-15T19:00:00+08:00"})
        self.assertTrue(decision.in_window)

    def test_one_second_before_start_is_excluded(self) -> None:
        decision = self.window.evaluate({"first_seen_at": "2026-09-15T18:59:59+08:00"})
        self.assertFalse(decision.in_window)
        self.assertEqual(decision.reason, "before_window")

    def test_missing_date_is_not_silently_included(self) -> None:
        for value in (None, "", "unknown"):
            decision = self.window.evaluate({"first_seen_at": value})
            self.assertFalse(decision.in_window)
            self.assertEqual(decision.reason, "missing_date")

    def test_future_date_is_excluded_and_reported(self) -> None:
        decision = self.window.evaluate({"first_seen_at": "2026-10-16T00:00:00+08:00"})
        self.assertFalse(decision.in_window)
        self.assertEqual(decision.reason, "after_as_of")

    def test_field_is_configurable(self) -> None:
        window = TimeWindow("published_at", 30, as_of=AS_OF)
        decision = window.evaluate({"first_seen_at": "2026-08-03T18:00:00+08:00", "published_at": "2026-10-01"})
        self.assertTrue(decision.in_window)

    def test_window_json_records_the_exact_bounds(self) -> None:
        payload = self.window.to_json()
        self.assertEqual(payload["field"], "first_seen_at")
        self.assertEqual(payload["days"], 30)
        self.assertEqual(payload["startInclusive"], "2026-09-15T19:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
