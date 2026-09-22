"""穗条调配方案的单元测试。"""
import math
import unittest

from grafting_tracker import db as gdb
from grafting_tracker.analysis import load_combo_stats, wilson_interval
from grafting_tracker.planning import (FLAG_ALL_LOW, FLAG_NO_DATA, FLAG_OK,
                                       build_plan, rootstock_summary)


def _conn():
    return gdb.init_db(":memory:")


def _add_combo(conn, cultivar, rootstock, grafted, alive, batch_no):
    c = gdb.get_or_create(conn, "cultivar", cultivar)
    r = gdb.get_or_create(conn, "rootstock", rootstock)
    b = gdb.add_batch(conn, batch_no, c, r, "切接", "2026-03-15", grafted)
    gdb.add_survey(conn, b, "2026-05-01", alive)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()
        # 品种甲：优组合 90%，差组合 50%
        _add_combo(self.conn, "品种甲", "砧木优", 200, 180, "B1")
        _add_combo(self.conn, "品种甲", "砧木差", 200, 100, "B2")
        # 品种乙：两个组合都低成活
        _add_combo(self.conn, "品种乙", "砧木差", 200, 100, "B3")
        _add_combo(self.conn, "品种乙", "砧木丙", 200, 110, "B4")
        # 品种丙：只有目标，无历史数据
        c = gdb.get_or_create(self.conn, "cultivar", "品种丙")
        gdb.set_scion_spec(self.conn, c, grafts_per_scion=4.0,
                           target_plants=800)
        # 品种甲指定目标 1000 株
        cid = gdb.get_or_create(self.conn, "cultivar", "品种甲")
        gdb.set_scion_spec(self.conn, cid, grafts_per_scion=4.0,
                           target_plants=1000)
        self.stats = load_combo_stats(self.conn)
        self.rows = {r.cultivar: r for r in build_plan(
            self.conn, self.stats, growth=0.2, buffer=0.1,
            default_rate=0.5, default_grafts_per_scion=4.0)}

    def test_low_combo_excluded(self):
        row = self.rows["品种甲"]
        self.assertEqual(row.flag, FLAG_OK)
        self.assertEqual(row.rootstock, "砧木优")  # 不选低成活的砧木差

    def test_scion_math(self):
        row = self.rows["品种甲"]
        expected = wilson_interval(180, 200)[0]
        self.assertAlmostEqual(row.expected_rate, expected)
        grafts = math.ceil(1000 / expected * 1.1)
        self.assertEqual(row.grafts_needed, grafts)
        self.assertEqual(row.scions_needed, math.ceil(grafts / 4.0))

    def test_all_low_flagged(self):
        row = self.rows["品种乙"]
        self.assertEqual(row.flag, FLAG_ALL_LOW)
        self.assertEqual(row.rootstock, "砧木丙")  # 取两者中较好的
        self.assertIn("全部组合低成活", row.note)

    def test_no_data_uses_default(self):
        row = self.rows["品种丙"]
        self.assertEqual(row.flag, FLAG_NO_DATA)
        self.assertIsNone(row.rootstock)
        self.assertEqual(row.expected_rate, 0.5)
        self.assertEqual(row.target_plants, 800)

    def test_growth_fallback_target(self):
        # 品种乙未设目标：今年嫁接 400 株 × 1.2 = 480
        self.assertEqual(self.rows["品种乙"].target_plants, 480)

    def test_rootstock_summary(self):
        summary = rootstock_summary(list(self.rows.values()))
        self.assertIn("砧木优", summary)
        self.assertEqual(summary["砧木优"], self.rows["品种甲"].grafts_needed)


if __name__ == "__main__":
    unittest.main()
