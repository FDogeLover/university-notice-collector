# -*- coding: utf-8 -*-
"""查询工具：从采集库检索通知（每条都带官方原文链接，可溯源）。

用法：
    python query.py --keyword 推免
    python query.py --keyword 推免 --school 深圳大学
    python query.py --recent --school 深圳大学 --limit 20
    python query.py --keyword 预推免 --limit 100
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from db import store  # noqa: E402


def dump(rows):
    if not rows:
        print("（无结果）")
        return
    for n in rows:
        print(f"[{n['school_name']} | {n['source_name'] or '-'} | "
              f"{n['type_tag'] or '-'} | 抓取于 {n['fetched_at']}]")
        print(f"  标题：{n['title']}")
        print(f"  原文：{n['url']}")
        if n["published_at"]:
            print(f"  发布时间：{n['published_at']}")
        print()


def main():
    ap = argparse.ArgumentParser(description="检索采集库中的高校通知")
    ap.add_argument("--keyword", help="关键词（标题/正文子串匹配）")
    ap.add_argument("--school", help="限定学校")
    ap.add_argument("--recent", action="store_true", help="按最近入库查询")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    conn = store.connect()
    if args.recent:
        dump(store.recent(conn, school_name=args.school, limit=args.limit))
    elif args.keyword:
        dump(store.search(conn, args.keyword, school_name=args.school,
                          limit=args.limit))
    else:
        ap.print_help()
    conn.close()


if __name__ == "__main__":
    main()
