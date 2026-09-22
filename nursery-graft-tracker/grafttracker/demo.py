"""生成一套贴近真实的演示数据（确定性随机，便于复现与测试）。

刻意构造的教学场景：
  · M-沃柑-07 在枳壳上亲和差（成活率约四成）→ 应被识别为低成活并停采；
  · F-西2 在 4 月上旬遭遇高温低湿热浪 → 同批次整体受冲击，归因应提示环境；
  · F-南3 3 月上旬低温连阴雨 → 环境胁迫组合；
  · M-沃柑-12、M-爱媛38-08 今年无嫁接记录 → 方案中以先验参与排序；
  · 母树产能故意不足以覆盖来年扩种目标 → 报告出现缺口与外部调剂建议。
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from .models import GraftMethod, SurvivalStatus


class _Rng:
    """可播种 LCG，保证每次生成的数据一致。"""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def random(self) -> float:
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        return self.state / 0x100000000

    def bernoulli(self, p: float) -> bool:
        return self.random() < p


# (tree_id, cultivar, location, age, usable_sticks, note)
MOTHER_TREES = [
    ("M-沃柑-03", "沃柑", "1号母本园-A区", 8, 420, "健壮"),
    ("M-沃柑-07", "沃柑", "1号母本园-B区", 6, 120, "去年轻度衰退"),
    ("M-沃柑-12", "沃柑", "2号母本园-C区", 5, 500, "新建母本园，尚未投产验证"),
    ("M-砂糖橘-02", "砂糖橘", "1号母本园-A区", 9, 350, "健壮"),
    ("M-砂糖橘-09", "砂糖橘", "3号母本园", 7, 80, "冠幅偏小"),
    ("M-爱媛38-01", "爱媛38", "1号母本园-D区", 8, 260, "健壮"),
    ("M-爱媛38-05", "爱媛38", "3号母本园", 10, 90, "树势一般"),
    ("M-爱媛38-08", "爱媛38", "2号母本园-C区", 4, 200, "新建母本园，尚未投产验证"),
]

# 批次: code, field, date, method, operator, note
BATCHES = [
    ("B2026-0305-E", "F-南3", date(2026, 3, 5), GraftMethod.CLEFT, "三组", "春接首批，遇倒春寒"),
    ("B2026-0310-A", "F-东1", date(2026, 3, 10), GraftMethod.CLEFT, "一组", ""),
    ("B2026-0310-B", "F-东1", date(2026, 3, 10), GraftMethod.WHIP, "二组", ""),
    ("B2026-0322-C", "F-西2", date(2026, 3, 22), GraftMethod.CLEFT, "一组", ""),
    ("B2026-0408-D", "F-西2", date(2026, 4, 8), GraftMethod.BUD, "二组", "芽接，遇4月上旬热浪"),
]

# 批次内组合: (batch_code, rootstock, cultivar, tree_id, n, true_alive_rate)
# 设计的四类教学场景：
#   0305-E 倒春寒批：两个组合都差（~50%）→ 环境主导，母树不背锅；
#   0310-A 正常批：03 优 / 07 差 → 07 亲和性风险，直接停采；
#   0322-C：香橙+01 优、枳壳+05 差 → 05 亲和性风险（砧木不亲和）；
#   0408-D 热浪批：03 也仅约六成、07 更差 → 环境+组合共同作用（07 仍被 0310 批坐实停采）。
COMBO_PLANS = [
    ("B2026-0305-E", "枳壳", "砂糖橘", "M-砂糖橘-02", 16, 0.52),
    ("B2026-0305-E", "枳壳", "砂糖橘", "M-砂糖橘-09", 16, 0.45),
    ("B2026-0310-A", "枳壳", "沃柑", "M-沃柑-03", 22, 0.91),
    ("B2026-0310-A", "枳壳", "沃柑", "M-沃柑-07", 22, 0.45),
    ("B2026-0310-B", "枳壳", "砂糖橘", "M-砂糖橘-02", 20, 0.88),
    ("B2026-0322-C", "香橙", "爱媛38", "M-爱媛38-01", 18, 0.92),
    ("B2026-0322-C", "枳壳", "爱媛38", "M-爱媛38-05", 18, 0.50),
    ("B2026-0408-D", "枳壳", "沃柑", "M-沃柑-03", 12, 0.62),
    ("B2026-0408-D", "枳壳", "沃柑", "M-沃柑-07", 12, 0.30),
]

# 每株抽一个未到复检日（pending），制造待复检样本
PENDING_PLANT_INDEX = 3


def _seasonal_temp(d: date, rng: _Rng) -> tuple[float, float, float]:
    """3~5 月基准气候：均温从 14℃ 平滑升到 24℃，叠加噪声。"""
    day_idx = (d - date(2026, 3, 1)).days
    base = 14 + 10 * min(1.0, day_idx / 70.0)
    mean_t = base + (rng.random() - 0.5) * 3.0
    return mean_t, mean_t - 5 - rng.random() * 2, mean_t + 5 + rng.random() * 3


def generate_environment(field: str, rng: _Rng) -> list[dict]:
    rows = []
    day = date(2026, 3, 1)
    end = date(2026, 5, 15)
    while day <= end:
        t, tmin, tmax = _seasonal_temp(day, rng)
        h = 72 + (rng.random() - 0.5) * 20
        rain = 0.0

        # F-南3：3/05–3/09 倒春寒，低温高湿连阴雨
        if field == "F-南3" and date(2026, 3, 5) <= day <= date(2026, 3, 9):
            t, tmin, tmax = 10.0 + rng.random(), 6.5, 13.0
            h = 86 + rng.random() * 6
            rain = 18 + rng.random() * 20
        # F-西2：4/08–4/13 热浪，高温低湿
        if field == "F-西2" and date(2026, 4, 8) <= day <= date(2026, 4, 13):
            t, tmin, tmax = 33.0 + rng.random() * 2, 21.0, 36.5
            h = 48 + rng.random() * 8
            rain = 0.0
        # F-东1：4/20 一场暴雨（单点，不主导整窗）
        if field == "F-东1" and day == date(2026, 4, 20):
            rain = 32.0
            h = 90.0

        rows.append(
            {
                "field": field,
                "day": day.isoformat(),
                "temp_mean_c": f"{t:.1f}",
                "temp_min_c": f"{tmin:.1f}",
                "temp_max_c": f"{tmax:.1f}",
                "humidity_mean_pct": f"{min(98, max(35, h)):.0f}",
                "rain_mm": f"{rain:.1f}",
            }
        )
        day += timedelta(days=1)
    return rows


def generate(data_dir: str | Path, *, force: bool = False) -> Path:
    base = Path(data_dir)
    if base.exists() and any(base.iterdir()) and not force:
        raise SystemExit(f"目录 {base} 非空；加 --force 覆盖")
    base.mkdir(parents=True, exist_ok=True)

    _write_csv(
        base / "mother_trees.csv",
        ["tree_id", "cultivar", "location", "age_years", "usable_sticks", "health_note"],
        [[t[0], t[1], t[2], f"{t[3]:.1f}", t[4], t[5]] for t in MOTHER_TREES],
    )
    _write_csv(
        base / "batches.csv",
        ["code", "field", "graft_date", "method", "operator", "note"],
        [[b[0], b[1], b[2].isoformat(), b[3].value, b[4], b[5]] for b in BATCHES],
    )

    env_rows: list[dict] = []
    for i, field in enumerate(sorted({b[1] for b in BATCHES})):
        env_rows.extend(generate_environment(field, _Rng(20260301 + i * 97)))
    _write_csv(
        base / "environment.csv",
        ["field", "day", "temp_mean_c", "temp_min_c", "temp_max_c",
         "humidity_mean_pct", "rain_mm"],
        [[r[k] for k in ("field", "day", "temp_mean_c", "temp_min_c",
                         "temp_max_c", "humidity_mean_pct", "rain_mm")]
         for r in env_rows],
    )

    graft_rows = []
    rng = _Rng(778899)
    seq = 0
    batch_dates = {b[0]: b[2] for b in BATCHES}
    for batch_code, rootstock, cultivar, tree_id, n, rate in COMBO_PLANS:
        for j in range(n):
            seq += 1
            graft_day = batch_dates[batch_code]
            pending = seq == PENDING_PLANT_INDEX
            if pending:
                status = SurvivalStatus.PENDING.value
                review = ""
            else:
                alive = rng.bernoulli(rate)
                status = SurvivalStatus.ALIVE.value if alive else SurvivalStatus.DEAD.value
                review = (graft_day + timedelta(days=30)).isoformat()
            graft_rows.append(
                [
                    f"{batch_code[-1]}-{graft_day.strftime('%m%d')}-{seq:04d}",
                    batch_code, rootstock, cultivar, tree_id, status,
                    review, "",
                ]
            )
    _write_csv(
        base / "grafts.csv",
        ["plant_id", "batch_code", "rootstock", "scion_cultivar",
         "mother_tree_id", "status", "review_date", "note"],
        graft_rows,
    )
    return base


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
