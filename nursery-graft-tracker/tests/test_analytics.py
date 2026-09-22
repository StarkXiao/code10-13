"""Wilson 置信区间与组合统计口径测试。"""
import unittest

from grafttracker.analytics import (
    aggregate,
    low_survival,
    overall,
    wilson_lower,
)
from grafttracker.demo import generate
from grafttracker.storage import load


class WilsonTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(wilson_lower(0, 0), 0.0)
        self.assertAlmostEqual(wilson_lower(10, 10), 0.722, places=2)
        self.assertAlmostEqual(wilson_lower(0, 10), 0.0, places=10)

    def test_small_sample_lower_bound_is_conservative(self):
        # 5 株活 4 株，点估计 80%，但下界远低于 80%
        self.assertLess(wilson_lower(4, 5), 0.50)
        # 样本变大，下界向点估计收敛
        self.assertGreater(wilson_lower(80, 100), 0.70)

    def test_monotonic_in_alive(self):
        self.assertLess(wilson_lower(5, 10), wilson_lower(6, 10))


class DemoAggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path

        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = generate(Path(cls.tmp.name) / "data")
        cls.ds = load(cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_overall_counts_exclude_pending(self):
        o = overall(self.ds)
        self.assertEqual(o["pending"], 1)
        self.assertEqual(o["reviewed"] + o["pending"], len(self.ds.grafts))
        self.assertEqual(o["alive"] + (o["reviewed"] - o["alive"]), o["reviewed"])

    def test_low_survival_includes_known_bad_tree(self):
        lows = low_survival(self.ds)
        bad = [s for s in lows if s.mother_tree_id == "M-沃柑-07"]
        self.assertTrue(bad, "M-沃柑-07 应被识别为低成活母树")
        for s in bad:
            self.assertLess(s.rate, 0.70)

    def test_small_sample_not_flagged(self):
        # 所有演示组合样本均 ≥5 或全部 ≥12，确认没有 <5 的组合被误判
        for s in aggregate(self.ds):
            if s.total < 5:
                self.assertFalse(s.low_survival)

    def test_stats_sorted_by_lower_bound(self):
        stats = aggregate(self.ds)
        bounds = [s.wilson_low for s in stats]
        self.assertEqual(bounds, sorted(bounds))

    def test_good_combo_top_rated(self):
        stats = aggregate(self.ds)
        good = next(
            s for s in stats
            if s.mother_tree_id == "M-沃柑-03" and s.rootstock == "枳壳"
            and "B2026-0310-A" in s.batches
        )
        # 跨正常批（91% 真值）与热浪批（62% 真值），聚合后仍应明显优于问题母树
        self.assertGreaterEqual(good.rate, 0.70)
        bad = next(
            s for s in stats
            if s.mother_tree_id == "M-沃柑-07" and s.rootstock == "枳壳"
        )
        self.assertGreater(good.rate, bad.rate + 0.30)


if __name__ == "__main__":
    unittest.main()
