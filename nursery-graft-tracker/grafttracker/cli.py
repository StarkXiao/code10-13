"""命令行入口。

用法示例：
  python -m grafttracker demo --data-dir ./data        # 生成演示数据
  python -m grafttracker summary                        # 总览 + 低成活组合
  python -m grafttracker combos --format csv           # 组合成活率（可导出）
  python -m grafttracker plants --dead                 # 死亡单株明细
  python -m grafttracker plan --year 2027              # 来年穗条调配方案
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .analytics import aggregate
from .planning import build_plan, load_targets
from .reporting import (
    COMBO_HEADERS,
    combo_rows,
    combos_csv,
    plan_markdown,
    plants_text,
    render_table,
    summary_text,
    write_file,
)
from .storage import DataError, Dataset, load


def _load(args: argparse.Namespace) -> Dataset:
    try:
        return load(args.data_dir)
    except DataError as exc:
        print(f"数据错误：{exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_summary(args: argparse.Namespace) -> None:
    print(summary_text(_load(args)))


def cmd_combos(args: argparse.Namespace) -> None:
    ds = _load(args)
    if args.format == "csv":
        print(combos_csv(ds), end="")
        return
    print(render_table(COMBO_HEADERS, combo_rows(aggregate(ds))))


def cmd_plants(args: argparse.Namespace) -> None:
    print(plants_text(_load(args), only_dead=args.dead, limit=args.limit))


def cmd_plan(args: argparse.Namespace) -> None:
    ds = _load(args)
    targets = load_targets(args.targets) if args.targets else None
    plan = build_plan(ds, year=args.year, targets=targets)
    md = plan_markdown(ds, plan)
    if args.output:
        write_file(args.output, md)
        print(f"方案已写入 {args.output}")
    else:
        print(md)


def cmd_demo(args: argparse.Namespace) -> None:
    from .demo import generate

    path = generate(args.data_dir, force=args.force)
    print(f"演示数据已生成于 {path}")
    print("接下来可运行：")
    print(f"  python -m grafttracker --data-dir {path} summary")
    print(f"  python -m grafttracker --data-dir {path} plan --year {date.today().year + 1}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grafttracker",
        description="苗圃嫁接成活追踪与来年穗条调配",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--data-dir", default="data", help="数据目录（默认 ./data）"
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        # SUPPRESS：子命令后未写 --data-dir 时不覆盖全局默认
        sp.add_argument("--data-dir", default=argparse.SUPPRESS,
                        help="数据目录（同全局参数，位置不限）")

    sp = sub.add_parser("summary", help="全圃总览 + 低成活组合")
    add_common(sp)
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser("combos", help="按砧木×品种×母树输出成活率")
    add_common(sp)
    sp.add_argument("--format", choices=["table", "csv"], default="table")
    sp.set_defaults(func=cmd_combos)

    sp = sub.add_parser("plants", help="单株记录明细")
    add_common(sp)
    sp.add_argument("--dead", action="store_true", help="只看死亡株")
    sp.add_argument("--limit", type=int, default=200, help="最多显示行数")
    sp.set_defaults(func=cmd_plants)

    sp = sub.add_parser("plan", help="生成来年穗条调配方案（Markdown）")
    add_common(sp)
    sp.add_argument("--year", type=int, default=date.today().year + 1)
    sp.add_argument("--targets", help="计划量 CSV：cultivar,rootstock,qty")
    sp.add_argument("--output", "-o", help="输出到文件（默认打印）")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("demo", help="生成一套演示数据")
    add_common(sp)
    sp.add_argument("--force", action="store_true", help="目录非空也覆盖")
    sp.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
