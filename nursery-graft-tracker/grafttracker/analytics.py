"""成活分析：组合聚合、Wilson 置信区间、低成活识别与原因归因。"""
from __future__ import annotations

from collections import defaultdict

from .environment import summarize_window
from .models import ComboStats, GraftRecord, SurvivalStatus
from .storage import Dataset


def wilson_lower(alive: int, total: int, z: float = 1.96) -> float:
    """二项比例 Wilson 95% 置信区间下界。

    样本很小时，点估计会骗人（5 株活 4 株 = 80% 并不稳）。
    用下界给组合排序，小样本自然靠后而不会直接被判死刑。
    """
    if total == 0:
        return 0.0
    phat = alive / total
    denom = 1 + z * z / total
    center = phat + z * z / (2 * total)
    margin = z * ((phat * (1 - phat) + z * z / (4 * total)) / total) ** 0.5
    return max(0.0, (center - margin) / denom)


def _weighted(
    pairs: list[tuple[float, int]],
) -> float:
    weight = sum(w for _, w in pairs)
    if weight == 0:
        return 0.0
    return sum(v * w for v, w in pairs) / weight


def aggregate(ds: Dataset) -> list[ComboStats]:
    """按 砧木 × 品种 × 母树 聚合已复检植株，并挂上愈合期环境。"""
    env_idx = ds.env_index()

    grouped: dict[tuple[str, str, str], list[GraftRecord]] = defaultdict(list)
    for g in ds.grafts:
        if g.status is SurvivalStatus.PENDING:
            continue  # 未复检不进分母
        grouped[g.combo_key].append(g)

    stats: list[ComboStats] = []
    for (rootstock, cultivar, tree_id), recs in grouped.items():
        total = len(recs)
        alive = sum(1 for g in recs if g.status is SurvivalStatus.ALIVE)

        # 按批次汇总环境，株数加权
        per_batch: dict[str, int] = defaultdict(int)
        for g in recs:
            per_batch[g.batch_code] += 1
        temp_pairs, hum_pairs, stress_pairs = [], [], []
        detail_terms: list[str] = []
        worst_share = 0.0
        methods: set[str] = set()
        for batch_code, n in per_batch.items():
            batch = ds.batches[batch_code]
            methods.add(batch.method.label)
            t, h, days, share, detail, span = summarize_window(
                batch, env_idx.get(batch.field, [])
            )
            if t == t:  # 非 NaN
                temp_pairs.append((t, n))
                hum_pairs.append((h, n))
                stress_pairs.append((share, n))
                worst_share = max(worst_share, share)
                if days and detail not in ("无显著胁迫", "无环境读数"):
                    tag = f"{batch_code}（{detail}，{days}/{span}天）"
                    if tag not in detail_terms:
                        detail_terms.append(tag)

        stats.append(
            ComboStats(
                rootstock=rootstock,
                cultivar=cultivar,
                mother_tree_id=tree_id,
                total=total,
                alive=alive,
                dead=total - alive,
                rate=alive / total,
                wilson_low=wilson_lower(alive, total),
                method="/".join(sorted(methods)),
                batches=sorted(per_batch),
                avg_temp=_weighted(temp_pairs),
                avg_humidity=_weighted(hum_pairs),
                stress_days=0,
                stress_share=_weighted(stress_pairs),
                worst_stress_share=worst_share,
                stress_detail="；".join(detail_terms) if detail_terms else "无显著胁迫",
            )
        )

    stats.sort(key=lambda s: (s.wilson_low, s.rate, -s.total))
    return stats


def pending_count(ds: Dataset) -> int:
    return sum(1 for g in ds.grafts if g.status is SurvivalStatus.PENDING)


def _batch_combo_rates(ds: Dataset) -> dict[str, list[float]]:
    """每个批次内各组合（样本≥5）的成活率列表，用于同批对照。

    注意必须按批次单算：某母树若同时在正常批次和受灾批次嫁接，
    跨批聚合率会把它的真实抗灾表现稀释掉。
    """
    counts: dict[tuple[str, tuple[str, str, str]], list[bool]] = defaultdict(list)
    for g in ds.grafts:
        if g.status is SurvivalStatus.PENDING:
            continue
        counts[(g.batch_code, g.combo_key)].append(
            g.status is SurvivalStatus.ALIVE
        )
    out: dict[str, list[float]] = defaultdict(list)
    for (batch_code, _key), flags in counts.items():
        if len(flags) >= 5:
            out[batch_code].append(sum(flags) / len(flags))
    return out


def attribution(ds: Dataset, stats: ComboStats) -> str:
    """低成活原因初判，用同批对照组合区分「天灾」与「组合问题」：

    - 环境主导：某一批次里所有组合都差（该批最优对照仍 <70%），天气背锅；
    - 亲和/穗源：某一批次里对照长得好（≥75% 且高出本组合 ≥25 点）——
      大家共受同一场胁迫，它独差，是组合（亲和性/母树）问题；
    - 环境+组合：既有受灾批次也有对照偏好的批次，明年复试拆分。
    """
    batch_rates = _batch_combo_rates(ds)
    best_peers, low_batches = [], []
    for batch_code in stats.batches:
        peers = [r for r in batch_rates.get(batch_code, [])]
        if not peers:
            continue
        best = max(peers)
        best_peers.append(best)
        if best < 0.70:
            low_batches.append((batch_code, best))
    best_peer = max(best_peers) if best_peers else stats.rate
    gap = best_peer - stats.rate
    peak = stats.worst_stress_share

    # 存在「全军覆没」批次且没有任何批次出现强对照 → 环境主导
    if low_batches and best_peer < 0.70:
        return (
            f"环境主导：同批最优对照仅 {best_peer:.0%}（胁迫峰值 {peak:.0%}），"
            "建议改善遮阴/喷雾后重复验证，暂不淘汰母树"
        )
    if best_peer >= 0.75 and gap >= 0.25:
        if stats.rate < 0.45:
            return (
                f"亲和性风险：同批对照 {best_peer:.0%} 而本组合 {stats.rate:.0%}，"
                "共受胁迫却独差，优先怀疑砧穗不亲和，建议更换砧木复试"
            )
        return (
            f"穗源风险：同批对照 {best_peer:.0%} 而该母树 {stats.rate:.0%}，"
            "建议暂停采穗并复检母树健康状况"
        )
    if low_batches and best_peer >= 0.70:
        return (
            f"环境+组合共同作用：受灾批次对照也仅 {max(r for _, r in low_batches):.0%}，"
            f"但正常条件下对照可达 {best_peer:.0%}；建议明年正常气候下复试一次再定去留"
        )
    return (
        f"环境+组合共同作用：同批对照 {best_peer:.0%}（高 {gap:.0%}）；"
        "建议明年正常气候下复试一次再定去留"
    )


def low_survival(ds: Dataset) -> list[ComboStats]:
    """低成活组合清单（样本达标 + 点估计/置信下界触线）。"""
    return [s for s in aggregate(ds) if s.low_survival]


def overall(ds: Dataset) -> dict[str, float | int]:
    recs = [g for g in ds.grafts if g.status is not SurvivalStatus.PENDING]
    alive = sum(1 for g in recs if g.status is SurvivalStatus.ALIVE)
    return {
        "reviewed": len(recs),
        "pending": pending_count(ds),
        "alive": alive,
        "rate": alive / len(recs) if recs else 0.0,
    }
