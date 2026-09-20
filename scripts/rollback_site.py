# -*- coding: utf-8 -*-
"""静态站回滚：把某次已发布的历史快照重新推到 gh-pages。

背景：每天 05:00 的发布是"孤儿提交 + --force"覆盖，历史快照仍留在仓库
对象里（`logs/cron-deploy.log` 每行都记着 `已发布快照 <hash>`），但此前
没有任何脚本把它们变回一次回滚动作——线上出问题时只能干看着。

用法：
    python scripts/rollback_site.py                 # 列出可选快照（最近的在前）
    python scripts/rollback_site.py --to 27b7333f35  # 回滚到该快照（前十位即可）
    python scripts/rollback_site.py --to 27b7333f35 --apply   # 真正强推
缺省只解析日志并校验对象存在，`--apply` 才推送。
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = [ROOT / "logs" / "cron-deploy.log", ROOT / "logs" / "deploy.log"]
SNAP_RE = re.compile(r"已发布快照\s+([0-9a-f]{7,40})")


def snapshots():
    """从发布日志里汇总历史快照（去重、按出现顺序倒序）。"""
    seen, out = set(), []
    for path in LOGS:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = SNAP_RE.search(line)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append(m.group(1))
    return list(reversed(out))


def resolve(prefix):
    """把短 hash 补全成完整 hash（对象必须在本地仓库里）。"""
    try:
        full = subprocess.run(["git", "rev-parse", "--verify", f"{prefix}^{{commit}}"],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8").stdout.strip()
    except Exception:  # noqa: BLE001
        full = ""
    return full


def main():
    ap = argparse.ArgumentParser(description="回滚静态站到某个历史快照")
    ap.add_argument("--to", help="目标快照 hash（可只写前 8 位）")
    ap.add_argument("--branch", default="gh-pages")
    ap.add_argument("--apply", action="store_true", help="真正强推（缺省只看）")
    args = ap.parse_args()

    snaps = snapshots()
    if not snaps:
        print("发布日志里没有快照记录（logs/cron-deploy.log 不存在？）")
    if not args.to:
        print(f"最近 {min(len(snaps), 10)} 个快照：")
        for h in snaps[:10]:
            full = resolve(h)
            subject = ""
            if full:
                subject = subprocess.run(
                    ["git", "log", "-1", "--format=%s %ad", "--date=short", full],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8").stdout.strip()
            print(f"  {h}  {subject or '（本地无该对象）'}")
        print("\n回滚：python scripts/rollback_site.py --to <hash> --apply")
        return

    full = resolve(args.to) or args.to
    remote = os.environ.get("UNIV_PUSH_REMOTE", "github-univ")
    print(f"目标快照 {full[:10]} → 强推 {remote}/{args.branch}")
    if not args.apply:
        print("（试运行，未推送；确认无误后加 --apply）")
        return
    r = subprocess.run(["git", "push", "--force", remote,
                        f"{full}:refs/heads/{args.branch}"], cwd=ROOT, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"推送失败：{r.stdout} {r.stderr}")
    print("已回滚。GitHub Pages 约 1-2 分钟后生效。")


if __name__ == "__main__":
    main()
