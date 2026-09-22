"""成活率统计、低成活组合识别与愈伤期环境线索分析。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_MIN_SAMPLES = 30     # 组合样本量下限，低于则判“样本不足”
DEFAULT_LOW_THRESHOLD = 0.60  # 成活率绝对阈值
DEFAULT_HEALING_DAYS = 14    # 愈伤关键期（嫁接后天数）
Z_95 = 1.96

STATUS_LOW = "低成活"
STATUS_OK = "正常"
STATUS_GOOD = "表现优"
STATUS_FEW = "样本不足"


def wilson_interval(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间，小样本下依然稳健。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _classify(k: int, n: int, overall: float, min_samples: int,
              low_threshold: float) -> tuple[str, str]:
    """判定组合状态，返回 (状态, 理由)。"""
    if n < min_samples:
        return STATUS_FEW, f"样本量 {n} < {min_samples}，暂不下结论"
    p = k / n
    low, high = wilson_interval(k, n)
    if p < low_threshold:
        return STATUS_LOW, f"成活率 {p:.1%} 低于阈值 {low_threshold:.0%}"
    if high < overall:
        return STATUS_LOW, f"置信区间上限 {high:.1%} 低于总体水平 {overall:.1%}"
    if low > overall:
        return STATUS_GOOD, f"置信区间下限 {low:.1%} 高于总体水平 {overall:.1%}"
    return STATUS_OK, ""


@dataclass
class ComboStats:
    """品种×砧木 组合的成活统计。"""
    cultivar: str
    rootstock: str
    batches: int
    grafted: int
    alive: int
    rate: float
    wilson_low: float
    wilson_high: float
    status: str
    status_reason: str


# 每批次取最新一次调查作为定案成活率
_LATEST_SURVEY_JOIN = """
JOIN survival_survey s ON s.batch_id = b.id
JOIN (SELECT batch_id, MAX(survey_date) AS md
      FROM survival_survey GROUP BY batch_id) t
  ON t.batch_id = s.batch_id AND t.md = s.survey_date
"""


def load_combo_stats(conn, min_samples: int = DEFAULT_MIN_SAMPLES,
                     low_threshold: float = DEFAULT_LOW_THRESHOLD
                     ) -> list[ComboStats]:
    """按 品种×砧木 汇总成活统计并判定状态。"""
    rows = conn.execute(
        f"""
        SELECT c.name AS cultivar, r.name AS rootstock,
               COUNT(DISTINCT b.id)   AS batches,
               SUM(b.quantity)        AS grafted,
               SUM(s.alive_count)     AS alive
        FROM graft_batch b
        JOIN cultivar  c ON c.id = b.cultivar_id
        JOIN rootstock r ON r.id = b.rootstock_id
        {_LATEST_SURVEY_JOIN}
        GROUP BY c.name, r.name
        """
    ).fetchall()
    total_g = sum(r["grafted"] for r in rows)
    total_a = sum(r["alive"] for r in rows)
    overall = total_a / total_g if total_g else 0.0

    stats: list[ComboStats] = []
    for r in rows:
        low, high = wilson_interval(r["alive"], r["grafted"])
        status, reason = _classify(r["alive"], r["grafted"], overall,
                                   min_samples, low_threshold)
        stats.append(ComboStats(
            cultivar=r["cultivar"], rootstock=r["rootstock"],
            batches=r["batches"], grafted=r["grafted"], alive=r["alive"],
            rate=r["alive"] / r["grafted"] if r["grafted"] else 0.0,
            wilson_low=low, wilson_high=high,
            status=status, status_reason=reason,
        ))
    stats.sort(key=lambda s: (s.cultivar, -s.wilson_low))
    return stats


def overview(conn) -> dict:
    """全季总览指标。"""
    b = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(quantity), 0) AS q"
        " FROM graft_batch").fetchone()
    s = conn.execute(
        f"""
        SELECT COUNT(DISTINCT b.id)       AS batches,
               COALESCE(SUM(b.quantity), 0) AS grafted,
               COALESCE(SUM(s.alive_count), 0) AS alive
        FROM graft_batch b
        {_LATEST_SURVEY_JOIN}
        """
    ).fetchone()
    return {
        "batches": b["n"], "grafted": b["q"],
        "surveyed_batches": s["batches"], "surveyed_grafted": s["grafted"],
        "alive": s["alive"],
        "overall_rate": (s["alive"] / s["grafted"]) if s["grafted"] else 0.0,
    }


@dataclass
class BatchEnvStats:
    """单批次的成活率 + 愈伤期环境暴露。"""
    batch_no: str
    cultivar: str
    rootstock: str
    graft_date: date
    quantity: int
    alive: int
    rate: float
    days: int                 # 窗口内有效环境记录天数
    temp_avg: float | None
    humidity_avg: float | None
    hot_days: int             # 最高温 ≥ hot 的天数
    cold_days: int            # 最低温 ≤ cold 的天数
    dry_days: int             # 平均湿度 ≤ dry 的天数
    wet_days: int             # 平均湿度 ≥ wet 的天数


def load_batch_env_stats(conn, healing_days: int = DEFAULT_HEALING_DAYS,
                         hot: float = 32.0, cold: float = 5.0,
                         dry: float = 50.0, wet: float = 95.0
                         ) -> list[BatchEnvStats]:
    """逐批次计算愈伤期（嫁接后 healing_days 天）内的环境暴露。"""
    batches = conn.execute(
        f"""
        SELECT b.id, b.batch_no, b.graft_date, b.quantity, b.plot_id,
               c.name AS cultivar, r.name AS rootstock, s.alive_count
        FROM graft_batch b
        JOIN cultivar  c ON c.id = b.cultivar_id
        JOIN rootstock r ON r.id = b.rootstock_id
        {_LATEST_SURVEY_JOIN}
        ORDER BY b.graft_date
        """
    ).fetchall()
    result: list[BatchEnvStats] = []
    for b in batches:
        start = date.fromisoformat(b["graft_date"])
        end = start + timedelta(days=healing_days - 1)
        env = conn.execute(
            """
            SELECT COUNT(*) AS days,
                   AVG(temp_avg)     AS tavg,
                   AVG(humidity_avg) AS havg,
                   COALESCE(SUM(CASE WHEN temp_max     >= ? THEN 1 ELSE 0 END), 0) AS hot,
                   COALESCE(SUM(CASE WHEN temp_min     <= ? THEN 1 ELSE 0 END), 0) AS cold,
                   COALESCE(SUM(CASE WHEN humidity_avg <= ? THEN 1 ELSE 0 END), 0) AS dry,
                   COALESCE(SUM(CASE WHEN humidity_avg >= ? THEN 1 ELSE 0 END), 0) AS wet
            FROM env_record
            WHERE plot_id = ? AND record_date BETWEEN ? AND ?
            """,
            (hot, cold, dry, wet, b["plot_id"],
             start.isoformat(), end.isoformat()),
        ).fetchone()
        result.append(BatchEnvStats(
            batch_no=b["batch_no"], cultivar=b["cultivar"],
            rootstock=b["rootstock"], graft_date=start,
            quantity=b["quantity"], alive=b["alive_count"],
            rate=b["alive_count"] / b["quantity"] if b["quantity"] else 0.0,
            days=env["days"], temp_avg=env["tavg"], humidity_avg=env["havg"],
            hot_days=env["hot"], cold_days=env["cold"],
            dry_days=env["dry"], wet_days=env["wet"],
        ))
    return result


def env_insights(stats: list[BatchEnvStats]) -> list[str]:
    """把批次按成活率中位数分成高/低两组，比较愈伤期环境暴露差异。"""
    usable = [s for s in stats if s.days > 0]
    if len(usable) < 4:
        return ["覆盖环境数据的批次不足 4 个，暂无法做环境对比。"]
    ordered = sorted(usable, key=lambda s: s.rate)
    half = len(ordered) // 2
    low_group = ordered[:half]
    high_group = ordered[len(ordered) - half:]

    factors = [
        ("平均温度",       "temp_avg",     "℃",  1.0),
        ("平均湿度",       "humidity_avg", "%",  5.0),
        ("高温日(≥32℃)",   "hot_days",     "天", 0.5),
        ("低温日(≤5℃)",    "cold_days",    "天", 0.5),
        ("干燥日(湿度≤50%)", "dry_days",    "天", 0.5),
        ("高湿日(湿度≥95%)", "wet_days",    "天", 0.5),
    ]
    msgs: list[str] = []
    for label, attr, unit, min_diff in factors:
        lo = sum(getattr(s, attr) or 0 for s in low_group) / len(low_group)
        hi = sum(getattr(s, attr) or 0 for s in high_group) / len(high_group)
        if abs(lo - hi) >= min_diff:
            direction = "偏高" if lo > hi else "偏低"
            msgs.append(
                f"低成活组愈伤期{label} {lo:.1f}{unit}，"
                f"较高成活组（{hi:.1f}{unit}）{direction}，"
                "提示该因子可能与低成活相关。")
    return msgs or ["高低成活组的环境暴露差异不明显，"
                    "低成活更可能来自砧穗亲和性或嫁接操作因素。"]
