# -*- coding: utf-8 -*-
"""修复库内发布时间：把未来日期/畸形日期/误取的日程日期换成站点真实发布时间。

背景：旧逻辑在详情页找不到"发布时间"标记时，会退化成"页面里第一个日期"，
于是正文开头的日程（"报名自2027年9月6日起"、"竞赛时间：2026年11月14日"、
"双选会举办时间 2026-10-23"）和图片路径里的数字（../images/2026-09/7abc.png
→ 2026-09-70）都被当成了发布时间，卡片右上角就显示成未来日期。

取值优先级：列表行日期（站点自己标在通知旁边的发布时间）> 详情页自己写明的
发布标记 > 留空。列表行是权威来源——详情页常常没有任何发布标记，"猜"必然出错。

两条规则的边界（都是"宁可不动，也别改错"）：
- 一定错的日期（空/畸形/未来）→ 换成列表日期；取不到就抓详情页看有没有发布
  标记（--detail）；再没有就置空，卡片显示"时间未知"
- 看着合法的日期 → 只有列表行写明完整年份时才覆盖。列表只写月日时年份靠
  回推（华中科技大学列表长期只有 "03/17"），照抄会把 2022 年的存档改成今年
- 详情页正文里扫出来的日期不算数（那正是当初污染的来源）

用法（项目根目录执行）：
    python scripts/fix_published_dates.py                    # 试运行：只报告
    python scripts/fix_published_dates.py --apply --detail     # 写库
    python scripts/fix_published_dates.py --school 中南大学    # 只处理某校

报告写入 data/fix_published_dates.csv；列表页抓取结果缓存在
data/fix_dates_cache.json，复核或重跑不再抓站。
"""
import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import fetch, parse  # noqa: E402
from db import store  # noqa: E402


def load_source_flags():
    """从 config/schools.yaml 取栏目的抓取方式（browser / real_browser）。"""
    cfg = yaml.safe_load(
        (ROOT / "config" / "schools.yaml").read_text(encoding="utf-8"))
    flags = {}
    for school in cfg.get("schools", []):
        for src in school.get("sources", []):
            flags[src["url"]] = (bool(src.get("browser")),
                                 bool(src.get("real_browser")))
    return flags


def bad_date(value):
    """发布时间是否一定错：空 / 不是合法日期 / 晚于今天。"""
    v = (value or "").strip()
    if not v:
        return True
    try:
        d = date.fromisoformat(v[:10])
    except ValueError:
        return True
    return d > date.today()


def list_dates(source, flags, domain, cache):
    """抓栏目列表页，返回 {通知 URL: (日期, 带年份的日期)}（抓不到就是空表）。

    只返回写明完整年份的列表日期（date_exact）：列表只写月日时年份靠回推
    （华中科技大学列表长期只有 "03/17"），拿去写库会把 2022 年的存档改成
    今年——那正是要修的毛病，不能自己再造一个。

    结果按栏目 URL 缓存进 cache（调用方落盘）：换规则复核或重跑时不再重复
    抓站，也不给高校站点添无谓的请求。
    """
    if source["url"] not in cache:
        browser, real = flags.get(source["url"], (False, False))
        attempts = ([(browser, real)] if (browser or real)
                    else [(False, False), (True, False)])
        items, last = [], None
        for use_b, use_r in attempts:
            try:
                html = fetch.http_get(source["url"], use_browser=use_b,
                                      use_real_browser=use_r)
            except Exception as e:  # noqa: BLE001
                last = e
                continue
            items = [(it["url"], it["date"], it["date_exact"]) for it in
                     parse.parse_list(html, source["url"], domain,
                                      max_items=200, stype=source["stype"])]
            break
        else:
            print(f"  !! 列表页抓取失败 [{source['name']}] "
                  f"{source['url']}: {last}")
        cache[source["url"]] = items
    return {u: de for u, _d, de in cache[source["url"]] if de}


def detail_date(url):
    """列表页没有日期时，重抓详情页取发布时间。

    只采信页面自己写明的（meta/发布标签）；正文里扫出来的日期只是猜测，
    拿它填库等于又造一个"看着像"的错日期，宁可留空。
    """
    if parse.is_file_url(url):
        return ""
    try:
        html = fetch.http_get(url)
    except Exception:  # noqa: BLE001
        return ""
    detail = parse.parse_detail(html, url)
    return (detail["published_at"]
            if detail.get("published_src") in ("meta", "label") else "")


def main():
    ap = argparse.ArgumentParser(description="修复库内发布时间（未来/畸形/误取）")
    ap.add_argument("--apply", action="store_true", help="写入数据库（缺省只报告）")
    ap.add_argument("--school", help="只处理名字包含该词的学校")
    ap.add_argument("--source", help="只处理名字包含该词的栏目")
    ap.add_argument("--no-list", action="store_true", help="不抓列表页")
    ap.add_argument("--no-prefer-list", action="store_true",
                    help="只修'一定错'的日期，不用列表日期覆盖已有的历史日期")
    ap.add_argument("--detail", action="store_true",
                    help="列表页取不到日期时再抓详情页（慢，但比置空强）")
    ap.add_argument("--sleep", type=float, default=0.3, help="列表页间隔秒数")
    ap.add_argument("--report", default="data/fix_published_dates.csv",
                    help="改动明细 CSV 输出路径")
    ap.add_argument("--cache", default="data/fix_dates_cache.json",
                    help="列表页抓取缓存（重跑不再抓站）")
    args = ap.parse_args()

    conn = store.connect()
    if args.apply:
        store.init_db(conn)  # 写库前跑迁移（顺带按新规则清洗存量标题）
    flags = load_source_flags()

    sources = conn.execute(
        "SELECT s.id, s.name, s.url, s.stype, sc.domain, sc.name AS school_name "
        "FROM sources s JOIN schools sc ON sc.id = s.school_id "
        "WHERE sc.enabled=1 ORDER BY sc.id, s.id").fetchall()
    notices = conn.execute(
        "SELECT n.id, n.title, n.url, n.published_at, n.school_id, n.source_id, "
        "       s.name AS school_name "
        "FROM notices n JOIN schools s ON s.id = n.school_id").fetchall()

    cache_path = ROOT / args.cache
    cache = (json.loads(cache_path.read_text(encoding="utf-8"))
             if cache_path.exists() else {})
    hints = {}
    if not args.no_list:
        for src in sources:
            if args.school and args.school not in src["school_name"]:
                continue
            if args.source and args.source not in src["name"]:
                continue
            print(f"· 列表页 {src['school_name']} / {src['name']}")
            hints.update(list_dates(src, flags, src["domain"], cache))
            # 缓存增量落盘：扫描要跑几十分钟，中断了也不用从头再来
            cache_path.write_text(json.dumps(cache, ensure_ascii=False),
                                  encoding="utf-8")
            time.sleep(args.sleep)
        print(f"\n列表页共取到 {len(hints)} 条带日期的通知\n")

    changes = []
    for n in notices:
        if args.school and args.school not in n["school_name"]:
            continue
        old = n["published_at"] or ""
        exact = hints.get(n["url"], "")     # 只认列表行写明完整年份的日期
        if bad_date(old):
            # 一定错（空/畸形/未来）：先用列表日期；仍然没有时，只有"填错了"
            # 的那些（畸形/未来）才值得再抓一次详情页——原本为空的只是缺信息，
            # 一抓就是上千个详情页，不值当
            reason = "未来/畸形日期" if old else "原为空"
            need_detail = args.detail and not exact and bool(old)
            new = exact or (detail_date(n["url"]) if need_detail else "")
        elif exact and exact != old and not args.no_prefer_list:
            # 库内日期看着合法但可能来自正文日程，列表行的完整日期更权威
            reason, new = "改用列表日期", exact
        else:
            continue
        if new == old:
            continue
        changes.append({"id": n["id"], "school": n["school_name"], "old": old,
                        "new": new, "reason": reason, "title": n["title"],
                        "url": n["url"]})

    blanked = sum(1 for c in changes if not c["new"])
    print(f"待改 {len(changes)} 条（其中置空 {blanked} 条）：")
    for c in changes[:40]:
        print(f"  [{c['reason']}] {c['school']} | {c['old']} → {c['new'] or '(空)'} "
              f"| {c['title'][:38]}")
    if len(changes) > 40:
        print(f"  … 其余 {len(changes) - 40} 条见 CSV")

    def write_report(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["id", "school", "old", "new",
                                               "reason", "title", "url"])
            w.writeheader()
            w.writerows(changes)

    path = ROOT / args.report
    write_report(path)
    if args.apply and changes:
        # 另存带时间戳的一份：写库后重跑会显示"待改 0 条"，默认文件就被覆盖了
        stamp = time.strftime("%Y%m%d-%H%M")
        write_report(path.with_name(f"{path.stem}-applied-{stamp}{path.suffix}"))
    print(f"\n明细已写入 {path}")

    if not args.apply:
        print("（试运行，未写库；确认无误后加 --apply）")
        conn.close()
        return
    for c in changes:
        conn.execute("UPDATE notices SET published_at=? WHERE id=?",
                     (c["new"], c["id"]))
    conn.commit()
    print(f"已更新 {len(changes)} 条。建议随后执行："
          f"python run.py --backfill-meta（按新日期校正 deadline_iso）")
    conn.close()


if __name__ == "__main__":
    main()
