"""来年穗条调配方案生成。

输入：今年各组合的成活表现 + 母树可采穗条量 + 来年计划嫁接量。
输出：
  1. 每个「品种 × 砧木」的穗条从哪些母树调、调多少根；
  2. 哪些母树因低成活应停采/复检；
  3. 母树产能缺口与外部调剂建议；
  4. 参照今年气象的各地块建议嫁接窗口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from .analytics import aggregate, attribution
from .environment import count_stress_days, readings_in_window
from .models import Batch, ComboStats, SurvivalStatus
from .storage import DataError, Dataset

BUDS_PER_STICK = 3          # 每根健壮穗条平均可取 3 个接芽
SAFETY_MARGIN = 1.10        # 10% 安全余量（损耗 + 补接）
SUSPEND_RATE = 0.60         # 非环境胁迫下成活率低于 60% → 暂停采穗
WINDOW_DAYS = 30
IDEAL_TEMP = (20.0, 28.0)
IDEAL_HUMIDITY = (65.0, 85.0)


@dataclass
class AllocationLine:
    """一行调配：某品种×砧木 的需求由某母树承担多少。"""

    cultivar: str
    rootstock: str
    mother_tree_id: str
    tree_location: str
    basis_rate: float          # 该母树在此砧木上的今年成活率（无数据为 None→先验）
    basis_n: int               # 样本株数
    grafts_planned: int
    sticks: int
    priority_rank: int
    evidence: str              # 实测 / 跨砧木推算 / 无记录（先验）


@dataclass
class TreeUse:
    tree_id: str
    cultivar: str
    sticks_capacity: int
    sticks_used: int
    suspended: bool

    @property
    def remaining(self) -> int:
        return max(0, self.sticks_capacity - self.sticks_used)


@dataclass
class Deficit:
    cultivar: str
    rootstock: str
    grafts_short: int
    sticks_short: int
    note: str


@dataclass
class WindowAdvice:
    field: str
    suggested_start: date
    expected_stress_days: float
    expected_temp: float
    expected_humidity: float
    note: str


@dataclass
class Plan:
    year: int
    lines: list[AllocationLine] = field(default_factory=list)
    tree_uses: dict[str, TreeUse] = field(default_factory=dict)
    deficits: list[Deficit] = field(default_factory=list)
    suspended_trees: list[tuple[str, str, str]] = field(default_factory=list)
    watch_trees: list[tuple[str, str, str]] = field(default_factory=list)
    windows: list[WindowAdvice] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- targets

def default_targets(ds: Dataset) -> dict[tuple[str, str], int]:
    """没有给目标文件时：来年计划量 = 今年各「品种×砧木」已复检株数（持平）。"""
    counts: dict[tuple[str, str], int] = {}
    for g in ds.grafts:
        if g.status is SurvivalStatus.PENDING:
            continue
        key = (g.scion_cultivar, g.rootstock)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def load_targets(path: str) -> dict[tuple[str, str], int]:
    """读取计划量 CSV：cultivar,rootstock,qty（首行表头）。"""
    import csv

    targets: dict[tuple[str, str], int] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            if not (row.get("cultivar") or "").strip():
                continue
            key = ((row["cultivar"]).strip(), (row["rootstock"]).strip())
            qty = int((row.get("qty") or "0").strip())
            if qty < 0:
                raise DataError(f"targets 第{i}行：qty 不能为负")
            targets[key] = targets.get(key, 0) + qty
    return targets


# ---------------------------------------------------------------- ranking

def _tree_scores(
    stats: list[ComboStats],
) -> tuple[
    dict[tuple[str, str, str], tuple[float, int]],
    dict[tuple[str, str], tuple[float, int]],
]:
    """返回 (品种,砧木,母树) → (率, 样本) 与 (品种,母树) → 跨砧木汇总。"""
    exact: dict[tuple[str, str, str], tuple[float, int]] = {}
    agg: dict[tuple[str, str], list[int]] = {}
    for s in stats:
        exact[(s.cultivar, s.rootstock, s.mother_tree_id)] = (s.rate, s.total)
        agg.setdefault((s.cultivar, s.mother_tree_id), []).extend(
            [1] * s.alive + [0] * s.dead
        )
    cross = {
        key: (sum(v) / len(v), len(v))
        for key, v in agg.items()
    }
    return exact, cross


def _suspended_trees(
    ds: Dataset, stats: list[ComboStats]
) -> set[str]:
    """低成活且非环境主导的母树 → 来年停采。

    判定与 analytics.attribution 同口径：同批最优对照也很差（环境重击）
    或明确归因于"环境+组合"需复试的，不直接停采，只给出复试标记。
    """
    banned: set[str] = set()
    watch: set[str] = set()
    all_stats = aggregate(ds)
    for s in stats:
        if s.total < 5 or s.rate >= SUSPEND_RATE:
            continue
        reason = attribution(ds, s)
        if reason.startswith("环境主导"):
            continue
        if reason.startswith("环境+组合"):
            watch.add(s.mother_tree_id)
            continue
        banned.add(s.mother_tree_id)
    return banned


# ---------------------------------------------------------------- plan

def build_plan(
    ds: Dataset,
    year: int,
    targets: dict[tuple[str, str], int] | None = None,
) -> Plan:
    stats = aggregate(ds)
    exact, cross = _tree_scores(stats)
    suspended = _suspended_trees(ds, stats)
    if targets is None:
        targets = default_targets(ds)

    plan = Plan(year=year)
    for tree in ds.mother_trees.values():
        plan.tree_uses[tree.tree_id] = TreeUse(
            tree_id=tree.tree_id,
            cultivar=tree.cultivar,
            sticks_capacity=tree.usable_sticks,
            sticks_used=0,
            suspended=tree.tree_id in suspended,
        )
    for s in stats:
        reason = attribution(ds, s)
        if s.total < 5 or s.rate >= SUSPEND_RATE:
            continue
        t = ds.mother_trees[s.mother_tree_id]
        entry = (
            s.mother_tree_id, t.cultivar,
            f"{s.rootstock} 成活率 {s.rate:.0%}（{reason}）",
        )
        if s.mother_tree_id in suspended:
            if not any(e[0] == s.mother_tree_id for e in plan.suspended_trees):
                plan.suspended_trees.append(entry)
        elif reason.startswith("环境+组合"):
            if not any(e[0] == s.mother_tree_id for e in plan.watch_trees):
                plan.watch_trees.append(entry)

    total_grafts = total_sticks = 0

    for (cultivar, rootstock), qty in targets.items():
        if qty <= 0:
            continue
        need_grafts = round(qty * SAFETY_MARGIN)
        need_sticks = -(-need_grafts // BUDS_PER_STICK)  # 向上取整

        # 候选母树：品种匹配、未停采
        candidates = [
            t for t in ds.mother_trees.values()
            if t.cultivar == cultivar and t.tree_id not in suspended
        ]

        def score(tree_id: str) -> tuple[float, float, int]:
            # 排序键：证据等级（实测>跨砧木>无记录）、成活率、样本量
            if (cultivar, rootstock, tree_id) in exact:
                rate, n = exact[(cultivar, rootstock, tree_id)]
                return (2.0, rate, n)
            if (cultivar, tree_id) in cross:
                rate, n = cross[(cultivar, tree_id)]
                return (1.0, rate, n)
            return (0.0, 0.5, 0)  # 无记录先验 50%

        ranked = sorted(
            candidates,
            key=lambda t: (score(t.tree_id)[0], score(t.tree_id)[1], score(t.tree_id)[2]),
            reverse=True,
        )

        remaining_grafts = need_grafts
        for rank, tree in enumerate(ranked, start=1):
            if remaining_grafts <= 0:
                break
            use = plan.tree_uses[tree.tree_id]
            cap_grafts = use.remaining * BUDS_PER_STICK
            if cap_grafts <= 0:
                continue
            take_grafts = min(remaining_grafts, cap_grafts)
            take_sticks = -(-take_grafts // BUDS_PER_STICK)
            evidence_level, basis_rate, basis_n = score(tree.tree_id)
            evidence = {2: "实测", 1: "跨砧木推算", 0: "无记录（先验50%）"}[
                int(evidence_level)
            ]
            plan.lines.append(
                AllocationLine(
                    cultivar=cultivar,
                    rootstock=rootstock,
                    mother_tree_id=tree.tree_id,
                    tree_location=tree.location,
                    basis_rate=basis_rate,
                    basis_n=basis_n,
                    grafts_planned=take_grafts,
                    sticks=take_sticks,
                    priority_rank=rank,
                    evidence=evidence,
                )
            )
            use.sticks_used += take_sticks
            remaining_grafts -= take_grafts
            total_grafts += take_grafts
            total_sticks += take_sticks

        if remaining_grafts > 0:
            plan.deficits.append(
                Deficit(
                    cultivar=cultivar,
                    rootstock=rootstock,
                    grafts_short=remaining_grafts,
                    sticks_short=-(-remaining_grafts // BUDS_PER_STICK),
                    note=(
                        "所有候选母树产能已满"
                        + ("（含停采母树未计入）" if suspended else "")
                        + "，需外部调剂或扩建采穗圃"
                    ),
                )
            )

    plan.totals = {
        "grafts_planned": total_grafts,
        "sticks_needed": total_sticks,
        "target_grafts": sum(targets.values()),
    }
    plan.windows = advise_windows(ds, year)
    return plan


# ---------------------------------------------------------------- windows

def advise_windows(ds: Dataset, year: int) -> list[WindowAdvice]:
    """对每个地块：在今年记录覆盖的春接季里滑动 30 天窗口，
    选胁迫天数最少（其次均温最接近 24℃、湿度最接近 75%）的起点，
    平移到目标年份给出建议。"""
    env_idx = ds.env_index()
    fields = sorted({b.field for b in ds.batches.values()})
    out: list[WindowAdvice] = []
    for field_id in fields:
        series = env_idx.get(field_id, [])
        if not series:
            continue
        # 以该地块最早批次的嫁接年为气象基准年
        base_year = min(
            b.graft_date for b in ds.batches.values() if b.field == field_id
        ).year
        first_day = date(base_year, 2, 15)
        last_day = date(base_year, 4, 15)
        # 构造伪批次用于复用读数取窗
        probe = Batch(
            code="_probe", field=field_id, graft_date=first_day,
            method=ds.batches[  # 取该地块任一方法
                next(iter(b.code for b in ds.batches.values() if b.field == field_id))
            ].method,
            operator="",
        )
        candidates = []
        cur = first_day
        while cur <= last_day:
            probe = Batch(
                code="_probe", field=field_id, graft_date=cur,
                method=probe.method, operator="",
            )
            window = readings_in_window(probe, series)
            if len(window) >= WINDOW_DAYS * 0.8:
                stress, _ = count_stress_days(window)
                t = mean(r.temp_mean_c for r in window)
                h = mean(r.humidity_mean_pct for r in window)
                temp_pen = abs(t - 24)
                hum_pen = abs(h - 75) / 5
                candidates.append((stress, temp_pen + hum_pen, cur, stress, t, h))
            cur += timedelta(days=1)
        if not candidates:
            continue
        _, _, best, stress_days, t, h = min(candidates, key=lambda c: (c[0], c[1]))
        shifted = date(year, best.month, best.day)
        ideal = IDEAL_TEMP[0] <= t <= IDEAL_TEMP[1] and IDEAL_HUMIDITY[0] <= h <= IDEAL_HUMIDITY[1]
        out.append(
            WindowAdvice(
                field=field_id,
                suggested_start=shifted,
                expected_stress_days=stress_days,
                expected_temp=round(t, 1),
                expected_humidity=round(h, 1),
                note=(
                    "窗口温湿度处于适宜区间"
                    if ideal
                    else "窗口为胁迫最少之选，仍需准备遮阴/喷雾/覆膜预案"
                ),
            )
        )
    return out
