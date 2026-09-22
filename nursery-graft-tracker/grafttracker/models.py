"""领域模型：嫁接批次、单株记录、环境读数、母树台账、成活结论。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class SurvivalStatus(str, Enum):
    """嫁接后 30 天复检结论。"""

    ALIVE = "alived"        # 成活：萌芽展叶
    DEAD = "dead"           # 死亡：接穗干枯
    PENDING = "pending"     # 未到复检日，尚无结论

    @property
    def label(self) -> str:
        return {
            SurvivalStatus.ALIVE: "成活",
            SurvivalStatus.DEAD: "死亡",
            SurvivalStatus.PENDING: "待定",
        }[self]


class GraftMethod(str, Enum):
    """嫁接方法（影响愈合期天数口径）。"""

    CLEFT = "cleft"             # 劈接
    BUD = "bud"                 # 芽接
    WHIP = "whip"               # 切接/舌接
    APPROACH = "approach"       # 靠接

    @property
    def label(self) -> str:
        return {
            GraftMethod.CLEFT: "劈接",
            GraftMethod.BUD: "芽接",
            GraftMethod.WHIP: "切接",
            GraftMethod.APPROACH: "靠接",
        }[self]


@dataclass(frozen=True)
class Batch:
    """一个嫁接批次：同一天、同地块、同方法的一组嫁接。"""

    code: str               # 批次号，如 B2026-0318-A
    field: str              # 地块/圃地编号
    graft_date: date        # 嫁接日期
    method: GraftMethod     # 嫁接方法
    operator: str           # 作业组/责任人
    note: str = ""

    @property
    def review_date(self) -> date:
        """复检日：嫁接后第 30 天。"""
        from datetime import timedelta

        return self.graft_date + timedelta(days=30)


@dataclass(frozen=True)
class GraftRecord:
    """单株嫁接记录 = 一株砧木 × 一根穗条（来自某母树）。"""

    plant_id: str           # 植株编号（全圃唯一），如 A-0318-001
    batch_code: str
    rootstock: str          # 砧木种类，如 枳壳
    scion_cultivar: str     # 接穗品种，如 沃柑
    mother_tree_id: str     # 穗条来源母树编号，如 M-沃柑-07
    status: SurvivalStatus
    graft_date: date
    review_date: date       # 实际复检日期
    note: str = ""

    @property
    def combo_key(self) -> tuple[str, str, str]:
        """砧木 × 品种 × 母树 的组合键。"""
        return (self.rootstock, self.scion_cultivar, self.mother_tree_id)

    @property
    def scion_source_key(self) -> tuple[str, str]:
        """品种 × 母树：穗条来源维度。"""
        return (self.scion_cultivar, self.mother_tree_id)


@dataclass(frozen=True)
class EnvironmentReading:
    """地块环境读数（嫁接后愈合期逐日记录）。

    愈合期 = 嫁接日起 30 天（劈接/切接/靠接），芽接 21 天。
    """

    field: str
    day: date               # 读数日期
    temp_mean_c: float      # 日均温 ℃
    humidity_mean_pct: float  # 日均相对湿度 %
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    rain_mm: float = 0.0    # 当日降雨量


@dataclass(frozen=True)
class MotherTree:
    """采穗母树台账。"""

    tree_id: str            # M-沃柑-07
    cultivar: str           # 品种
    location: str           # 母本园位置
    age_years: float        # 树龄
    usable_sticks: int      # 来年预计可采健壮穗条（根）
    health_note: str = ""


@dataclass
class ComboStats:
    """一个嫁接组合的成活统计（分母只含已复检植株）。"""

    rootstock: str
    cultivar: str
    mother_tree_id: str
    total: int                  # 已复检株数
    alive: int
    dead: int
    rate: float                 # 成活率 0~1
    wilson_low: float           # Wilson 95% 置信区间下界（样本小时不被高成活率骗到）
    method: str = ""
    batches: list[str] = field(default_factory=list)
    # 愈合期环境（加权，按批次株数）
    avg_temp: float = 0.0
    avg_humidity: float = 0.0
    stress_days: int = 0        # 各批次胁迫天数按株数加权（天）
    stress_share: float = 0.0   # 加权胁迫天数 / 愈合期总天数
    worst_stress_share: float = 0.0  # 受冲击最重批次的胁迫占比
    stress_detail: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rootstock, self.cultivar, self.mother_tree_id)

    @property
    def low_survival(self) -> bool:
        """低成活判定：率 < 70% 且样本 >= 5；或置信下界 < 55%。"""
        if self.total < 5:
            return False
        return self.rate < 0.70 or self.wilson_low < 0.55

    @property
    def grade(self) -> str:
        if self.total < 5:
            return "样本不足"
        if self.rate >= 0.85:
            return "优"
        if self.rate >= 0.70:
            return "良"
        if self.rate >= 0.50:
            return "差"
        return "极差"
