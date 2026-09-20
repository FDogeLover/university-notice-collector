# -*- coding: utf-8 -*-
"""数据库定时备份：`sqlite3 .backup` 语义的在线备份 + 保留 N 天 + 校验。

为什么不能 cp：库跑在 WAL 模式下，直接复制文件可能拿到没有 -wal 的半截
数据（`data/university.db` 是 8961 条通知 + 正文快照的唯一副本，丢了不可再生）。

用法：
    python scripts/backup_db.py                    # 默认备份到 仓库上级/backups
    python scripts/backup_db.py --keep 14          # 保留 14 天
    python scripts/backup_db.py --dir /data/backup --label daily
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="SQLite 在线备份 + 保留策略")
    ap.add_argument("--dir", default=str(ROOT.parent / "backups"),
                    help="备份目录（默认放仓库外，避免被 reset/clean 波及）")
    ap.add_argument("--keep", type=int, default=7, help="保留天数")
    ap.add_argument("--label", default="daily", help="备份文件名前缀")
    ap.add_argument("--db", default=None, help="源库路径（默认 data/university.db）")
    args = ap.parse_args()

    src_path = Path(args.db) if args.db else store.get_db_path()
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst_path = out_dir / f"{args.label}-{stamp}.db"

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)          # 在线备份：包含 WAL 里未落盘的事务
    finally:
        dst.close()
        src.close()

    size = dst_path.stat().st_size
    chk = sqlite3.connect(str(dst_path))
    try:
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        notices = chk.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
    finally:
        chk.close()

    cutoff = time.time() - args.keep * 86400
    removed = 0
    for old in out_dir.glob(f"{args.label}-*.db"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            removed += 1
    print(f"备份完成：{dst_path}（{size / 1048576:.1f} MB，{notices} 条通知，"
          f"integrity={integrity}）；清理过期 {removed} 个，目录保留 "
          f"{len(list(out_dir.glob(args.label + '-*.db')))} 个")
    if integrity != "ok" or notices == 0:
        raise SystemExit("备份校验失败：integrity=%s notices=%s" % (integrity, notices))


if __name__ == "__main__":
    main()
