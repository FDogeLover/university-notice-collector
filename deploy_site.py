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
    run(["git", "push", "--force", remote, f"{commit}:refs/heads/{branch}"])
    index_file.unlink(missing_ok=True)
    return commit


def enable_pages(branch="gh-pages"):
    """用 gh CLI 把 Pages 来源设为 gh-pages 分支（已启用则改源）。"""
    repo = run(["gh", "repo", "view", "--json", "nameWithOwner",
                "-q", ".nameWithOwner"]).strip()
    try:
        cur = run(["gh", "api", f"repos/{repo}/pages"])
        print(f"Pages 已启用：{cur[:120]}…，切换来源为 {branch}")
        run(["gh", "api", f"repos/{repo}/pages", "-X", "PUT",
             "-f", f"source[branch]={branch}",
             "-f", "source[path]=/"])
    except RuntimeError:
        print("Pages 尚未启用，正在开启…")
        out = run(["gh", "api", f"repos/{repo}/pages", "-X", "POST",
                   "-f", f"source[branch]={branch}",
                   "-f", "source[path]=/"])
        print(out[:200])
    return repo


def main():
    ap = argparse.ArgumentParser(description="构建并发布静态站到 GitHub Pages")
    ap.add_argument("--out", default=None, help="已有站点目录（默认先构建 site/）")
    ap.add_argument("--branch", default="gh-pages", help="Pages 来源分支")
    ap.add_argument("--no-build", action="store_true", help="不重新构建")
    ap.add_argument("--no-pages-setup", action="store_true",
                    help="跳过 Pages 来源设置")
    args = ap.parse_args()

    site_dir = None if args.no_build else args.out
    if args.no_build:
        site_dir = args.out or (ROOT / "site")
    site_dir = build(site_dir)
    commit = publish(site_dir, branch=args.branch)
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
