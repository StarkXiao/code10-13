# 苗圃嫁接成活追踪系统（nursery-graft-tracker）

记录每株嫁接的**砧木 × 接穗品种 × 穗条母树**组合与嫁接后愈合期的**温湿度**，
按批次做同批对照，识别低成活组合并区分「天灾还是组合不行」，最终生成**来年穗条调配方案**。

零依赖（Python ≥ 3.10 标准库即可），数据全部是 UTF-8 CSV，Excel 可直接编辑。

---

## 闭环

```
嫁接当日建批次（地块/方法/作业组）
   ↓
逐株登记：植株号、砧木、品种、穗条来源母树
   ↓
愈合期逐日记录地块温湿度（30 天，芽接 21 天）
   ↓
第 30 天复检：成活 / 死亡（未复检的 pending 不进分母）
   ↓
按「砧木 × 品种 × 母树」聚合成活率（Wilson 95% 置信下界排序）
   ↓
低成活组合 + 同批对照归因（环境主导 / 亲和性 / 穗源 / 共同作用）
   ↓
来年方案：停采母树、优先母树调穗、产能缺口、建议嫁接窗口
```

## 快速开始

```bash
cd nursery-graft-tracker

# 生成演示数据（柑橘嫁接，含倒春寒/热浪/亲和差/产能缺口等场景）
python3 -m grafttracker demo --data-dir ./data

# 全圃总览 + 低成活组合
python3 -m grafttracker summary --data-dir ./data

# 组合成活率导出 CSV
python3 -m grafttracker combos --data-dir ./data --format csv

# 死亡单株明细
python3 -m grafttracker plants --data-dir ./data --dead

# 来年穗条调配方案（Markdown）；不给 --targets 时默认与今年规模持平
python3 -m grafttracker plan --data-dir ./data --year 2027 \
  --targets examples/targets.csv -o examples/plan-2027.md

# 测试
python3 -m unittest discover -s tests
```

## 数据文件（`--data-dir` 目录下四份 CSV）

| 文件 | 关键字段 |
| --- | --- |
| `batches.csv` | `code` 批次号、`field` 地块、`graft_date`、`method`（cleft/bud/whip/approach）、`operator` |
| `grafts.csv` | `plant_id`、`batch_code`、`rootstock`、`scion_cultivar`、`mother_tree_id`、`status`（alived/dead/pending）、`review_date` |
| `environment.csv` | `field`、`day`、`temp_mean_c`、`humidity_mean_pct`、可选 `temp_min_c/temp_max_c/rain_mm` |
| `mother_trees.csv` | `tree_id`、`cultivar`、`location`、`age_years`、`usable_sticks` 来年可采穗条数 |

加载时做完整交叉校验：批次/母树引用必须存在、母树品种必须与接穗一致、
植株号不重复、湿度 0–100、环境读数只能属于有批次的地块。

来年计划量文件（可选）：`cultivar,rootstock,qty`，见 `examples/targets.csv`。

## 判定口径（避免误判的关键设计）

1. **未复检植株不进分母**：`pending` 单列，绝不当成死亡或成活。
2. **Wilson 95% 置信下界排序**：5 株活 4 株的「80%」不会排在 100 株活 80 株前面；
   样本 < 5 的组合即使率低也不判低成活。
3. **低成活判定**：样本 ≥5 且（成活率 <70% 或置信下界 <55%）。
4. **环境胁迫按愈合窗口算**：高温低湿（≥32℃ 且 ≤60%）、低温（<13℃）、
   暴雨（≥25mm）算单日胁迫；连续 3 天 >28℃ 且 <70% 湿度补记干热连段。
5. **归因必须有同批对照**：
   - 同批最优对照也 <70% → **环境主导**，母树不背锅、不停采；
   - 同批对照 ≥75% 且高出 25 个点、它独差（率<45%）→ **亲和性风险**，建议换砧木复试；
   - 率 45–70% 区间独差 → **穗源风险**，停采复检母树；
   - 介于其间 → **环境+组合**，列入复试观察、限产不停采。
6. **跨批次环境用株数加权**，同时保留「峰值胁迫占比」，避免热浪批次被正常批次稀释。

## 调配方案算法

- 每个「品种 × 砧木」需求 = 计划量 × 1.10 安全余量；每根穗条按 3 接芽换算。
- 母树排序：同砧木实测成活率 > 跨砧木推算 > 无记录先验（50%，表中标注证据等级）。
- 停采母树完全不参与；候选母树按优先级依次吃满产能。
- 产能不足时启用未投产母树（先验），仍不足则输出缺口株数/穗条数与外部调剂建议。
- 建议嫁接窗口：对今年 2/15–4/15 的所有 30 天窗口按胁迫天数最少、
  均温接近 24℃、湿度接近 75% 择优，平移到目标年份给出各地块起嫁日。

## 目录

```text
nursery-graft-tracker/
├─ grafttracker/
│  ├─ models.py        # 批次/单株/环境/母树/组合统计
│  ├─ storage.py       # CSV 加载与交叉校验
│  ├─ environment.py   # 愈合窗口与胁迫识别
│  ├─ analytics.py     # Wilson 区间、聚合、低成活、同批对照归因
│  ├─ planning.py      # 停采/优先级/产能/缺口/嫁接窗口
│  ├─ reporting.py     # 终端表格、CSV、Markdown 方案
│  ├─ demo.py          # 确定性演示数据生成
│  └─ cli.py           # 命令行入口
├─ examples/           # 计划量样例与生成的方案
└─ tests/              # 35 项 unittest（统计口径/胁迫/校验/方案）
```
