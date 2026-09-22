"""数据加载与交叉校验测试。"""
import csv
import tempfile
import unittest
from pathlib import Path

from grafttracker.storage import DataError, load

BATCH_HEADER = ["code", "field", "graft_date", "method", "operator", "note"]
GRAFT_HEADER = ["plant_id", "batch_code", "rootstock", "scion_cultivar",
                "mother_tree_id", "status", "review_date", "note"]
ENV_HEADER = ["field", "day", "temp_mean_c", "temp_min_c", "temp_max_c",
              "humidity_mean_pct", "rain_mm"]
TREE_HEADER = ["tree_id", "cultivar", "location", "age_years",
               "usable_sticks", "health_note"]


class StorageFixture:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def cleanup(self):
        self._tmp.cleanup()

    def write(self, name: str, header: list[str], rows: list[list]):
        with (self.dir / name).open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    def write_minimal(self, *, graft_rows=None, env_rows=None, tree_rows=None,
                      batch_rows=None):
        self.write("batches.csv", BATCH_HEADER, batch_rows if batch_rows is not None else [
            ["B1", "F1", "2026-03-10", "cleft", "一组", ""],
        ])
        self.write("grafts.csv", GRAFT_HEADER, graft_rows if graft_rows is not None else [[
            "P1", "B1", "枳壳", "沃柑", "T1", "alived", "2026-04-09", "",
        ]])
        self.write("environment.csv", ENV_HEADER, env_rows if env_rows is not None else [[
            "F1", "2026-03-10", "22.0", "17.0", "27.0", "70", "0",
        ]])
        self.write("mother_trees.csv", TREE_HEADER, tree_rows if tree_rows is not None else [[
            "T1", "沃柑", "母本园", "6.0", "100", "",
        ]])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.fx = StorageFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_minimal_loads(self):
        self.fx.write_minimal()
        ds = load(self.fx.dir)
        self.assertEqual(len(ds.grafts), 1)

    def test_missing_file(self):
        self.fx.write_minimal()
        (self.fx.dir / "grafts.csv").unlink()
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_unknown_batch_reference(self):
        self.fx.write_minimal(graft_rows=[[
            "P1", "B9", "枳壳", "沃柑", "T1", "alived", "2026-04-09", "",
        ]])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_unknown_mother_tree(self):
        self.fx.write_minimal(graft_rows=[[
            "P1", "B1", "枳壳", "沃柑", "T9", "alived", "2026-04-09", "",
        ]])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_cultivar_mismatch(self):
        self.fx.write_minimal()
        self.fx.write("mother_trees.csv", TREE_HEADER, [
            ["T1", "砂糖橘", "母本园", "6.0", "100", ""],
        ])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_duplicate_plant_id(self):
        self.fx.write_minimal(graft_rows=[
            ["P1", "B1", "枳壳", "沃柑", "T1", "alived", "2026-04-09", ""],
            ["P1", "B1", "枳壳", "沃柑", "T1", "dead", "2026-04-09", ""],
        ])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_bad_enum_and_date(self):
        self.fx.write_minimal(batch_rows=[
            ["B1", "F1", "2026/03/10", "cleft", "一组", ""],
        ])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_humidity_range(self):
        self.fx.write_minimal(env_rows=[[
            "F1", "2026-03-10", "22.0", "17.0", "27.0", "120", "0",
        ]])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_env_field_not_used(self):
        self.fx.write_minimal(env_rows=[
            ["F1", "2026-03-10", "22.0", "17.0", "27.0", "70", "0"],
            ["F9", "2026-03-10", "22.0", "17.0", "27.0", "70", "0"],
        ])
        with self.assertRaises(DataError):
            load(self.fx.dir)

    def test_pending_without_review_date_defaults(self):
        self.fx.write_minimal(graft_rows=[[
            "P1", "B1", "枳壳", "沃柑", "T1", "pending", "", "",
        ]])
        ds = load(self.fx.dir)
        self.assertEqual(ds.grafts[0].review_date, ds.batches["B1"].review_date)


if __name__ == "__main__":
    unittest.main()
