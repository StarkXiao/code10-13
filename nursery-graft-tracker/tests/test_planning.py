"""来年调配方案的端到端测试（基于演示数据）。"""
import tempfile
import unittest
from pathlib import Path

from grafttracker.demo import generate
from grafttracker.planning import (
    BUDS_PER_STICK,
    SAFETY_MARGIN,
    build_plan,
    default_targets,
    load_targets,
)
from grafttracker.storage import load


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = generate(Path(cls.tmp.name) / "data")
        cls.ds = load(cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bad_tree_suspended_but_environment_victim_not(self):
        plan = build_plan(self.ds, 2027)
        suspended = {t for t, _, _ in plan.suspended_trees}
        # M-沃柑-07：正常批次也仅约四成 → 亲和性风险，停采
        self.assertIn("M-沃柑-07", suspended)
        # M-爱媛38-05：同批香橙组合 83% 而它 33% → 停采
        self.assertIn("M-爱媛38-05", suspended)
        # M-砂糖橘-09：倒春寒批次全军覆没（最优对照也 <70%）→ 环境主导，不惩罚
        self.assertNotIn("M-砂糖橘-09", suspended)
        self.assertNotIn("M-砂糖橘-02", suspended)

    def test_allocation_prefers_best_tree_first(self):
        plan = build_plan(
            self.ds, 2027,
            targets={("沃柑", "枳壳"): 300},
        )
        lines = [l for l in plan.lines if l.cultivar == "沃柑"]
        self.assertTrue(lines)
        # 第一优先级必须是表现最好的 M-沃柑-03，且证据为实测
        self.assertEqual(lines[0].mother_tree_id, "M-沃柑-03")
        self.assertEqual(lines[0].evidence, "实测")
        # 停采母树不参与
        self.assertFalse(
            any(l.mother_tree_id == "M-沃柑-07" for l in lines)
        )

    def test_stick_math_rounds_up_with_safety_margin(self):
        qty = 100
        plan = build_plan(self.ds, 2027, targets={("沃柑", "枳壳"): qty})
        need_grafts = round(qty * SAFETY_MARGIN)
        need_sticks = -(-need_grafts // BUDS_PER_STICK)
        self.assertEqual(
            sum(l.grafts_planned for l in plan.lines if l.cultivar == "沃柑"),
            need_grafts,
        )
        # 穗条向上取整，可能略多于理论值（最后一根用不满）
        self.assertGreaterEqual(
            sum(l.sticks for l in plan.lines if l.cultivar == "沃柑"),
            need_sticks,
        )

    def test_capacity_respected(self):
        plan = build_plan(self.ds, 2027, targets={("沃柑", "枳壳"): 100})
        for u in plan.tree_uses.values():
            self.assertLessEqual(u.sticks_used, u.sticks_capacity)
            self.assertGreaterEqual(u.sticks_used, 0)

    def test_deficit_when_demand_exceeds_capacity(self):
        # 沃柑可采母树：03=420 + 12=500 = 920 根 ≈ 2760 株（07 停采）
        plan = build_plan(self.ds, 2027, targets={("沃柑", "枳壳"): 5000})
        self.assertTrue(plan.deficits)
        d = next(d for d in plan.deficits if d.cultivar == "沃柑")
        # 分配量不超过产能
        allocated = sum(
            l.grafts_planned for l in plan.lines if l.cultivar == "沃柑"
        )
        self.assertEqual(allocated + d.grafts_short, round(5000 * SAFETY_MARGIN))

    def test_unproven_tree_used_with_prior_evidence(self):
        plan = build_plan(self.ds, 2027, targets={("沃柑", "枳壳"): 2000})
        lines_12 = [l for l in plan.lines if l.mother_tree_id == "M-沃柑-12"]
        self.assertTrue(lines_12, "产能不足时应动用无记录的新建母树")
        self.assertEqual(lines_12[0].evidence, "无记录（先验50%）")

    def test_default_targets_match_this_year_volume(self):
        targets = default_targets(self.ds)
        reviewed = sum(
            1 for g in self.ds.grafts if g.status.value != "pending"
        )
        self.assertEqual(sum(targets.values()), reviewed)

    def test_window_advice_present_and_ordered(self):
        plan = build_plan(self.ds, 2027)
        self.assertEqual({w.field for w in plan.windows}, {"F-东1", "F-西2", "F-南3"})
        for w in plan.windows:
            self.assertEqual(w.suggested_start.year, 2027)
        # F-西2 4 月上旬有热浪，建议起点应避开 4/8 前后
        west = next(w for w in plan.windows if w.field == "F-西2")
        self.assertFalse(
            west.suggested_start.month == 4 and west.suggested_start.day in range(6, 12)
        )

    def test_load_targets_csv(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "targets.csv"
        targets = load_targets(str(path))
        self.assertEqual(targets[("沃柑", "枳壳")], 900)
        plan = build_plan(self.ds, 2027, targets=targets)
        self.assertTrue(plan.lines)


if __name__ == "__main__":
    unittest.main()
