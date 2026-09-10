# -*- coding: utf-8 -*-
"""静态展示站生成器：把库内采集成果导出为免配置的静态网站。

用法（项目根目录执行）：
    python build_site.py                       # 输出到 site/
    python build_site.py --out 路径 --max-content 8000

特点：
- 无 AI、无采集入口，纯展示：统计 / 筛选 / 关键词搜索 / 即将截止 / 正文快照 /
  官方原文链接；停用学校不会导出
- 数据以 .js 文件注入（window.SITE_DATA / SITE_CONTENT），不依赖 fetch，
  **双击 index.html 即可打开**，也支持上传到 GitHub Pages / 服务器等任意静态主机
- 全部相对路径，可部署在域名子路径下
"""
import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from crawler.extract import (  # noqa: E402
    clean_summary,
    extract_highlights,
    standardize_highlights,
)
from db import store  # noqa: E402
from web.ai_context import SCHOOL_ABBRS  # noqa: E402

TEMPLATE_DIR = ROOT / "web" / "static_site"


def _iso(published_at):
    """取发布时间字符串中的 ISO 日期部分（用于时间筛选）。"""
    return (published_at or "")[:10]


def collect(conn, max_content):
    """从库中取全部启用学校的通知与统计。
    返回 (notices, stats, deadlines, content_dir_ids, school_tags)。"""
    rows = conn.execute(
        "SELECT n.id, n.title, n.url, n.type_tag, n.published_at, n.fetched_at,"
        "       n.content_md, sc.name AS school_name, sc.tags AS school_tags,"
        "       s.name AS source_name, COALESCE(s.stype,'研究生教育') AS stype "
        "FROM notices n "
        "JOIN schools sc ON sc.id = n.school_id "
        "LEFT JOIN sources s ON s.id = n.source_id "
        "WHERE sc.enabled = 1 "
        "ORDER BY (n.published_at IS NULL OR n.published_at='') ASC,"
        " n.published_at DESC, n.fetched_at DESC",
    ).fetchall()
    meta_maps = store.notice_meta_maps(conn, [r["id"] for r in rows])
    school_tags = {
        r["name"]: (r["tags"] or "")
        for r in conn.execute(
            "SELECT name, tags FROM schools WHERE enabled=1")
    }

    notices = []
    content_dir_ids = []
    for r in rows:
        meta = meta_maps.get(r["id"]) or {}
        content = r["content_md"] or ""
        highlights = standardize_highlights(
            meta or extract_highlights(content, r["title"]),
            r["published_at"] or "",
        )
        notices.append({
            "id": r["id"],
            "title": r["title"],
            "url": r["url"],
            "type": r["type_tag"] or "未分类",
            "school": r["school_name"],
            "school_tags": r["school_tags"] or "",
            "source": r["source_name"] or "",
            "stype": r["stype"],
            "published": r["published_at"] or "",
            "date": _iso(r["published_at"]),
            "excerpt": clean_summary(content, r["title"], max_len=120),
            "highlights": highlights,
            "deadline": meta.get("deadline_iso") or "",
            "has_content": bool(content),
        })
        if content:
            content_dir_ids.append((r["id"], content[:max_content]))

    by_type, by_school = {}, {}
    for n in notices:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
        by_school[n["school"]] = by_school.get(n["school"], 0) + 1
    # "最近更新"取实际抓取时间（与主站语义一致）；不能用 published_at：
    # 那是原文发布日期，可能含历史/异常日期（如解析错误的 2052 年）
    last_fetch = max((r["fetched_at"] or "" for r in rows), default="")
    stats = {
        "schools": len(by_school),
        "notices": len(notices),
        "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]),
        "by_school": sorted(by_school.items(), key=lambda kv: -kv[1]),
        "last_fetch": last_fetch,
    }

    today = date.today()
    until = today + timedelta(days=30)
    deadlines = [
        {"id": n["id"], "title": n["title"], "school": n["school"],
         "deadline": n["deadline"]}
        for n in sorted(notices, key=lambda x: x["deadline"])
        if n["deadline"] and today.isoformat() <= n["deadline"] <= until.isoformat()
    ][:50]
    return notices, stats, deadlines, content_dir_ids, school_tags


def write_site(out_dir, notices, stats, deadlines, content_dir_ids,
               school_tags=None):
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "data" / "content").mkdir(parents=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")  # 跳过 GitHub Pages 的 Jekyll 处理

    # 静态资源与页面模板
    for name in ("index.html", "style.css", "app.js"):
        src = TEMPLATE_DIR / name
        dst = out / ("assets/" + name if name != "index.html" else name)
        shutil.copy(src, dst)

    # 数据文件：window.SITE_DATA 注入（file:// 下可用，不依赖 fetch/CORS）
    payload = {
        "generatedAt": date.today().isoformat(),
        "stats": stats,
        "deadlines": deadlines,
        "notices": notices,
        "abbrs": SCHOOL_ABBRS,     # 学校简称映射，供前端分组搜索
        "schoolTags": school_tags,  # 学校标签（985/211），供徽章与筛选
    }
    (out / "data" / "notices.js").write_text(
        "window.SITE_DATA = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";",
        encoding="utf-8",
    )
    # 正文快照：每条一个 .js，详情打开时按需注入
    for nid, content in content_dir_ids:
        (out / "data" / "content" / f"{nid}.js").write_text(
            'window.SITE_CONTENT=window.SITE_CONTENT||{};'
            f'window.SITE_CONTENT["{nid}"]='
            + json.dumps({"content": content}, ensure_ascii=False)
            + ";",
            encoding="utf-8",
        )
    return out


def main():
    ap = argparse.ArgumentParser(description="生成静态展示站（纯展示，无 AI）")
    ap.add_argument("--out", default=str(ROOT / "site"), help="输出目录")
    ap.add_argument("--max-content", type=int, default=20000,
                    help="每条正文快照最多保留字符数")
    args = ap.parse_args()

    conn = store.connect()
    try:
        store.init_db(conn)
        notices, stats, deadlines, contents, school_tags = collect(
            conn, args.max_content)
    finally:
        conn.close()
    out = write_site(args.out, notices, stats, deadlines, contents, school_tags)
    n_files = len(list((out / "data" / "content").glob("*.js")))
    print(f"静态站已生成：{out}")
    print(f"  通知 {len(notices)} 条（{n_files} 条含正文快照），"
          f"即将截止 {len(deadlines)} 条，数据日期 {date.today().isoformat()}")
    print("  部署：整个目录上传到静态主机即可；本地直接双击 index.html 也能打开。")


if __name__ == "__main__":
    main()
