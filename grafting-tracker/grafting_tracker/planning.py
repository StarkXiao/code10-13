"""来年穗条调配方案生成。

核心思路：
- 每个品种优先选用历史表现可靠的砧木（按 Wilson 下界排序，保守口径）；
- 低成活组合被排除，不再安排来年嫁接；
- 需嫁接株数 = 目标成品苗 / 预期成活率 × (1 + 安全系数)；
- 穗条需求 = 需嫁接株数 / 每根穗条可嫁接株数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .analysis import STATUS_FEW, STATUS_LOW, ComboStats

FLAG_OK = "ok"
FLAG_ALL_LOW = "all_low"
FLAG_NO_DATA = "no_data"

MIN_EXPECTED_RATE = 0.05  # 预期成活率下限，防止除以零或失真


@dataclass
class PlanRow:
    cultivar: str
    rootstock: str | None      # 推荐砧木；None 表示无历史依据
    expected_rate: float       # 采用的预期成活率（Wilson 下界）
    target_plants: int         # 来年目标成品苗
    grafts_needed: int         # 需嫁接株数
    grafts_per_scion: float    # 每根穗条可嫁接株数
    scions_needed: int         # 穗条需求（根）
    flag: str                  # ok / all_low / no_data
    note: str = ""


def _pick_rootstock(candidates: list[ComboStats]):
    """从某品种的历史组合中挑选砧木，返回 (组合, flag, note)。"""
    usable = [s for s in candidates
              if s.status not in (STATUS_LOW, STATUS_FEW)]
    if usable:
        return max(usable, key=lambda s: s.wilson_low), FLAG_OK, ""
    few = [s for s in candidates if s.status == STATUS_FEW]
    if few:
        return (max(few, key=lambda s: s.wilson_low), FLAG_OK,
                "仅有小样本数据，建议先小批量验证后再扩大")
    if candidates:
        return (max(candidates, key=lambda s: s.wilson_low), FLAG_ALL_LOW,
                "全部组合低成活：建议改进嫁接工艺或试配新砧木，"
                "本方案按最保守口径备料")
    return None, FLAG_NO_DATA, "无历史嫁接数据，按默认成活率备料"


def build_plan(conn, stats: list[ComboStats], growth: float = 0.2,
               buffer: float = 0.1, default_rate: float = 0.5,
               default_grafts_per_scion: float = 4.0) -> list[PlanRow]:
    """生成逐品种调配方案。

    growth: 未设目标品种按今年嫁接量 × (1+growth) 推算目标；
    buffer: 在成活率折算之外附加的安全系数。
    """
    grafted = {r["cultivar"]: r["g"] for r in conn.execute(
        "SELECT c.name AS cultivar, SUM(b.quantity) AS g"
        " FROM graft_batch b JOIN cultivar c ON c.id = b.cultivar_id"
        " GROUP BY c.name")}
    specs: dict[str, tuple[float, int | None]] = {}
    for r in conn.execute(
            "SELECT c.name AS cultivar, s.grafts_per_scion AS gps,"
            " s.target_plants AS tp"
            " FROM scion_spec s JOIN cultivar c ON c.id = s.cultivar_id"):
        specs[r["cultivar"]] = (r["gps"], r["tp"])

    names = sorted(set(grafted) | set(specs) | {s.cultivar for s in stats})
    rows: list[PlanRow] = []
    for name in names:
        gps, tp = specs.get(name, (default_grafts_per_scion, None))
        notes: list[str] = []
        if tp:
            target = tp
            notes.append("目标：指定")
        else:
            target = math.ceil(grafted.get(name, 0) * (1 + growth))
            notes.append(f"目标：按今年嫁接量 +{growth:.0%} 推算")
        if target <= 0:
            continue

        candidates = [s for s in stats if s.cultivar == name]
        best, flag, note = _pick_rootstock(candidates)
        if note:
            notes.insert(0, note)
        if best is None:
            rootstock, expected = None, default_rate
        else:
            rootstock = best.rootstock
            expected = max(best.wilson_low, MIN_EXPECTED_RATE)

        grafts = math.ceil(target / expected * (1 + buffer))
        scions = math.ceil(grafts / gps) if gps > 0 else 0
        rows.append(PlanRow(
            cultivar=name, rootstock=rootstock, expected_rate=expected,
            target_plants=target, grafts_needed=grafts,
            grafts_per_scion=gps, scions_needed=scions,
            flag=flag, note="；".join(notes),
        ))
    return rows


def rootstock_summary(rows: list[PlanRow]) -> dict[str, int]:
    """按推荐砧木汇总砧木苗需求（株）。"""
    out: dict[str, int] = {}
    for r in rows:
        if r.rootstock:
            out[r.rootstock] = out.get(r.rootstock, 0) + r.grafts_needed
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
