# -*- coding: utf-8 -*-
"""回滚"可疑日期置空"里的误判：页面确实写着那个日期的，把原值放回去。

用法（先跑 --no-list --blank-suspicious 生成 -applied-*.csv，再用它复核）：
    python scripts/recheck_blanked.py data/fix_published_dates-applied-*.csv
    加 --apply 真正写回；缺省只报告。
"""
import argparse
import csv
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from db import store  # noqa: E402
from fix_published_dates import page_has_date  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="复核置空行：页面有日期就放回")
    ap.add_argument("csvs", nargs="+", help="fix_published_dates-applied-*.csv")
    ap.add_argument("--apply", action="store_true", help="写回数据库")
    args = ap.parse_args()

    conn = store.connect()
    rows = []
    for pattern in args.csvs:
        for path in glob.glob(pattern):
            for r in csv.DictReader(open(path, encoding="utf-8-sig")):
                if r["reason"] == "可疑日期置空" and r["old"]:
                    rows.append(r)
    print(f"待复核的置空行：{len(rows)} 条")
    keep, restore, unknown = [], [], []
    for r in rows:
        hit = page_has_date(r["url"], r["old"][:10])
        if hit is True:
            restore.append(r)
        elif hit is False:
            keep.append(r)
        else:
            unknown.append(r)
    print(f"  页面确实写着该日期（应放回）: {len(restore)}")
    print(f"  页面没有该日期（置空正确）  : {len(keep)}")
    print(f"  页面抓不到（保持现状）      : {len(unknown)}")
    for r in restore[:10]:
        print(f"    ↩ {r['school']} {r['old']} | {r['title'][:38]}")
    if args.apply and restore:
        for r in restore:
            conn.execute("UPDATE notices SET published_at=? WHERE id=?",
                         (r["old"], r["id"]))
        conn.commit()
        print(f"已放回 {len(restore)} 条")
    conn.close()


if __name__ == "__main__":
    main()
