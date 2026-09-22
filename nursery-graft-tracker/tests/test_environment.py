"""温湿度胁迫识别与愈合窗口测试。"""
import unittest
from datetime import date

from grafttracker.environment import (
    DEFAULT_HEALING_DAYS,
    count_stress_days,
    healing_window_days,
    readings_in_window,
    summarize_window,
)
from grafttracker.models import Batch, EnvironmentReading, GraftMethod


def _r(day: date, t: float, h: float = 70.0, rain: float = 0.0) -> EnvironmentReading:
    return EnvironmentReading(field="F", day=day, temp_mean_c=t,
                              humidity_mean_pct=h, rain_mm=rain)


def _batch(method=GraftMethod.CLEFT, graft_date=date(2026, 3, 10)):
    return Batch(code="B1", field="F", graft_date=graft_date,
                 method=method, operator="x")


class StressTests(unittest.TestCase):
    def test_healing_window_length(self):
        self.assertEqual(healing_window_days(_batch(GraftMethod.CLEFT)), 30)
        self.assertEqual(healing_window_days(_batch(GraftMethod.BUD)), 21)

    def test_high_temp_low_humidity_day(self):
        days, detail = count_stress_days([_r(date(2026, 3, 10), 33.0, 55.0)])
        self.assertEqual(days, 1)
        self.assertIn("高温低湿", "".join(detail))

    def test_low_temp_day(self):
        days, detail = count_stress_days([_r(date(2026, 3, 10), 11.0, 80.0)])
        self.assertEqual(days, 1)
        self.assertIn("低温", "".join(detail))

    def test_heavy_rain_day(self):
        days, _ = count_stress_days([_r(date(2026, 3, 10), 22.0, 90.0, rain=30.0)])
        self.assertEqual(days, 1)

    def test_comfortable_day_not_stress(self):
        days, detail = count_stress_days([_r(date(2026, 3, 10), 24.0, 72.0)])
        self.assertEqual(days, 0)
        self.assertEqual(detail, [])

    def test_dry_hot_run_needs_three_consecutive_days(self):
        # 30℃/65% 单日不触发任何规则，连续 3 天才标记干热连段
        series = [_r(date(2026, 3, 10 + i), 30.0, 65.0) for i in range(3)]
        days, detail = count_stress_days(series)
        self.assertEqual(days, 3)
        self.assertIn("干热连段", "".join(detail))

        series2 = [_r(date(2026, 3, 10 + i), 30.0, 65.0) for i in range(2)]
        self.assertEqual(count_stress_days(series2)[0], 0)

    def test_window_inclusive_range(self):
        b = _batch(graft_date=date(2026, 3, 10))
        series = [
            _r(date(2026, 3, 9), 20),   # 窗口前
            _r(date(2026, 3, 10), 20),  # 嫁接日（含）
            _r(date(2026, 4, 8), 20),   # 嫁接日+29 = 第 30 天（含）
            _r(date(2026, 4, 9), 20),   # 窗口后
        ]
        window = readings_in_window(b, series)
        self.assertEqual(len(window), 2)

    def test_summarize_handles_missing_readings(self):
        t, h, days, share, detail, span = summarize_window(_batch(), [])
        self.assertNotEqual(t, t)  # NaN
        self.assertEqual(days, 0)
        self.assertEqual(detail, "无环境读数")
        self.assertEqual(span, DEFAULT_HEALING_DAYS)


if __name__ == "__main__":
    unittest.main()
