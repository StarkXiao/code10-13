"""终端表格与 Markdown 报告输出。"""
from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from .analytics import aggregate, attribution, low_survival, overall, pending_count
from .models import ComboStats, SurvivalStatus
from .planning import Plan
from .storage import Dataset


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    """简单等宽终端表格（中文按显示宽度 2 计）。"""
    def width(s: str) -> int:
        return sum(2 if ord(ch) > 127 else 1 for ch in s)

    def pad(s: str, w: int) -> str:
        return s + " " * max(0, w - width(s))

    widths = [width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], width(cell))

    def line(cells: list[str]) -> str:
        return "│ " + " │ ".join(pad(c, widths[i]) for i, c in enumerate(cells)) + " │"

    sep_top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    sep_mid = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    sep_bot = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"
    return "\n".join(
        [sep_top, line(headers), sep_mid] + [line(r) for r in rows] + [sep_bot]
    )


def combo_rows(stats: list[ComboStats]) -> list[list[str]]:
    rows = []
    for s in stats:
        rows.append(
            [
                s.rootstock,
                s.cultivar,
                s.mother_tree_id,
                str(s.total),
                str(s.alive),
                _pct(s.rate),
                _pct(s.wilson_low),
                s.grade,
                f"{s.avg_temp:.1f}/{s.avg_humidity:.0f}" if s.avg_temp else "-",
                f"{_pct(s.stress_share)}/峰{_pct(s.worst_stress_share)}",
                s.stress_detail,
            ]
        )
    return rows


COMBO_HEADERS = [
    "砧木", "接穗品种", "穗条母树", "复检数", "成活", "成活率",
    "置信下界", "评级", "均温℃/湿%", "胁迫均/峰", "胁迫明细",
]


def summary_text(ds: Dataset) -> str:
    o = overall(ds)
    lines = [
        f"全圃：已复检 {o['reviewed']} 株，成活 {o['alive']} 株，"
        f"总成活率 {_pct(o['rate'])}；另有 {o['pending']} 株未到复检日。",
        "",
        "各组合表现（按置信下界升序，越靠前越差）：",
        render_table(COMBO_HEADERS, combo_rows(aggregate(ds))),
    ]
    lows = low_survival(ds)
    if lows:
        lines += ["", f"⚠ 低成活组合 {len(lows)} 个（成活率<70% 且样本≥5，或置信下界<55%）："]
        for s in lows:
            lines.append(
                f"  · {s.rootstock} × {s.cultivar}（母树 {s.mother_tree_id}）"
                f" {s.alive}/{s.total} = {_pct(s.rate)}，"
                f"下界 {_pct(s.wilson_low)}；"
                f"胁迫均值 {_pct(s.stress_share)}、峰值 {_pct(s.worst_stress_share)}；"
                f"归因：{attribution(ds, s)}"
            )
    else:
        lines += ["", "✓ 无样本达标的低成活组合。"]
    return "\n".join(lines)


def plants_text(ds: Dataset, *, only_dead: bool, limit: int) -> str:
    recs = ds.grafts
    if only_dead:
        recs = [g for g in recs if g.status is SurvivalStatus.DEAD]
    recs = sorted(recs, key=lambda g: (g.batch_code, g.plant_id))[:limit]
    rows = [
        [
            g.plant_id, g.batch_code, g.rootstock, g.scion_cultivar,
            g.mother_tree_id, g.status.label, g.graft_date.isoformat(),
            g.review_date.isoformat(),
        ]
        for g in recs
    ]
    return render_table(
        ["植株", "批次", "砧木", "品种", "母树", "结论", "嫁接日", "复检日"], rows
    )


def combos_csv(ds: Dataset) -> str:
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "rootstock", "scion_cultivar", "mother_tree_id", "reviewed", "alive",
            "rate", "wilson_low", "grade", "avg_temp_c", "avg_humidity_pct",
            "stress_share", "worst_stress_share", "stress_detail", "batches",
        ]
    )
    for s in aggregate(ds):
        w.writerow(
            [
                s.rootstock, s.cultivar, s.mother_tree_id, s.total, s.alive,
                f"{s.rate:.4f}", f"{s.wilson_low:.4f}", s.grade,
                f"{s.avg_temp:.2f}", f"{s.avg_humidity:.2f}",
                f"{s.stress_share:.4f}", f"{s.worst_stress_share:.4f}",
                s.stress_detail, ";".join(s.batches),
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------- markdown

def plan_markdown(ds: Dataset, plan: Plan) -> str:
    o = overall(ds)
    lines: list[str] = []
    lines.append(f"# {plan.year} 年穗条调配方案\n")
    lines.append(
        f"> 依据：今年已复检 {o['reviewed']} 株、总成活率 {_pct(o['rate'])}；"
        f"安全余量 10%，每根穗条按 3 接芽计。\n"
    )

    lines.append("## 一、低成活组合识别\n")
    lows = low_survival(ds)
    if not lows:
        lines.append("无样本达标的低成活组合。\n")
    else:
        lines.append(
            "| 砧木 | 品种 | 母树 | 成活 | 成活率 | 置信下界 | 愈合期胁迫（均/峰） | 归因 |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for s in lows:
            lines.append(
                f"| {s.rootstock} | {s.cultivar} | {s.mother_tree_id} | "
                f"{s.alive}/{s.total} | {_pct(s.rate)} | {_pct(s.wilson_low)} | "
                f"{_pct(s.stress_share)} / {_pct(s.worst_stress_share)}"
                f"｜{s.stress_detail} | {attribution(ds, s)} |"
            )
        lines.append("")

    lines.append("## 二、停采 / 复检母树\n")
    if not plan.suspended_trees:
        lines.append("无。\n")
    else:
        lines.append("| 母树 | 品种 | 停采依据 |")
        lines.append("| --- | --- | --- |")
        for tree_id, cultivar, reason in plan.suspended_trees:
            lines.append(f"| {tree_id} | {cultivar} | {reason} |")
        lines.append("")

    if plan.watch_trees:
        lines.append("### 附：复试观察母树（暂不停采，限产并安排对照复试）\n")
        lines.append("| 母树 | 品种 | 观察依据 |")
        lines.append("| --- | --- | --- |")
        for tree_id, cultivar, reason in plan.watch_trees:
            lines.append(f"| {tree_id} | {cultivar} | {reason} |")
        lines.append("")

    lines.append("## 三、穗条调配明细\n")
    lines.append(
        f"计划嫁接（含余量）**{plan.totals['grafts_planned']} 株**，"
        f"需穗条 **{plan.totals['sticks_needed']} 根**。\n"
    )
    lines.append(
        "| 品种 | 砧木 | 优先级 | 母树 | 母本园 | 依据成活率(样本) | 证据 | 承担株数 | 穗条(根) |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for ln in plan.lines:
        basis = (
            f"{_pct(ln.basis_rate)}（{ln.basis_n}株）"
            if ln.evidence != "无记录（先验50%）"
            else "—"
        )
        lines.append(
            f"| {ln.cultivar} | {ln.rootstock} | {ln.priority_rank} | "
            f"{ln.mother_tree_id} | {ln.tree_location} | {basis} | "
            f"{ln.evidence} | {ln.grafts_planned} | {ln.sticks} |"
        )
    lines.append("")

    lines.append("## 四、母树产能占用\n")
    lines.append("| 母树 | 品种 | 产能(根) | 已分配 | 剩余 | 状态 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for u in sorted(plan.tree_uses.values(), key=lambda u: (u.cultivar, u.tree_id)):
        lines.append(
            f"| {u.tree_id} | {u.cultivar} | {u.sticks_capacity} | {u.sticks_used} "
            f"| {u.remaining} | {'停采复检' if u.suspended else '正常'} |"
        )
    lines.append("")

    lines.append("## 五、产能缺口与外部调剂\n")
    if not plan.deficits:
        lines.append("现有母本园产能可覆盖全部计划。\n")
    else:
        lines.append("| 品种 | 砧木 | 缺口株数 | 缺口穗条(根) | 建议 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for d in plan.deficits:
            lines.append(
                f"| {d.cultivar} | {d.rootstock} | {d.grafts_short} | "
                f"{d.sticks_short} | {d.note} |"
            )
        lines.append("")

    lines.append("## 六、建议嫁接窗口（依据今年气象滑窗）\n")
    if not plan.windows:
        lines.append("环境读数不足，无法给出窗口建议。\n")
    else:
        lines.append("| 地块 | 建议起嫁日 | 预计均温℃ | 预计湿度% | 30天内预计胁迫天数 | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for w in plan.windows:
            lines.append(
                f"| {w.field} | {w.suggested_start.isoformat()} | "
                f"{w.expected_temp} | {w.expected_humidity} | "
                f"{w.expected_stress_days} | {w.note} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(
        "_说明：成活率排序使用 Wilson 95% 置信下界，避免小样本误判；"
        "环境胁迫占比≥30% 的低成活组合不归咎于母树。_\n"
    )
    return "\n".join(lines)


def write_file(path: str | Path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
