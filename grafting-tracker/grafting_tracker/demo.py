"""生成一季可复现的演示数据（2026 年春季嫁接季）。

演示数据内嵌两类“真相”，用于验证系统能否识别：
- 低亲和组合：红富士×山定子（≈55%）、翠冠×豆梨（≈58%）；
- 环境胁迫：2026-04-10 ~ 04-18 热浪，愈伤期覆盖该时段的批次成活率被压低。
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from . import db as gdb

CULTIVARS = [("红富士", "苹果"), ("嘎啦", "苹果"), ("翠冠", "梨"), ("黄金梨", "梨")]
ROOTSTOCKS = ["八棱海棠", "山定子", "杜梨", "豆梨"]
PLOTS = ["一号棚", "二号棚", "露地A区"]
METHODS = ["切接", "劈接", "舌接"]

# 各组合的“真实”亲和性成活率（演示用隐藏参数）
COMBO_RATE = {
    ("红富士", "八棱海棠"): 0.88,
    ("红富士", "山定子"): 0.55,   # 低亲和
    ("嘎啦",   "八棱海棠"): 0.85,
    ("嘎啦",   "山定子"): 0.80,
    ("翠冠",   "杜梨"):   0.90,
    ("翠冠",   "豆梨"):   0.58,   # 低亲和
    ("黄金梨", "杜梨"):   0.83,
    ("黄金梨", "豆梨"):   0.78,
}

SEASON_START = date(2026, 3, 1)
SEASON_END = date(2026, 5, 31)
HEATWAVE_START = date(2026, 4, 10)
HEATWAVE_END = date(2026, 4, 18)
HEATWAVE_TEMP_BOOST = 9.0      # 热浪期间日均温抬升（℃）
HEATWAVE_HUMIDITY_DROP = 10.0  # 热浪期间湿度下降（%）
HEATWAVE_PENALTY = 0.10        # 愈伤期覆盖热浪的批次成活率惩罚


def _season_temp(d: date) -> float:
    """3 月初 8℃ → 5 月底 24℃ 的简易季节曲线。"""
    t = (d - SEASON_START).days / 91.0
    return 8.0 + 16.0 * t


def make_demo_data(conn, seed: int = 42) -> dict:
    rng = random.Random(seed)
    plots = {n: gdb.get_or_create(conn, "plot", n) for n in PLOTS}
    cult_ids = {n: gdb.get_or_create(conn, "cultivar", n, species=s)
                for n, s in CULTIVARS}
    root_ids = {n: gdb.get_or_create(conn, "rootstock", n) for n in ROOTSTOCKS}

    # 1) 环境数据：逐日、逐苗床
    plot_offset = {"一号棚": 1.5, "二号棚": 1.0, "露地A区": 0.0}
    day = SEASON_START
    while day <= SEASON_END:
        in_heatwave = HEATWAVE_START <= day <= HEATWAVE_END
        for name, pid in plots.items():
            base = _season_temp(day) + plot_offset[name]
            if in_heatwave:
                base += HEATWAVE_TEMP_BOOST
            tavg = base + rng.gauss(0, 1.2)
            tmax = tavg + rng.uniform(4, 7)
            tmin = tavg - rng.uniform(3, 6)
            hum = 78 - (tavg - 15) * 1.8 + rng.gauss(0, 6)
            if in_heatwave:
                hum -= HEATWAVE_HUMIDITY_DROP
            gdb.upsert_env(conn, pid, day.isoformat(),
                           round(tavg, 1), round(tmax, 1), round(tmin, 1),
                           round(min(100.0, max(30.0, hum)), 1))
        day += timedelta(days=1)

    # 2) 嫁接批次：每组合 3 批
    batch_seq = 0
    for (cult, root), rate in COMBO_RATE.items():
        for _ in range(3):
            batch_seq += 1
            gdate = date(2026, 3, 8) + timedelta(days=rng.randint(0, 45))
            qty = rng.randint(80, 160)
            heal_end = gdate + timedelta(days=13)
            penalty = (HEATWAVE_PENALTY
                       if gdate <= HEATWAVE_END and heal_end >= HEATWAVE_START
                       else 0.0)
            p = min(0.98, max(0.05, rate - penalty + rng.gauss(0, 0.03)))
            alive1 = sum(1 for _ in range(qty) if rng.random() < p)
            bid = gdb.add_batch(
                conn, f"J{batch_seq:03d}", cult_ids[cult], root_ids[root],
                rng.choice(METHODS), gdate.isoformat(), qty,
                plots[rng.choice(PLOTS)],
                operator=rng.choice(["张工", "李工", "王工"]),
                scion_source="本圃采穗母树")
            # 3) 两次调查：+45 天、+90 天（第二次少量衰亡）
            gdb.add_survey(conn, bid, (gdate + timedelta(days=45)).isoformat(),
                           alive1, surveyor="赵记录")
            alive2 = sum(1 for _ in range(alive1) if rng.random() < 0.98)
            gdb.add_survey(conn, bid, (gdate + timedelta(days=90)).isoformat(),
                           alive2, surveyor="赵记录")

    # 4) 穗条参数与部分品种的来年目标（苹果类每穗条约接 4 株，梨类约 5 株）
    species_of = dict(CULTIVARS)
    for cult, cid in cult_ids.items():
        gdb.set_scion_spec(conn, cid,
                           grafts_per_scion=4.0 if species_of[cult] == "苹果"
                           else 5.0)
    gdb.set_scion_spec(conn, cult_ids["红富士"], target_plants=1500)
    gdb.set_scion_spec(conn, cult_ids["翠冠"], target_plants=1200)
    conn.commit()
    return {"batches": batch_seq, "combos": len(COMBO_RATE)}
