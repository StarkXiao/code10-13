# 苗圃嫁接成活追踪系统（grafting-tracker）

记录每批嫁接的「品种 × 砧木」组合与苗床温湿度，按组合统计成活率、识别低成活组合，并据此生成来年穗条调配方案。

纯 Python 3.11+ 标准库实现（SQLite 存储），**零第三方依赖**。

## 快速开始

```bash
cd grafting-tracker

# 生成一季演示数据（2026 春季，24 个批次 + 逐日温湿度）
python3 -m grafting_tracker --db data/demo.db demo

# 成活率分析报告（识别低成活组合 + 愈伤期环境线索）
python3 -m grafting_tracker --db data/demo.db analyze

# 来年穗条调配方案
python3 -m grafting_tracker --db data/demo.db plan
```

## 日常使用

```bash
# 1. 登记嫁接批次
python3 -m grafting_tracker add-batch \
  --batch-no J001 --cultivar 红富士 --rootstock 八棱海棠 \
  --method 切接 --date 2026-03-15 --quantity 120 \
  --plot 一号棚 --operator 张工 --scion-source 本圃采穗母树

# 2. 录入逐日温湿度（或批量导入 CSV）
python3 -m grafting_tracker add-env --plot 一号棚 --date 2026-03-15 \
  --temp-avg 14.2 --temp-max 19.5 --temp-min 9.1 --humidity 72
python3 -m grafting_tracker import-env env.csv
# CSV 表头：plot,date,temp_avg,temp_max,temp_min,humidity_avg

# 3. 成活调查（同批次可多次，统计时取最新一次）
python3 -m grafting_tracker add-survey --batch-no J001 --date 2026-05-01 --alive 104

# 4. 设置品种参数：每根穗条可嫁接株数、来年目标成品苗
python3 -m grafting_tracker set-spec --cultivar 红富士 \
  --grafts-per-scion 4 --target-plants 1500

# 5. 分析与规划（--out 可输出 Markdown 文件）
python3 -m grafting_tracker analyze --out report.md
python3 -m grafting_tracker plan --growth 0.2 --buffer 0.1 --out plan.md
```

数据库路径默认 `./data/grafting.db`，可用 `--db` 或环境变量 `GRAFTING_DB` 指定。

## 方法口径

### 低成活组合识别

- 成活率按「品种 × 砧木」聚合，每批次取**最新一次调查**为定案成活数；
- 置信区间采用 **Wilson 区间**（小样本稳健），判为「低成活」需满足其一：
  - 成活率低于绝对阈值（默认 60%，`--low-threshold` 可调）；
  - Wilson 95% 区间上限低于全圃总体水平（显著偏低）；
- 组合样本量 < 30（`--min-samples`）时只标注「样本不足」，不下结论，避免小批次误判。

### 愈伤期环境线索

取每批次嫁接后 14 天（`--healing-days`）内该苗床的温湿度，统计平均温湿度与
高温/低温/干燥/高湿天数；把批次按成活率中位数分成高低两组，报告差异明显的环境因子，
作为低成活成因的线索（亲和性之外排查环境与操作因素）。

### 穗条调配方案

- 每个品种优先选用「非低成活且样本充足」的组合中 Wilson 下界最高的砧木；
- 预期成活率取 Wilson 下界（保守口径），需嫁接株数 = 目标 ÷ 预期成活率 × (1 + 安全系数)；
- 穗条需求 = 需嫁接株数 ÷ 每根穗条可嫁接株数（按品种配置，默认 4）；
- 全部组合低成活的品种会标注 ⚠ 并按最保守口径备料，提示改进工艺或试配新砧木；
- 未设目标的品种按今年嫁接量 × (1 + `--growth`) 推算。

## 数据模型

| 表 | 内容 |
| --- | --- |
| `cultivar` / `rootstock` / `plot` | 品种 / 砧木 / 苗床字典 |
| `graft_batch` | 嫁接批次：组合、方法、日期、株数、苗床、嫁接人、穗条来源 |
| `survival_survey` | 成活调查（同批次同日唯一，重复录入覆盖） |
| `env_record` | 逐日温湿度（按苗床，同日覆盖） |
| `scion_spec` | 品种穗条参数与来年目标成品苗 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖：Wilson 区间数值、低成活/表现优/样本不足判定、最新调查取值、
愈伤期窗口聚合、低成活组合排除、全低品种降级、无数据品种默认口径、穗条换算取整。
