# -*- coding: utf-8 -*-
"""静态站一键部署：构建 site/ 并发布到 GitHub Pages 的 gh-pages 分支。

用法（项目根目录执行）：
    python deploy_site.py               # 构建 + 发布
    python deploy_site.py --no-build    # 跳过构建，直接发布现有 site/
    python deploy_site.py --out D:\\site # 指定已构建目录

发布形态：把 site/ 全部内容作为一个**孤儿提交**强推到 origin/gh-pages
（单提交快照，无历史累积，不触碰当前工作区分支）。
首次发布后用 gh CLI 或仓库设置页把 Pages 来源切到 gh-pages 分支。

与本项目另一参考站点（AUV）的 Actions 构建不同：本站数据来自本地
gitignore 的 SQLite，无法在 CI 构建，故采用本地构建 + gh-pages 发布。
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd, env=None, cwd=None, capture=True):
    r = subprocess.run(cmd, cwd=cwd or ROOT, env=env,
                       capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"命令失败: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return (r.stdout or "").strip()


def build(out_dir):
    if not out_dir:
        print("== 构建静态站…")
        run([sys.executable, str(ROOT / "build_site.py")])
        return ROOT / "site"
    return Path(out_dir)


LAST_PUBLISH = ROOT / "data" / "last_publish.json"


def site_stats(site_dir):
    """从构建产物里读出本次要发布的量（供发布前护栏比对）。"""
    import json
    import re

    path = Path(site_dir) / "data" / "notices.js"
    if not path.exists():
        return {}
    head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    m = re.search(r'"generatedAt"\s*:\s*"([^"]+)"', head)
    n = re.search(r'"notices"\s*:\s*(\d+)', head)
    return {"generatedAt": m.group(1) if m else "",
            "notices": int(n.group(1)) if n else 0}


def db_freshness(max_age_days=3):
    """构建用的库有多旧：返回 (最新抓取时间, 距今天数)；读不到返回 (None, -1)。"""
    import sqlite3
    from datetime import datetime

    db = Path(os.environ.get("UNIV_DB", ROOT / "data" / "university.db"))
    if not db.exists():
        return None, -1
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        latest = conn.execute("SELECT MAX(fetched_at) FROM notices").fetchone()[0]
        conn.close()
        age = (datetime.now() - datetime.strptime(latest, "%Y-%m-%d %H:%M:%S")).days
        return latest, age
    except Exception:  # noqa: BLE001
        return None, -1


def guard_publish(site_dir, force=False, min_ratio=0.95, max_age_days=3):
    """发布前护栏：构建用的库太旧、或本次构建的量明显变少，就中止发布。

    线上站是每日快照的覆盖式发布（孤儿提交 + `--force`），本机库比服务器库
    落后时在本机跑一次，就会把线上 8961 条覆盖成 5622 条、静默丢掉 3339 条
    通知与其正文快照。两道闸：① 构建用的库最新抓取时间超过 max_age_days
    （本机库常年不动，服务器库每小时在采，这条最灵敏）；② 本次条数低于上次
    发布的 95%。要强行发布得显式加 --force。
    """
    import json

    latest, age = db_freshness()
    if age >= 0:
        print(f"== 发布前护栏：构建库最新抓取 {latest}（{age} 天前）")
        if age > max_age_days:
            raise SystemExit(
                f"拒绝发布：构建用的库 {age} 天没有新数据（>{max_age_days} 天）。"
                f"线上站的权威库在服务器（ssh study 的 "
                f"/home/university-notice-collector），发布应在那里做；"
                f"确认无误可加 --force 强制发布。")
    else:
        print("! 护栏提示：读不到构建库的最新抓取时间，跳过新鲜度检查")

    cur = site_stats(site_dir)
    if not cur.get("notices"):
        print("! 护栏跳过：构建产物里读不到条数")
        return cur
    prev = {}
    if LAST_PUBLISH.exists():
        try:
            prev = json.loads(LAST_PUBLISH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = {}
    prev_n = int(prev.get("notices") or 0)
    print(f"== 发布前护栏：本次 {cur['notices']} 条"
          f"（构建于 {cur.get('generatedAt') or '?'}），"
          f"上次发布 {prev_n or '（无记录）'} 条")
    if prev_n and cur["notices"] < prev_n * min_ratio:
        raise SystemExit(
            f"拒绝发布：本次 {cur['notices']} 条 < 上次 {prev_n} 条的 "
            f"{int(min_ratio * 100)}%。这通常意味着构建用的库不是生产库"
            f"（服务器库才是在采的那份）。确认无误可加 --force 强制发布。")
    return cur


def record_publish(site_dir, commit):
    """记下这次发布的量，作为下次护栏的基线。"""
    import json

    cur = site_stats(site_dir)
    LAST_PUBLISH.parent.mkdir(parents=True, exist_ok=True)
    LAST_PUBLISH.write_text(json.dumps(
        {"at": time.strftime("%F %T"), "commit": commit, **cur},
        ensure_ascii=False, indent=2), encoding="utf-8")


def publish(site_dir, branch="gh-pages", remote="origin"):
    """把 site_dir 内容作为孤儿提交强推到远端 gh-pages 分支。"""
    site_dir = Path(site_dir).resolve()
    if not (site_dir / "index.html").exists():
        raise SystemExit(f"{site_dir} 没有 index.html，请先构建")
    if not (site_dir / ".nojekyll").exists():
        (site_dir / ".nojekyll").write_text("", encoding="utf-8")

    index_file = ROOT / ".git" / "site-deploy-index"
    if index_file.exists():
        index_file.unlink()
    # GIT_WORK_TREE 指向站点目录：索引路径以站点根为前缀（部署到域名根），
    # 对象与推送复用主仓库（site/ 在主 .gitignore 里，故 add 需 -f）
    env = {**os.environ,
           "GIT_DIR": str(ROOT / ".git"),
           "GIT_WORK_TREE": str(site_dir),
           "GIT_INDEX_FILE": str(index_file)}

    print(f"== 发布 {site_dir} → {remote}/{branch}")
    run(["git", "read-tree", "--empty"], env=env)
    run(["git", "add", "--all", "-f", "."], env=env, cwd=site_dir)
    tree = run(["git", "write-tree"], env=env)
    msg = f"deploy site {date.today().isoformat()} (build from local db)"
    commit = run(["git", "commit-tree", tree, "-m", msg], env=env)
    last_err = None
    # 推送远端：优先 SSH（github.com 到本机的 HTTPS 间歇性超时 ~130s，
    # 同机 Obsidian 仓库走 SSH 长期稳定；社区亦推荐 SSH 替代 HTTPS）。
    # 远端名从环境变量 UNIV_PUSH_REMOTE 读取，默认 github-univ（见 ~/.ssh/config）。
    push_remote = os.environ.get("UNIV_PUSH_REMOTE", "github-univ")
    print(f"   （实际推送目标：{push_remote}/{branch}；改目标设 UNIV_PUSH_REMOTE）")
    # 指数退避：5s → 30s → 120s（HTTPS 超时窗口约 130s，短重试会落在同一窗口内）
    backoff = [5, 30, 120]
    for attempt in range(len(backoff) + 1):
        try:
            run(["git", "push", "--force", push_remote,
                 f"{commit}:refs/heads/{branch}"])
            break
        except RuntimeError as e:  # noqa: PERF203
            last_err = e
            if attempt < len(backoff):
                wait = backoff[attempt]
                print(f"   push 失败（第 {attempt + 1} 次），{wait}s 后重试…")
                time.sleep(wait)
    else:
        raise last_err
    index_file.unlink(missing_ok=True)
    return commit


def enable_pages(branch="gh-pages"):
    """用 gh CLI 把 Pages 来源设为 gh-pages 分支（已启用则改源）。

    GET 与 PUT 分开 try：原实现把两步包在同一个 try 里，PUT 的失败会被
    当成"Pages 尚未启用"去 POST，最终只报 POST 的 409，真实错因被吞掉，
    日志里天天留一条假告警。
    """
    repo = run(["gh", "repo", "view", "--json", "nameWithOwner",
                "-q", ".nameWithOwner"]).strip()
    try:
        cur = run(["gh", "api", f"repos/{repo}/pages"])
    except RuntimeError:
        print("Pages 尚未启用，正在开启…")
        out = run(["gh", "api", f"repos/{repo}/pages", "-X", "POST",
                   "-f", f"source[branch]={branch}",
                   "-f", "source[path]=/"])
        print(out[:200])
        return repo
    print(f"Pages 已启用：{cur[:120]}…，切换来源为 {branch}")
    try:
        run(["gh", "api", f"repos/{repo}/pages", "-X", "PUT",
             "-f", f"source[branch]={branch}",
             "-f", "source[path]=/"])
    except RuntimeError as e:
        print(f"! Pages 来源切换失败（站点已在服务，可忽略；手动改仓库 "
              f"Settings→Pages）：{str(e)[:160]}")
    return repo


def main():
    ap = argparse.ArgumentParser(description="构建并发布静态站到 GitHub Pages")
    ap.add_argument("--out", default=None, help="已有站点目录（默认先构建 site/）")
    ap.add_argument("--branch", default="gh-pages", help="Pages 来源分支")
    ap.add_argument("--no-build", action="store_true", help="不重新构建")
    ap.add_argument("--no-pages-setup", action="store_true",
                    help="跳过 Pages 来源设置")
    ap.add_argument("--force", action="store_true",
                    help="跳过发布前护栏（条数比上次发布少也照发）")
    args = ap.parse_args()

    site_dir = None if args.no_build else args.out
    if args.no_build:
        site_dir = args.out or (ROOT / "site")
    site_dir = build(site_dir)
    guard_publish(site_dir, force=args.force)
    commit = publish(site_dir, branch=args.branch)
    record_publish(site_dir, commit)
    print(f"已发布快照 {commit[:10]}")

    repo = None
    if not args.no_pages_setup:
        try:
            repo = enable_pages(args.branch)
        except RuntimeError as e:
            print(f"! Pages 来源自动设置失败（可手动在仓库 Settings→Pages "
                  f"选择 {args.branch} 分支）：{e}")

    if repo:
        user = repo.split("/")[0]
        print(f"\n发布完成，站点地址（首次/更新后约 1-2 分钟生效）：\n"
              f"  https://{user.lower()}.github.io/{repo.split('/')[1]}/")


if __name__ == "__main__":
    main()
