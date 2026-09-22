"""愈合期温湿度胁迫分析。

嫁接成活不只取决于组合亲和性：愈合期遇到连续高温低湿（蒸腾失水）、
低温（愈伤组织不活动）或暴雨积水，同样会大面积死苗。分析时把组合
成活率与其愈合期环境一起呈现，避免把「天旱晒死的」错判为「亲和差」。
"""
from __future__ import annotations

from datetime import timedelta
from statistics import mean

from .models import Batch, EnvironmentReading

# 愈合期长度（含嫁接当天）
HEALING_DAYS = {
    "bud": 21,
}
DEFAULT_HEALING_DAYS = 30

# 胁迫阈值（日均口径）
HIGH_TEMP_C = 32.0
LOW_TEMP_C = 13.0
LOW_HUMIDITY_PCT = 60.0
HEAVY_RAIN_MM = 25.0

# 连续高温低湿判定窗口
RUN_LENGTH = 3


def healing_window_days(batch: Batch) -> int:
    return HEALING_DAYS.get(batch.method.value, DEFAULT_HEALING_DAYS)


def readings_in_window(
    batch: Batch, series: list[EnvironmentReading]
) -> list[EnvironmentReading]:
    """取该批次地块在愈合窗口内的读数（按日期对齐）。"""
    end = batch.graft_date + timedelta(days=healing_window_days(batch) - 1)
    return [r for r in series if batch.graft_date <= r.day <= end]


def count_stress_days(window: list[EnvironmentReading]) -> tuple[int, list[str]]:
    """统计胁迫天数与命中的胁迫类型说明。

    单日命中任一：
      - 高温低湿：日均温 ≥32℃ 且湿度 ≤60%
      - 低温：日均温 <13℃
      - 暴雨：日降雨 ≥25mm
    另计连续 ≥3 天「温度>28 且湿度<70」的干热连段，整段计胁迫。
    """
    stress_flags: list[set[str]] = []
    for r in window:
        tags: set[str] = set()
        if r.temp_mean_c >= HIGH_TEMP_C and r.humidity_mean_pct <= LOW_HUMIDITY_PCT:
            tags.add("高温低湿")
        if r.temp_mean_c < LOW_TEMP_C:
            tags.add("低温")
        if r.rain_mm >= HEAVY_RAIN_MM:
            tags.add("暴雨")
        stress_flags.append(tags)

    # 干热连段：连续 ≥3 天「温度>28 且湿度<70」。
    # 对只满足连段、未达高温低湿阈值的天补标记，避免与单日规则重复计数。
    run = 0
    for i, r in enumerate(window):
        hot_dry = r.temp_mean_c > 28 and r.humidity_mean_pct < 70
        run = run + 1 if hot_dry else 0
        if run >= RUN_LENGTH:
            for j in range(i - RUN_LENGTH + 1, i + 1):
                stress_flags[j].add("干热连段")

    days = sum(1 for tags in stress_flags if tags)
    counter: dict[str, int] = {}
    for tags in stress_flags:
        # 互斥归类：单日已命中严重规则的天不再计入连段
        primary = next(
            (t for t in ("高温低湿", "低温", "暴雨") if t in tags),
            "干热连段" if "干热连段" in tags else None,
        )
        if primary:
            counter[primary] = counter.get(primary, 0) + 1
    detail_parts = []
    for name in ("高温低湿", "干热连段", "低温", "暴雨"):
        if counter.get(name):
            detail_parts.append(f"{name}{counter[name]}天")
    return days, detail_parts


def summarize_window(
    batch: Batch, series: list[EnvironmentReading]
) -> tuple[float, float, int, float, str, int]:
    """返回 (均温, 均湿, 胁迫天数, 胁迫占比, 说明, 窗口应有天数)。"""
    window = readings_in_window(batch, series)
    span = healing_window_days(batch)
    if not window:
        return float("nan"), float("nan"), 0, 0.0, "无环境读数", span
    days, detail_parts = count_stress_days(window)
    return (
        mean(r.temp_mean_c for r in window),
        mean(r.humidity_mean_pct for r in window),
        days,
        days / span,
        "、".join(detail_parts) if detail_parts else "无显著胁迫",
        span,
    )
