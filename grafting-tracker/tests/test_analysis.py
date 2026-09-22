"""成活率统计与低成活识别的单元测试。"""
import unittest

from grafting_tracker import db as gdb
from grafting_tracker.analysis import (STATUS_FEW, STATUS_GOOD, STATUS_LOW,
                                       load_batch_env_stats, load_combo_stats,
                                       wilson_interval)


def _conn():
    return gdb.init_db(":memory:")


class WilsonIntervalTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_known_value(self):
        low, high = wilson_interval(50, 100)
        self.assertAlmostEqual(low, 0.4038, places=3)
        self.assertAlmostEqual(high, 0.5962, places=3)

    def test_bounds(self):
        low, high = wilson_interval(200, 200)
        self.assertGreater(low, 0.9)
        self.assertLessEqual(high, 1.0)
        low, high = wilson_interval(0, 200)
        self.assertGreaterEqual(low, 0.0)
        self.assertLess(high, 0.1)


class ComboStatsTest(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()
        c = gdb.get_or_create(self.conn, "cultivar", "品种甲")
        r_good = gdb.get_or_create(self.conn, "rootstock", "砧木优")
        r_bad = gdb.get_or_create(self.conn, "rootstock", "砧木差")
        r_new = gdb.get_or_create(self.conn, "rootstock", "砧木新")
        # 优组合：210 嫁接 179 成活（两批，其中一批两次调查，验证取最新）
        b1 = gdb.add_batch(self.conn, "B1", c, r_good, "切接", "2026-03-15", 200)
        gdb.add_survey(self.conn, b1, "2026-04-30", 180)
        gdb.add_survey(self.conn, b1, "2026-06-15", 170)  # 最新调查定案
        b2 = gdb.add_batch(self.conn, "B2", c, r_good, "劈接", "2026-04-01", 10)
        gdb.add_survey(self.conn, b2, "2026-05-20", 9)
        # 差组合：200 嫁接 100 成活
        b3 = gdb.add_batch(self.conn, "B3", c, r_bad, "切接", "2026-03-15", 200)
        gdb.add_survey(self.conn, b3, "2026-05-01", 100)
        # 小样本组合：10 嫁接 9 成活
        b4 = gdb.add_batch(self.conn, "B4", c, r_new, "切接", "2026-03-20", 10)
        gdb.add_survey(self.conn, b4, "2026-05-05", 9)
        self.stats = {s.rootstock: s
                      for s in load_combo_stats(self.conn, min_samples=30)}

    def test_low_combo_detected(self):
        s = self.stats["砧木差"]
        self.assertEqual(s.status, STATUS_LOW)
        self.assertAlmostEqual(s.rate, 0.50)

    def test_good_combo_detected(self):
        s = self.stats["砧木优"]
        self.assertEqual(s.status, STATUS_GOOD)
        # 最新调查定案：170 + 9 = 179 / 210
        self.assertEqual(s.alive, 179)
        self.assertEqual(s.grafted, 210)

    def test_small_sample_flagged(self):
        self.assertEqual(self.stats["砧木新"].status, STATUS_FEW)

    def test_survey_validation(self):
        c = gdb.get_or_create(self.conn, "cultivar", "品种乙")
        r = gdb.get_or_create(self.conn, "rootstock", "砧木乙")
        b = gdb.add_batch(self.conn, "B9", c, r, "切接", "2026-03-10", 50)
        with self.assertRaises(ValueError):
            gdb.add_survey(self.conn, b, "2026-05-01", 51)  # 成活 > 嫁接


class EnvWindowTest(unittest.TestCase):
    def test_healing_window_aggregation(self):
        conn = _conn()
        c = gdb.get_or_create(conn, "cultivar", "品种甲")
        r = gdb.get_or_create(conn, "rootstock", "砧木甲")
        p = gdb.get_or_create(conn, "plot", "一号棚")
        b = gdb.add_batch(conn, "B1", c, r, "切接", "2026-04-01", 100, p)
        gdb.add_survey(conn, b, "2026-05-20", 80)
        # 窗口 04-01 ~ 04-14（14 天）；04-15 之后的记录不应计入
        for day in range(1, 21):
            d = f"2026-04-{day:02d}"
            hot = day in (5, 10)          # 窗口内 2 个高温日
            late_hot = day == 18          # 窗口外高温日，不应计入
            gdb.upsert_env(conn, p, d, 20.0,
                           35.0 if (hot or late_hot) else 25.0,
                           12.0, 70.0)
        (s,) = load_batch_env_stats(conn, healing_days=14)
        self.assertEqual(s.days, 14)
        self.assertEqual(s.hot_days, 2)
        self.assertAlmostEqual(s.rate, 0.8)


if __name__ == "__main__":
    unittest.main()
