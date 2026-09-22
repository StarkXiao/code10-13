"""分析报告与调配方案的 Markdown 渲染。"""
from __future__ import annotations

from .analysis import STATUS_LOW, ComboStats
from .planning import FLAG_ALL_LOW, FLAG_NO_DATA, PlanRow


def render_analysis(ov: dict, stats: list[ComboStats],
                    insights: list[str], healing_days: int) -> str:
    lines = ["# 嫁接成活分析报告", ""]
    lines += [
        "## 总览", "",
        f"- 嫁接批次 **{ov['batches']}** 个，共嫁接 **{ov['grafted']:,}** 株",
        f"- 已调查 **{ov['surveyed_batches']}** 个批次"
        f"（未调查 {ov['batches'] - ov['surveyed_batches']} 个）",
        f"- 总成活率 **{ov['overall_rate']:.1%}**"
        f"（{ov['alive']:,} / {ov['surveyed_grafted']:,}）",
        "",
    ]

    lines += [
        "## 组合成活率（品种 × 砧木）", "",
        "| 品种 | 砧木 | 批次 | 嫁接株数 | 成活株数 | 成活率 | 95% 置信区间 | 判定 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    mark = {STATUS_LOW: " ⚠", "表现优": " ★"}
    for s in stats:
        lines.append(
            f"| {s.cultivar} | {s.rootstock} | {s.batches} | {s.grafted:,}"
            f" | {s.alive:,} | {s.rate:.1%}"
            f" | {s.wilson_low:.1%} ~ {s.wilson_high:.1%}"
            f" | {s.status}{mark.get(s.status, '')} |")
    lines.append("")

    low = [s for s in stats if s.status == STATUS_LOW]
    lines += ["## 低成活组合", ""]
    if low:
        for s in low:
            lines.append(
                f"- **{s.cultivar} × {s.rootstock}**：成活率 {s.rate:.1%}"
                f"（{s.alive:,}/{s.grafted:,}），{s.status_reason}")
    else:
        lines.append("- 未发现低成活组合。")
    lines.append("")

    lines += [f"## 愈伤期环境线索（嫁接后 {healing_days} 天窗口）", ""]
    lines += [f"- {m}" for m in insights]
    lines.append("")
    return "\n".join(lines)


def render_plan(rows: list[PlanRow], summary: dict[str, int],
                next_year: int, growth: float, buffer: float) -> str:
    lines = [f"# {next_year} 年穗条调配方案", ""]
    lines.append(
        f"> 未设目标的品种按今年嫁接量 +{growth:.0%} 推算；"
        f"嫁接量在成活率折算后附加 {buffer:.0%} 安全系数；"
        "预期成活率取历史 Wilson 95% 置信区间下界（保守口径）。")
    lines += [
        "",
        "| 品种 | 推荐砧木 | 目标成品苗 | 预期成活率 | 需嫁接株数"
        " | 株/穗条 | 穗条需求(根) | 备注 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in rows:
        rootstock = r.rootstock or "—（待定）"
        name = r.cultivar + (" ⚠" if r.flag == FLAG_ALL_LOW else "")
        lines.append(
            f"| {name} | {rootstock} | {r.target_plants:,}"
            f" | {r.expected_rate:.0%} | {r.grafts_needed:,}"
            f" | {r.grafts_per_scion:g} | {r.scions_needed:,}"
            f" | {r.note or '—'} |")
    lines.append("")

    lines += ["## 汇总", ""]
    lines.append(f"- 穗条总需求：**{sum(r.scions_needed for r in rows):,} 根**")
    lines.append(f"- 嫁接总任务：**{sum(r.grafts_needed for r in rows):,} 株**")
    if summary:
        lines.append("- 砧木苗需求（按推荐砧木）：")
        for name, qty in summary.items():
            lines.append(f"  - {name}：{qty:,} 株")
    lines.append("")

    risk = [r for r in rows if r.flag in (FLAG_ALL_LOW, FLAG_NO_DATA)]
    lines += ["## 风险与执行建议", ""]
    if risk:
        for r in risk:
            lines.append(f"- **{r.cultivar}**：{r.note}")
    else:
        lines.append("- 各品种均有可靠历史组合可依。")
    lines += [
        "- 穗条建议于休眠期（12 月—翌年 2 月）采集，蜡封后 0–5℃ 冷藏；"
        "嫁接前逐捆核对品种标签。",
        "- 低成活组合对应的穗条量已改配到推荐砧木；"
        "如需保留试配，请单独登记小批量批次以便来年继续追踪。",
        "",
    ]
    return "\n".join(lines)
