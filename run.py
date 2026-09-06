# -*- coding: utf-8 -*-
"""主入口：手动触发采集。

用法：
    python run.py --list                 # 只初始化数据库并打印学校/栏目入口
    python run.py                        # 采集所有学校所有栏目
    python run.py --school 深圳大学      # 只采集指定学校
    python run.py --school 深圳大学 --source 研究生招生网
    python run.py --no-detail            # 不抓详情页（更快，只存标题+原文链接）
    python run.py --max-items 80         # 每个栏目最多抽取 80 条
    python run.py --backfill-meta        # 仅为存量通知补齐截止日期等结构化字段

说明：config/schools.yaml 中某栏目设 browser: true 时，会用 Playwright
真实浏览器抓取（用于过反爬，如北邮的 JS 挑战），其余站点仍用 requests。
"""
import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from crawler import dedup, fetch, parse  # noqa: E402
from crawler.extract import extract_highlights, normalize_deadline  # noqa: E402
from db import store  # noqa: E402

CONFIG = ROOT / "config" / "schools.yaml"


# 规则提取写入 notice_meta 的字段（刷新时先清掉这几个，避免陈旧数据）
RULE_META_FIELDS = ("deadline", "period", "target", "college", "deadline_iso")


def _save_notice_meta(conn, notice_id, content_md, title, published_at=""):
    """从正文提取截止日期/对象/学院等结构化字段，写入 notice_meta。"""
    try:
        hl = extract_highlights(content_md, title)
        if hl.get("deadline"):
            # ISO 归一化日期：供排序与"即将截止"筛选
            iso = normalize_deadline(hl["deadline"], published_at)
            if iso:
                hl["deadline_iso"] = iso
        marks = ",".join("?" * len(RULE_META_FIELDS))
        conn.execute(
            f"DELETE FROM notice_meta WHERE notice_id=? AND field_name IN ({marks})",
            (notice_id, *RULE_META_FIELDS),
        )
        for k, v in hl.items():
            if v:
                store.upsert_notice_meta(conn, notice_id, k, v)
    except Exception as e:  # noqa: BLE001
        print(f"  ! meta 提取失败 (notice {notice_id}): {e}")


def backfill_meta(conn):
    """为已入库且有正文快照的通知补齐 notice_meta（幂等，可重复执行）。"""
    rows = conn.execute(
        "SELECT id, title, content_md, published_at FROM notices "
        "WHERE content_md IS NOT NULL AND content_md != ''"
    ).fetchall()
    done = 0
    for r in rows:
        _save_notice_meta(conn, r["id"], r["content_md"], r["title"],
                          r["published_at"] or "")
        done += 1
    total = conn.execute(
        "SELECT COUNT(DISTINCT notice_id) AS c FROM notice_meta").fetchone()["c"]
    print(f"回填完成：处理 {done} 条通知，当前有结构化字段的通知 {total} 条。")


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}




def _fetch_detail_content(item_url, use_browser, use_real_browser=False):
    """抓取详情正文：requests → 无头渲染 → 真实 Chrome（瑞数），逐级兜底。

    返回 (content_md, published)。
    """
    try:
        html = fetch.http_get(item_url, use_browser=use_browser,
                              use_real_browser=use_real_browser)
        detail = parse.parse_detail(html, item_url)
        if detail["content_md"] and len(detail["content_md"]) > 50:
            return detail["content_md"], detail["published_at"]
        # requests 拿不到正文 → 依次尝试无头渲染、真实 Chrome（瑞数反爬）
        if not use_browser:
            try:
                html2 = fetch.http_get(item_url, use_browser=True)
                detail2 = parse.parse_detail(html2, item_url)
                if detail2["content_md"] and len(detail2["content_md"]) > 50:
                    return detail2["content_md"], detail2["published_at"]
            except Exception:  # noqa: BLE001
                pass
        if not use_real_browser:
            try:
                html3 = fetch.http_get(item_url, use_real_browser=True)
                detail3 = parse.parse_detail(html3, item_url)
                if detail3["content_md"] and len(detail3["content_md"]) > 50:
                    return detail3["content_md"], detail3["published_at"]
            except Exception:  # noqa: BLE001
                pass
        return detail["content_md"], detail["published_at"]
    except Exception:  # noqa: BLE001
        return None, ""


def crawl_source(conn, school, school_id, source, args):
    """采集单个栏目：抽列表 -> 逐个入库（可带详情快照）。"""
    url, domain = source["url"], school["domain"]
    use_browser = bool(source.get("browser", False))
    use_real = bool(source.get("real_browser", False))
    source_id = conn.execute(
        "SELECT id FROM sources WHERE url=?", (url,)
    ).fetchone()["id"]
    try:
        html = fetch.http_get(url, use_browser=use_browser,
                              use_real_browser=use_real)
    except Exception as e:  # noqa: BLE001
        # 列表页 requests 抓取失败 → 自动降级 Playwright 渲染（未启用真实浏览器时）
        if not use_real:
            try:
                html = fetch.http_get(url, use_browser=True)
            except Exception:  # noqa: BLE001
                store.log_fetch(conn, source_id, school_id, "error", 0, str(e))
                print(f"  !! [{school['name']}][{source['name']}] 抓取失败: {e}")
                return 0
        else:
            store.log_fetch(conn, source_id, school_id, "error", 0, str(e))
            print(f"  !! [{school['name']}][{source['name']}] 抓取失败: {e}")
            return 0

    items = parse.parse_list(html, url, domain, max_items=args.max_items)
    if not items:
        # 列表页无有效通知：可能是导航页 / 反爬拦截 / URL 错误，醒目标注便于清理配置
        print(f"  ⚠ [{school['name']}][{source['name']}] 列表页未解析到有效通知："
              f"可能是导航页 / 反爬拦截 / 栏目 URL 错误")
    new_count = 0
    for it in items:
        title, item_url = it["title"], it["url"]
        if dedup.should_skip(conn, school_id, item_url, title, ""):
            continue
        content_md, published, type_tag = None, "", parse.infer_type(title)
        if not args.no_detail:
            content_md, published = _fetch_detail_content(
                item_url, use_browser, use_real_browser=use_real)
            time.sleep(args.sleep)
        notice_id, _ = store.insert_notice(
            conn, school_id, source_id, title, item_url,
            content_md=content_md, published_at=published, type_tag=type_tag,
        )
        new_count += 1
        if notice_id and content_md:
            _save_notice_meta(conn, notice_id, content_md, title,
                              published_at=published or "")
        print(f"  + [{school['name']}][{source['name']}] {title}\n      {item_url}")
    store.log_fetch(conn, source_id, school_id, "ok", new_count,
                    f"抽到 {len(items)} 条，新增 {new_count} 条")
    return new_count


def main():
    ap = argparse.ArgumentParser(description="高校官网信息采集（手动触发）")
    ap.add_argument("--school", help="只采集指定学校名")
    ap.add_argument("--source", help="只采集指定栏目名（与 --school 配合）")
    ap.add_argument("--list", action="store_true", help="打印入口清单后退出")
    ap.add_argument("--no-detail", action="store_true", help="跳过详情页抓取")
    ap.add_argument("--max-items", type=int, default=50, help="每栏目最多抽取条数")
    ap.add_argument("--sleep", type=float, default=0.5, help="抓详情页间隔秒数")
    ap.add_argument("--backfill-meta", action="store_true",
                    help="仅为已入库通知补齐结构化字段（截止日期等），不抓取网页")
    args = ap.parse_args()

    store.init_db()
    conn = store.connect()
    schools = load_config()["schools"]
    store.import_schools(conn, schools)

    if args.backfill_meta:
        backfill_meta(conn)
        conn.close()
        return

    if args.list:
        for src in store.list_sources(conn):
            print(f"{src['school_name']} | {src['category']} | "
                  f"{src['name']} | {src['url']}")
        conn.close()
        return

    total_new = 0
    enabled_names = store.enabled_school_names(conn)
    for school in schools:
        if args.school and args.school not in school["name"]:
            continue
        # 停用的学校跳过（显式 --school 指定时仍允许，便于手动补采）
        if not args.school and school["name"] not in enabled_names:
            print(f"\n== 跳过（已停用）{school['name']} ==")
            continue
        school_id = store.school_id_by_name(conn, school["name"])
        for source in school.get("sources", []):
            if args.source and args.source not in source["name"]:
                continue
            print(f"\n== 采集 {school['name']} / {source['name']} ==")
            try:
                total_new += crawl_source(conn, school, school_id, source, args)
            except Exception as e:  # noqa: BLE001
                print(f"  !! {e}")

    fetch.close_browser()
    try:
        fetch.close_real_browser()
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    print(f"\n完成，共新增 {total_new} 条。"
          f"数据库：{store.get_db_path()}")


if __name__ == "__main__":
    main()
