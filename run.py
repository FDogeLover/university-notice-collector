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
from concurrent.futures import ThreadPoolExecutor
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


def dedupe_notices(conn):
    """清理存量重复：同校+同标题+同发布时间 的冗余条目（保留最早一条）。

    附带清理这些冗余条目的 notice_meta（外键引用）。返回删除条数。
    """
    rows = conn.execute(
        "SELECT school_id, title, published_at, COUNT(*) AS c "
        "FROM notices WHERE published_at != '' "
        "GROUP BY school_id, title, published_at HAVING c > 1"
    ).fetchall()
    removed = 0
    for r in rows:
        ids = [x["id"] for x in conn.execute(
            "SELECT id FROM notices WHERE school_id=? AND title=? "
            "AND published_at=? ORDER BY id",
            (r["school_id"], r["title"], r["published_at"]),
        ).fetchall()]
        keep, drop = ids[0], ids[1:]
        marks = ",".join("?" * len(drop))
        conn.execute(
            f"DELETE FROM notice_meta WHERE notice_id IN ({marks})", drop)
        conn.execute(f"DELETE FROM notices WHERE id IN ({marks})", drop)
        removed += len(drop)
    conn.commit()
    print(f"去重完成：清理 {removed} 条冗余通知。")
    return removed


def retag_notices(conn):
    """按当前规则重打类型标签（规则升级后回填存量，幂等）。"""
    rows = conn.execute("SELECT id, title, type_tag FROM notices").fetchall()
    changed = 0
    for r in rows:
        new = parse.infer_type(r["title"])
        if new != r["type_tag"]:
            conn.execute("UPDATE notices SET type_tag=? WHERE id=?",
                         (new, r["id"]))
            changed += 1
    conn.commit()
    dist = conn.execute(
        "SELECT COALESCE(type_tag,'未分类') t, COUNT(*) c FROM notices "
        "GROUP BY t ORDER BY c DESC").fetchall()
    print(f"重打标签完成：更新 {changed} 条")
    for d in dist[:10]:
        print(f"  {d['t']}: {d['c']}")
    return changed


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}




def _fetch_detail_content(item_url, use_browser, use_real_browser=False,
                          attempts=None):
    """抓取详情正文：按栏目配置 → 无头渲染 → 真实 Chrome（瑞数），逐级兜底。

    与首版不同：某一级**抛异常**（网络失败/反爬拦截）时同样继续降级尝试，
    而不是直接放弃；全部失败时返回正文最长的那份兜底结果。
    attempts 为 [(use_browser, use_real), ...] 时的自定义尝试序列，
    供并发快速通道只走 requests（Playwright/Chrome 实例非线程安全）。
    详情页直接是附件（PDF/Word 等）时返回空正文，避免二进制乱码入库。
    返回 (content_md, published)。
    """
    if parse.is_file_url(item_url):
        return None, ""
    if attempts is None:
        if use_real_browser:
            attempts = [(True, True)]
        elif use_browser:
            attempts = [(True, False), (True, True)]
        else:
            attempts = [(False, False), (True, False), (True, True)]
    best_content, best_published = None, ""
    for use_b, use_r in attempts:
        try:
            html = fetch.http_get(item_url, use_browser=use_b,
                                  use_real_browser=use_r)
            detail = parse.parse_detail(html, item_url)
        except Exception:  # noqa: BLE001
            continue
        content = detail["content_md"] or ""
        if len(content) > 50:
            return content, detail["published_at"]
        if len(content) > len(best_content or ""):
            best_content, best_published = content, detail["published_at"]
    return best_content, best_published


def _detail_quick_job(item_url):
    """并发快速通道：单次 requests 抓详情（不碰浏览器实例，线程安全）。

    正文达标返回 (url, content, published)；不达标/失败返回 (url, None, ...)，
    由调用方串行走完整升级链兜底。附件 URL 直接返回空（避免二进制乱码）。
    """
    if parse.is_file_url(item_url):
        return item_url, None, ""
    try:
        html = fetch.http_get(item_url, use_browser=False,
                              use_real_browser=False)
        detail = parse.parse_detail(html, item_url)
    except Exception:  # noqa: BLE001
        return item_url, None, ""
    content = detail["content_md"] or ""
    if len(content) > 50:
        return item_url, content, detail["published_at"]
    return item_url, None, detail["published_at"]


def _fetch_details(items, use_browser, use_real, args):
    """为待入库条目抓详情，返回 {url: (content_md, published)}。

    纯 requests 栏目用线程池并发（--workers 控制并发数）；浏览器/真实
    Chrome 栏目及并发未达标的条目保持串行（浏览器实例非线程安全）。
    """
    details = {}
    if not items:
        return details
    if use_browser or use_real or args.workers <= 1:
        for it in items:
            details[it["url"]] = _fetch_detail_content(
                it["url"], use_browser, use_real_browser=use_real)
            time.sleep(args.sleep)
        return details

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for item_url, content, published in ex.map(
                _detail_quick_job, [it["url"] for it in items]):
            if content is not None:
                details[item_url] = (content, published)
    retry = [it for it in items if it["url"] not in details]
    if retry:
        print(f"  · {len(retry)} 条并发未达标，串行浏览器兜底…")
        for it in retry:
            details[it["url"]] = _fetch_detail_content(it["url"], use_browser,
                                                       use_real_browser=use_real)
            time.sleep(args.sleep)
    return details


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

    items = parse.parse_list(html, url, domain, max_items=args.max_items,
                             stype=source.get("stype"))
    if not items:
        # 列表页无有效通知：可能是导航页 / 反爬拦截 / URL 错误，醒目标注便于清理配置
        print(f"  ⚠ [{school['name']}][{source['name']}] 列表页未解析到有效通知："
              f"可能是导航页 / 反爬拦截 / 栏目 URL 错误")
    # 第一阶段：去重筛出待入库条目
    new_items = []
    for it in items:
        if dedup.should_skip(conn, school_id, it["url"], it["title"], ""):
            continue
        new_items.append(it)
    # 第二阶段：并发抓详情（浏览器模式自动退化为串行），再统一入库
    details = {}
    if not args.no_detail:
        details = _fetch_details(new_items, use_browser, use_real, args)
    new_count = 0
    for it in new_items:
        title, item_url = it["title"], it["url"]
        content_md, published = details.get(item_url, (None, ""))
        # 详情级指纹查重：同校+同标题+同发布时间 → 跨栏目重复，跳过
        if dedup.find_by_title_published(conn, school_id, title, published):
            continue
        type_tag = parse.infer_type(title)
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
    ap.add_argument("--workers", type=int, default=6,
                    help="详情页并发抓取线程数（浏览器模式不生效）")
    ap.add_argument("--backfill-meta", action="store_true",
                    help="仅为已入库通知补齐结构化字段（截止日期等），不抓取网页")
    ap.add_argument("--dedupe", action="store_true",
                    help="清理存量重复（同校+同标题+同发布时间），不抓取网页")
    ap.add_argument("--retag", action="store_true",
                    help="按当前规则重打类型标签（规则升级后回填），不抓取网页")
    ap.add_argument("--shard", type=lambda s: tuple(map(int, s.split("/"))),
                    help="分片采集：--shard 1/3 表示只跑 1/3 的栏目（可按时段分次跑完）")
    args = ap.parse_args()

    store.init_db()
    conn = store.connect()
    schools = load_config()["schools"]
    store.import_schools(conn, schools)

    if args.backfill_meta:
        backfill_meta(conn)
        conn.close()
        return
    if args.dedupe:
        dedupe_notices(conn)
        conn.close()
        return
    if args.retag:
        retag_notices(conn)
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
    shard_idx = shard_total = 0
    if args.shard:
        shard_idx, shard_total = args.shard  # (i, n)：只跑第 i 片（从 1 计）
    seen_sources = 0  # 分片计数：按栏目出现顺序稳定取模
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
            # 分片：按栏目顺序取模，把全量拆成 N 段分时执行（规避 1 小时限制）
            if shard_total:
                if seen_sources % shard_total != shard_idx - 1:
                    seen_sources += 1
                    continue
                seen_sources += 1
            print(f"\n== 采集 {school['name']} / {source['name']} ==")
            try:
                total_new += crawl_source(conn, school, school_id, source, args)
            except Exception as e:  # noqa: BLE001
                print(f"  !! {e}")

    if shard_total:
        print(f"\n[分片 {shard_idx}/{shard_total}] 本片完成，新增 {total_new} 条")

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
