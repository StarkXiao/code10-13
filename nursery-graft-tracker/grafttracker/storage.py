"""CSV 存储层：加载与校验四份基础数据。

数据目录默认 ./data（可用 --data-dir 覆盖）：

  batches.csv      嫁接批次
  grafts.csv       单株嫁接与复检记录
  environment.csv  地块逐日温湿度
  mother_trees.csv 采穗母树台账

全部为 UTF-8、首行表头。日期一律 YYYY-MM-DD。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .models import (
    Batch,
    EnvironmentReading,
    GraftMethod,
    GraftRecord,
    MotherTree,
    SurvivalStatus,
)

REQUIRED_FILES = ("batches.csv", "grafts.csv", "environment.csv", "mother_trees.csv")


class DataError(ValueError):
    """数据文件格式/引用完整性错误。"""


def _parse_date(raw: str, *, context: str) -> date:
    raw = (raw or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DataError(f"{context}：日期应为 YYYY-MM-DD，实际为 {raw!r}") from exc


def _parse_float(raw: str, *, context: str, default: float | None = None) -> float:
    raw = (raw or "").strip()
    if not raw and default is not None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise DataError(f"{context}：应为数字，实际为 {raw!r}") from exc


def _parse_int(raw: str, *, context: str) -> int:
    raw = (raw or "").strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise DataError(f"{context}：应为整数，实际为 {raw!r}") from exc


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise DataError(f"缺少数据文件：{path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Dataset:
    batches: dict[str, Batch]
    grafts: list[GraftRecord]
    readings: list[EnvironmentReading]
    mother_trees: dict[str, MotherTree]

    def find_batch(self, code: str) -> Batch:
        if code not in self.batches:
            raise DataError(f"单株记录引用了不存在的批次号：{code}")
        return self.batches[code]

    def env_index(self) -> dict[str, list[EnvironmentReading]]:
        idx: dict[str, list[EnvironmentReading]] = {}
        for r in self.readings:
            idx.setdefault(r.field, []).append(r)
        for seq in idx.values():
            seq.sort(key=lambda r: r.day)
        return idx


def load(data_dir: str | Path) -> Dataset:
    """加载并交叉校验整个数据目录。"""
    base = Path(data_dir)
    batches = _load_batches(base / "batches.csv")
    grafts = _load_grafts(base / "grafts.csv", batches)
    readings = _load_environment(base / "environment.csv", batches)
    trees = _load_mother_trees(base / "mother_trees.csv")

    # 穗条来源必须能在母树台账中找到（品种也要对得上）
    for g in grafts:
        tree = trees.get(g.mother_tree_id)
        if tree is None:
            raise DataError(
                f"植株 {g.plant_id} 的穗条母树 {g.mother_tree_id} 不在 mother_trees.csv"
            )
        if tree.cultivar != g.scion_cultivar:
            raise DataError(
                f"植株 {g.plant_id}：母树 {g.mother_tree_id} 品种为 {tree.cultivar}，"
                f"与接穗品种 {g.scion_cultivar} 不符"
            )

    return Dataset(batches=batches, grafts=grafts, readings=readings, mother_trees=trees)


def _load_batches(path: Path) -> dict[str, Batch]:
    out: dict[str, Batch] = {}
    for i, row in enumerate(_rows(path), start=2):
        ctx = f"batches.csv 第{i}行"
        code = (row.get("code") or "").strip()
        if not code:
            raise DataError(f"{ctx}：批次号 code 为空")
        method_raw = (row.get("method") or "").strip()
        try:
            method = GraftMethod(method_raw)
        except ValueError as exc:
            raise DataError(
                f"{ctx}：未知嫁接方法 {method_raw!r}，"
                f"可选 {[m.value for m in GraftMethod]}"
            ) from exc
        if code in out:
            raise DataError(f"{ctx}：批次号 {code} 重复")
        out[code] = Batch(
            code=code,
            field=(row.get("field") or "").strip(),
            graft_date=_parse_date(row.get("graft_date", ""), context=ctx),
            method=method,
            operator=(row.get("operator") or "").strip(),
            note=(row.get("note") or "").strip(),
        )
    if not out:
        raise DataError("batches.csv 没有任何批次记录")
    return out


def _load_grafts(path: Path, batches: dict[str, Batch]) -> list[GraftRecord]:
    out: list[GraftRecord] = []
    seen: set[str] = set()
    for i, row in enumerate(_rows(path), start=2):
        ctx = f"grafts.csv 第{i}行"
        plant_id = (row.get("plant_id") or "").strip()
        if not plant_id:
            raise DataError(f"{ctx}：植株编号 plant_id 为空")
        if plant_id in seen:
            raise DataError(f"{ctx}：植株编号 {plant_id} 重复")
        seen.add(plant_id)

        batch_code = (row.get("batch_code") or "").strip()
        batch = batches.get(batch_code)
        if batch is None:
            raise DataError(f"{ctx}：引用了不存在的批次号 {batch_code}")

        status_raw = (row.get("status") or "").strip()
        try:
            status = SurvivalStatus(status_raw)
        except ValueError as exc:
            raise DataError(
                f"{ctx}：未知状态 {status_raw!r}，"
                f"可选 {[s.value for s in SurvivalStatus]}"
            ) from exc

        review_raw = (row.get("review_date") or "").strip()
        review_date = (
            _parse_date(review_raw, context=ctx)
            if review_raw
            else batch.review_date
        )
        if status is not SurvivalStatus.PENDING and review_date < batch.graft_date:
            raise DataError(f"{ctx}：复检日期早于嫁接日期")

        out.append(
            GraftRecord(
                plant_id=plant_id,
                batch_code=batch_code,
                rootstock=(row.get("rootstock") or "").strip(),
                scion_cultivar=(row.get("scion_cultivar") or "").strip(),
                mother_tree_id=(row.get("mother_tree_id") or "").strip(),
                status=status,
                graft_date=batch.graft_date,
                review_date=review_date,
                note=(row.get("note") or "").strip(),
            )
        )
    if not out:
        raise DataError("grafts.csv 没有任何植株记录")
    return out


def _load_environment(
    path: Path, batches: dict[str, Batch]
) -> list[EnvironmentReading]:
    out: list[EnvironmentReading] = []
    seen: set[tuple[str, date]] = set()
    fields_used = {b.field for b in batches.values()}
    for i, row in enumerate(_rows(path), start=2):
        ctx = f"environment.csv 第{i}行"
        field_id = (row.get("field") or "").strip()
        if field_id not in fields_used:
            raise DataError(f"{ctx}：地块 {field_id} 没有任何批次使用，请核对编号")
        day = _parse_date(row.get("day", ""), context=ctx)
        key = (field_id, day)
        if key in seen:
            raise DataError(f"{ctx}：地块 {field_id} 在 {day} 有重复读数")
        seen.add(key)

        humidity = _parse_float(row.get("humidity_mean_pct", ""), context=ctx)
        if not 0 <= humidity <= 100:
            raise DataError(f"{ctx}：湿度应在 0~100 之间，实际为 {humidity}")
        temp = _parse_float(row.get("temp_mean_c", ""), context=ctx)

        def _opt_float(col: str) -> float | None:
            raw = (row.get(col) or "").strip()
            return float(raw) if raw else None

        out.append(
            EnvironmentReading(
                field=field_id,
                day=day,
                temp_mean_c=temp,
                humidity_mean_pct=humidity,
                temp_min_c=_opt_float("temp_min_c"),
                temp_max_c=_opt_float("temp_max_c"),
                rain_mm=_parse_float(row.get("rain_mm", ""), context=ctx, default=0.0),
            )
        )
    return out


def _load_mother_trees(path: Path) -> dict[str, MotherTree]:
    out: dict[str, MotherTree] = {}
    for i, row in enumerate(_rows(path), start=2):
        ctx = f"mother_trees.csv 第{i}行"
        tree_id = (row.get("tree_id") or "").strip()
        if not tree_id:
            raise DataError(f"{ctx}：母树编号 tree_id 为空")
        if tree_id in out:
            raise DataError(f"{ctx}：母树编号 {tree_id} 重复")
        sticks = _parse_int(row.get("usable_sticks", ""), context=ctx)
        if sticks < 0:
            raise DataError(f"{ctx}：可采穗条数不能为负")
        out[tree_id] = MotherTree(
            tree_id=tree_id,
            cultivar=(row.get("cultivar") or "").strip(),
            location=(row.get("location") or "").strip(),
            age_years=_parse_float(row.get("age_years", ""), context=ctx),
            usable_sticks=sticks,
            health_note=(row.get("health_note") or "").strip(),
        )
    if not out:
        raise DataError("mother_trees.csv 没有任何母树记录")
    return out
