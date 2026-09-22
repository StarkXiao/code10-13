"""命令行入口：python -m grafting_tracker <command> ..."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

from . import db as gdb
from .analysis import (DEFAULT_HEALING_DAYS, DEFAULT_LOW_THRESHOLD,
                       DEFAULT_MIN_SAMPLES, env_insights,
                       load_batch_env_stats, load_combo_stats, overview)
from .planning import build_plan, rootstock_summary
from .report import render_analysis, render_plan

DEFAULT_DB = "./data/grafting.db"


def _resolve_cultivar(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM cultivar WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"品种不存在: {name}（请先通过 add-batch 登记）")
    return row["id"]


def _resolve_batch(conn, batch_no: str) -> int:
    row = conn.execute("SELECT id FROM graft_batch WHERE batch_no = ?",
                       (batch_no,)).fetchone()
    if row is None:
        raise ValueError(f"批次不存在: {batch_no}")
    return row["id"]


def _check_date(s: str) -> str:
    date.fromisoformat(s)  # 格式非法时抛 ValueError
    return s


def _emit(text: str, out: str | None) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
        print(f"已写入 {out}")
    else:
        print(text)


def cmd_init(conn, args):
    print(f"数据库已就绪: {args.db}")


def cmd_add_batch(conn, args):
    _check_date(args.date)
    cid = gdb.get_or_create(conn, "cultivar", args.cultivar, species=args.species)
    rid = gdb.get_or_create(conn, "rootstock", args.rootstock)
    pid = gdb.get_or_create(conn, "plot", args.plot) if args.plot else None
    bid = gdb.add_batch(conn, args.batch_no, cid, rid, args.method, args.date,
                        args.quantity, pid, args.operator, args.scion_source)
    conn.commit()
    print(f"批次 {args.batch_no} 已登记（id={bid}，{args.cultivar} × "
          f"{args.rootstock}，{args.quantity} 株）")


def cmd_add_survey(conn, args):
    _check_date(args.date)
    bid = _resolve_batch(conn, args.batch_no)
    gdb.add_survey(conn, bid, args.date, args.alive, args.surveyor)
    conn.commit()
    print(f"批次 {args.batch_no} 调查已登记：{args.date} 成活 {args.alive} 株")


def cmd_add_env(conn, args):
    _check_date(args.date)
    pid = gdb.get_or_create(conn, "plot", args.plot)
    gdb.upsert_env(conn, pid, args.date, args.temp_avg, args.temp_max,
                   args.temp_min, args.humidity)
    conn.commit()
    print(f"{args.plot} {args.date} 环境数据已登记")


def cmd_import_env(conn, args):
    n = 0
    with open(args.csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pid = gdb.get_or_create(conn, "plot", row["plot"].strip())
            gdb.upsert_env(conn, pid, _check_date(row["date"].strip()),
                           float(row["temp_avg"]), float(row["temp_max"]),
                           float(row["temp_min"]), float(row["humidity_avg"]))
            n += 1
    conn.commit()
    print(f"已导入 {n} 条环境记录")


def cmd_set_spec(conn, args):
    cid = _resolve_cultivar(conn, args.cultivar)
    if args.grafts_per_scion is None and args.target_plants is None:
        raise ValueError("至少给出 --grafts-per-scion 或 --target-plants 之一")
    gdb.set_scion_spec(conn, cid, args.grafts_per_scion, args.target_plants)
    conn.commit()
    print(f"品种 {args.cultivar} 穗条参数已更新")


def cmd_analyze(conn, args):
    ov = overview(conn)
    stats = load_combo_stats(conn, args.min_samples, args.low_threshold)
    env_stats = load_batch_env_stats(conn, args.healing_days)
    text = render_analysis(ov, stats, env_insights(env_stats),
                           args.healing_days)
    _emit(text, args.out)


def cmd_plan(conn, args):
    stats = load_combo_stats(conn)
    rows = build_plan(conn, stats, growth=args.growth, buffer=args.buffer,
                      default_rate=args.default_rate,
                      default_grafts_per_scion=args.grafts_per_scion)
    row = conn.execute("SELECT MAX(graft_date) AS d FROM graft_batch").fetchone()
    next_year = (date.fromisoformat(row["d"]).year + 1
                 if row and row["d"] else date.today().year + 1)
    text = render_plan(rows, rootstock_summary(rows), next_year,
                       args.growth, args.buffer)
    _emit(text, args.out)


def cmd_demo(conn, args):
    from .demo import make_demo_data
    info = make_demo_data(conn)
    print(f"演示数据已生成：{info['batches']} 个批次、{info['combos']} 个组合、"
          "2026-03-01 ~ 05-31 逐日环境数据")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grafting-tracker",
        description="苗圃嫁接成活追踪系统：记录嫁接组合与温湿度，"
                    "识别低成活组合，生成来年穗条调配方案")
    parser.add_argument("--db", default=os.environ.get("GRAFTING_DB", DEFAULT_DB),
                        help=f"SQLite 数据库路径（默认 {DEFAULT_DB}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化数据库").set_defaults(func=cmd_init)

    p = sub.add_parser("add-batch", help="录入嫁接批次")
    p.add_argument("--batch-no", required=True, help="批次号（唯一）")
    p.add_argument("--cultivar", required=True, help="接穗品种")
    p.add_argument("--rootstock", required=True, help="砧木")
    p.add_argument("--method", required=True, help="嫁接方法，如 切接/劈接/舌接/T形芽接")
    p.add_argument("--date", required=True, help="嫁接日期 YYYY-MM-DD")
    p.add_argument("--quantity", type=int, required=True, help="嫁接株数")
    p.add_argument("--plot", default=None, help="苗床/地块")
    p.add_argument("--operator", default="", help="嫁接人")
    p.add_argument("--scion-source", default="", help="穗条来源")
    p.add_argument("--species", default="", help="树种（新品种首次登记时填写）")
    p.set_defaults(func=cmd_add_batch)

    p = sub.add_parser("add-survey", help="录入成活调查")
    p.add_argument("--batch-no", required=True)
    p.add_argument("--date", required=True, help="调查日期 YYYY-MM-DD")
    p.add_argument("--alive", type=int, required=True, help="成活株数")
    p.add_argument("--surveyor", default="", help="调查人")
    p.set_defaults(func=cmd_add_survey)

    p = sub.add_parser("add-env", help="录入单日温湿度")
    p.add_argument("--plot", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--temp-avg", type=float, required=True)
    p.add_argument("--temp-max", type=float, required=True)
    p.add_argument("--temp-min", type=float, required=True)
    p.add_argument("--humidity", type=float, required=True, help="平均湿度 %%")
    p.set_defaults(func=cmd_add_env)

    p = sub.add_parser("import-env", help="从 CSV 批量导入环境数据"
                       "（列：plot,date,temp_avg,temp_max,temp_min,humidity_avg）")
    p.add_argument("csv_path")
    p.set_defaults(func=cmd_import_env)

    p = sub.add_parser("set-spec", help="设置品种穗条参数/来年目标")
    p.add_argument("--cultivar", required=True)
    p.add_argument("--grafts-per-scion", type=float, default=None,
                   help="每根穗条可嫁接株数")
    p.add_argument("--target-plants", type=int, default=None,
                   help="来年目标成品苗（株）")
    p.set_defaults(func=cmd_set_spec)

    p = sub.add_parser("analyze", help="成活率分析，识别低成活组合")
    p.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    p.add_argument("--low-threshold", type=float, default=DEFAULT_LOW_THRESHOLD)
    p.add_argument("--healing-days", type=int, default=DEFAULT_HEALING_DAYS)
    p.add_argument("--out", default=None, help="报告输出路径（默认打印）")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("plan", help="生成来年穗条调配方案")
    p.add_argument("--growth", type=float, default=0.2,
                   help="未设目标品种的增长率（默认 0.2）")
    p.add_argument("--buffer", type=float, default=0.1,
                   help="安全系数（默认 0.1）")
    p.add_argument("--default-rate", type=float, default=0.5,
                   help="无历史数据品种的默认成活率")
    p.add_argument("--grafts-per-scion", type=float, default=4.0,
                   help="默认每根穗条可嫁接株数")
    p.add_argument("--out", default=None, help="方案输出路径（默认打印）")
    p.set_defaults(func=cmd_plan)

    sub.add_parser("demo", help="生成一季演示数据").set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    conn = gdb.init_db(args.db)
    try:
        args.func(conn, args)
    except (ValueError, gdb.sqlite3.IntegrityError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
