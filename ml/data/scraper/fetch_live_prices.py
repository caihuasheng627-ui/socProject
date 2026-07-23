#!/usr/bin/env python3
"""
多平台 CS2 饰品实时行情采集
============================
聚合 Skinport / BUFF / Steam / Waxpeer / MarketCSGO / Lootfarm /
CSGOTrader / CSFloat 当前报价, 输出统一 CSV / JSON。

用法示例:
  # 默认读 docs/expo/seed_portfolio.json 名单 + 免 Cookie 批量平台
  python fetch_live_prices.py --platforms skinport,waxpeer,marketcsgo,lootfarm,csgotrader

  # 显式指定 docs 目录
  python fetch_live_prices.py --from-docs docs/expo --spread

  # 含需 Cookie / 限流平台
  export BUFF_SESSION='你的 buff.163.com session cookie'
  python fetch_live_prices.py \\
      --platforms skinport,buff,steam,csfloat \\
      --items "AK-47 | Redline (Field-Tested)" "AWP | Asiimov (Field-Tested)"

  # 从训练集 CSV 取前 20 件, 持续轮询
  python fetch_live_prices.py \\
      --from-csv ../training_dataset.csv --limit 20 \\
      --platforms waxpeer,marketcsgo --watch --interval 120

  # 跨平台价差
  python fetch_live_prices.py --platforms skinport,waxpeer,marketcsgo,lootfarm --spread

环境变量:
  BUFF_SESSION     BUFF Cookie `session` 值(F12 → Application → Cookies)
  USD_CNY_RATE     CNY→USD 汇率, 默认 7.2(仅 BUFF 换算)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# Windows / pipe 友好
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent
DEFAULT_OUT = DATA_DIR / "live_quotes.csv"

sys.path.insert(0, str(HERE))

from platforms import (  # noqa: E402
    DEFAULT_WATCHLIST,
    PLATFORM_REGISTRY,
    Quote,
    build_clients,
    load_names_from_csv,
    load_names_from_docs,
    utc_now_iso,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Crawl real-time CS2 skin quotes from multiple marketplaces",
    )
    p.add_argument(
        "--platforms",
        default="skinport,waxpeer,marketcsgo",
        help=f"逗号分隔平台列表, 可选: {','.join(PLATFORM_REGISTRY)}",
    )
    p.add_argument("--items", nargs="*", help="market_hash_name 列表; 省略则用 docs/expo 名单")
    p.add_argument(
        "--from-docs",
        nargs="?",
        const="docs/expo",
        default=None,
        help="从 docs/expo 种子读取名单(默认路径 docs/expo; 与 --items/--from-csv 互斥优先 items)",
    )
    p.add_argument("--from-csv", help="从 CSV 读取物品名(列 market_hash_name)")
    p.add_argument("--limit", type=int, default=0, help="最多采集 N 件(0=全部)")
    p.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUT),
        help=f"输出 CSV 路径(默认 {DEFAULT_OUT})",
    )
    p.add_argument("--json", dest="json_path", help="额外输出 JSON 快照路径")
    p.add_argument("--append", action="store_true", help="追加写入 CSV(用于 --watch 历史)")
    p.add_argument("--watch", action="store_true", help="持续轮询")
    p.add_argument("--interval", type=float, default=60.0, help="--watch 轮询间隔秒")
    p.add_argument("--rounds", type=int, default=0, help="--watch 最多轮数(0=无限)")
    p.add_argument("--spread", action="store_true", help="打印跨平台价差摘要")
    p.add_argument("--buff-session", default=os.getenv("BUFF_SESSION", ""), help="覆盖 BUFF_SESSION")
    p.add_argument("--usd-cny-rate", type=float, default=float(os.getenv("USD_CNY_RATE", "7.2")))
    p.add_argument("--buff-interval", type=float, default=1.2)
    p.add_argument("--steam-interval", type=float, default=3.0)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def resolve_names(args: argparse.Namespace) -> list[str]:
    if args.items:
        names = list(args.items)
    elif args.from_csv:
        names = load_names_from_csv(args.from_csv)
    else:
        # 默认 / --from-docs: 读 docs/expo
        docs_path = args.from_docs
        if docs_path is None:
            # 相对仓库根
            repo = Path(__file__).resolve().parents[3]
            docs_path = str(repo / "docs" / "expo")
        elif not Path(docs_path).is_absolute():
            # 相对调用 cwd 或仓库根都试一次
            cand = Path(docs_path)
            if not cand.exists():
                cand = Path(__file__).resolve().parents[3] / docs_path
            docs_path = str(cand)
        names = load_names_from_docs(docs_path)
        if not names and DEFAULT_WATCHLIST:
            names = list(DEFAULT_WATCHLIST)
    if args.limit and args.limit > 0:
        names = names[: args.limit]
    return names


CSV_FIELDS = [
    "fetched_at",
    "platform",
    "market_hash_name",
    "currency",
    "price",
    "price_native",
    "buy_price",
    "sell_price",
    "volume",
    "ok",
    "error",
]


def quotes_to_rows(quotes: list[Quote]) -> list[dict]:
    rows = []
    for q in quotes:
        rows.append(
            {
                "fetched_at": q.fetched_at,
                "platform": q.platform,
                "market_hash_name": q.market_hash_name,
                "currency": q.currency,
                "price": q.price,
                "price_native": q.price_native,
                "buy_price": q.buy_price,
                "sell_price": q.sell_price,
                "volume": q.volume,
                "ok": int(bool(q.ok and q.price is not None)),
                "error": q.error or "",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not (append and path.exists() and path.stat().st_size > 0)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(rows)


def write_json(path: Path, quotes: list[Quote]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": utc_now_iso(),
        "count": len(quotes),
        "ok": sum(1 for q in quotes if q.ok and q.price is not None),
        "quotes": [
            {**q.to_dict(), "extra": q.extra}
            for q in quotes
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def print_table(quotes: list[Quote], quiet: bool = False) -> None:
    if quiet:
        return
    ok = [q for q in quotes if q.ok and q.price is not None]
    fail = [q for q in quotes if not (q.ok and q.price is not None)]
    print(f"\n{'platform':<10} {'price':>10} {'vol':>6}  name")
    print("-" * 72)
    for q in ok:
        vol = "" if q.volume is None else str(q.volume)
        print(f"{q.platform:<10} {q.price:>10.2f} {vol:>6}  {q.market_hash_name}")
    if fail:
        print(f"\n失败 {len(fail)} 条:")
        for q in fail[:20]:
            print(f"  [{q.platform}] {q.market_hash_name}: {q.error}")
        if len(fail) > 20:
            print(f"  ... 另有 {len(fail) - 20} 条")
    print(f"\n成功 {len(ok)}/{len(quotes)}")


def print_spread(quotes: list[Quote]) -> None:
    by_name: dict[str, dict[str, float]] = defaultdict(dict)
    for q in quotes:
        if q.ok and q.price is not None:
            by_name[q.market_hash_name][q.platform] = q.price
    print("\n=== 跨平台价差(USD) ===")
    print(f"{'item':<48} {'platforms':<28} spread%")
    print("-" * 90)
    for name, m in sorted(by_name.items()):
        if len(m) < 2:
            continue
        prices = list(m.values())
        lo, hi = min(prices), max(prices)
        spread = (hi - lo) / lo * 100 if lo > 0 else 0.0
        plat = " | ".join(f"{k}={v:.2f}" for k, v in sorted(m.items()))
        short = name if len(name) <= 46 else name[:43] + "..."
        print(f"{short:<48} {plat:<28} {spread:6.2f}%")


def run_once(args: argparse.Namespace, names: list[str]) -> list[Quote]:
    platforms = [x.strip() for x in args.platforms.split(",") if x.strip()]
    clients = build_clients(
        platforms,
        buff_session=args.buff_session,
        usd_cny_rate=args.usd_cny_rate,
        buff_interval=args.buff_interval,
        steam_interval=args.steam_interval,
    )
    all_quotes: list[Quote] = []
    try:
        for client in clients:
            if not args.quiet:
                print(f"[{utc_now_iso()}] fetching {client.name} × {len(names)} ...")
            try:
                qs = client.fetch_quotes(names)
            except Exception as e:
                if not args.quiet:
                    print(f"  ERROR {client.name}: {e}")
                qs = [
                    Quote(
                        market_hash_name=n,
                        platform=client.name,
                        currency="USD",
                        price=None,
                        ok=False,
                        error=str(e),
                    )
                    for n in names
                ]
            all_quotes.extend(qs)
            ok_n = sum(1 for q in qs if q.ok and q.price is not None)
            if not args.quiet:
                print(f"  {client.name}: {ok_n}/{len(qs)} ok")
    finally:
        for c in clients:
            c.close()
    return all_quotes


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    names = resolve_names(args)
    if not names:
        print("ERROR: 无物品可采集", file=sys.stderr)
        return 2
    if not args.quiet:
        print(f"物品 {len(names)} 件 | 平台 {args.platforms}")

    out_path = Path(args.output)
    round_i = 0
    while True:
        round_i += 1
        quotes = run_once(args, names)
        rows = quotes_to_rows(quotes)
        write_csv(out_path, rows, append=args.append or (args.watch and round_i > 1))
        if args.json_path:
            write_json(Path(args.json_path), quotes)
        print_table(quotes, quiet=args.quiet)
        if args.spread:
            print_spread(quotes)
        if not args.quiet:
            print(f"已写入: {out_path}")
            if args.json_path:
                print(f"JSON:   {args.json_path}")

        if not args.watch:
            break
        if args.rounds and round_i >= args.rounds:
            break
        if not args.quiet:
            print(f"等待 {args.interval}s 后下一轮 ...")
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
