# -*- coding: utf-8 -*-
"""全量审计探针：把「栏目在真实网络里到底能不能采到」量出来。

给项目完成度评估用：对 config/schools.yaml 里的每个栏目做一次列表页实探
（HTTP 状态、字节数、parse_list 解析条数、最新列表日期），并把它与库内
实际数据（通知数、有正文数、空发布日期数、失败次数、最近尝试时间）拼在一起，
输出可直接统计的 JSON。

安全边界：与 scripts/find_list_url.py 同一套受控请求——请求前就地校验协议、
主机名与 DNS 解析出的每个 IP（拒绝环回/私有/保留地址），跳转逐跳复检，
因此只会访问公网 http/https。

用法：
    python scripts/audit_probe.py --db-stats                      # 只看库内数据
    python scripts/audit_probe.py --sweep --no-browser --limit 5  # 小样试跑
    python scripts/audit_probe.py --sweep --json-out out/a.json    # 全量实探
    python scripts/audit_probe.py --url <URL> --domain <域名>      # 单栏目复核
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from crawler import parse  # noqa: E402
from db import store  # noqa: E402
from find_list_url import _parse_count, _probe_headers, _render, guarded_get, safe_url  # noqa: E402

JS_SHELL = ("enable javascript", "please enable js", "正在加载", "loading...",
            "window.location", "document.write")


def load_sources(limit=0, school=None):
    """从 yaml 读栏目清单（yaml 是源头，与 run.py 同口径）。"""
    cfg = yaml.safe_load(
        (ROOT / "config" / "schools.yaml").read_text(encoding="utf-8"))
    rows = []
    for sc in cfg.get("schools", []):
        if school and school not in sc["name"]:
            continue
        for src in sc.get("sources", []):
            rows.append({
                "school": sc["name"], "domain": sc.get("domain", ""),
                "name": src.get("name", ""), "url": src["url"],
                "stype": src.get("stype", "研究生教育"),
                "browser": bool(src.get("browser")),
                "real_browser": bool(src.get("real_browser")),
            })
    return rows[:limit] if limit else rows


def db_stats(db_path=None):
    """库内实况：每个栏目的通知数/正文率/空日期/失败次数。"""
    conn = store.connect(db_path)
    stats = {}
    rows = conn.execute(
        "SELECT s.id, s.url,"
        "  (SELECT COUNT(*) FROM notices n WHERE n.source_id=s.id) AS notices,"
        "  (SELECT COUNT(*) FROM notices n WHERE n.source_id=s.id"
        "     AND n.content_md IS NOT NULL AND n.content_md != '') AS with_content,"
        "  (SELECT COUNT(*) FROM notices n WHERE n.source_id=s.id"
        "     AND (n.published_at IS NULL OR n.published_at='')) AS empty_date,"
        "  (SELECT MAX(n.published_at) FROM notices n WHERE n.source_id=s.id"
        "     AND n.published_at != '') AS newest_published,"
        "  (SELECT MAX(n.fetched_at) FROM notices n WHERE n.source_id=s.id)"
        "     AS newest_fetched,"
        "  (SELECT COUNT(*) FROM fetch_logs f WHERE f.source_id=s.id"
        "     AND f.status='error') AS errors,"
        "  (SELECT MAX(f.run_at) FROM fetch_logs f WHERE f.source_id=s.id)"
        "     AS last_run "
        "FROM sources s").fetchall()
    for r in rows:
        stats[r["url"].rstrip("/")] = dict(r)
    today = date.today().isoformat()
    total = conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"]
    future = conn.execute(
        "SELECT COUNT(*) c FROM notices WHERE published_at > ?",
        (today,)).fetchone()["c"]
    empty = conn.execute(
        "SELECT COUNT(*) c FROM notices "
        "WHERE published_at IS NULL OR published_at=''").fetchone()["c"]
    nocontent = conn.execute(
        "SELECT COUNT(*) c FROM notices "
        "WHERE content_md IS NULL OR content_md=''").fetchone()["c"]
    schools = conn.execute(
        "SELECT COUNT(*) c FROM schools WHERE enabled=1").fetchone()["c"]
    conn.close()
    return {
        "summary": {"notices": total, "schools_enabled": schools,
                    "published_future": future, "published_empty": empty,
                    "no_content": nocontent},
        "sources": stats,
    }


def probe(url, domain, stype, allow_browser=False):
    """实探一个栏目列表页：受控 GET + parse_list 计数（必要时浏览器渲染）。"""
    rec = {"status": 0, "bytes": 0, "err": "", "parse_count": 0,
           "newest_date": "", "sample_title": "", "ms": 0,
           "version": "", "links": 0, "scripts": 0, "via": "requests"}
    if not safe_url(url):
        # 区分 DNS 失败与非公网地址（原来一律报"拒绝非公网地址"，误导过排查）
        from crawler.fetch import target_problem
        rec["err"] = target_problem(url) or "目标不可访问"
        return rec
    t0 = time.time()
    try:
        # 探针超时对齐生产（crawler/fetch.py 的 http_get 用 20s；原来 12s
        # 会让响应 16s 的站点（华北电力教务处）被误判成不可达）
        r = guarded_get(url, _probe_headers(url), timeout=20)
        rec["status"] = r.status_code
        r.encoding = r.apparent_encoding or "utf-8"
        html = r.text or ""
        rec["bytes"] = len(r.content or b"")
        rec["version"] = (r.headers.get("Server") or "")[:40]
        rec["links"] = html.count("<a ")
        rec["scripts"] = html.lower().count("<script")
        rec["_head"] = html[:6000].lower()
        if r.status_code < 400:
            items = parse.parse_list(html, url, domain, max_items=10,
                                     stype=stype)
            rec["parse_count"] = len(items)
            if items:
                rec["newest_date"] = max((i.get("date") or "" for i in items),
                                         default="")
                rec["sample_title"] = items[0]["title"][:60]
    except Exception as e:  # noqa: BLE001
        rec["err"] = f"{type(e).__name__}: {e}"[:120]
    if rec["parse_count"] == 0 and allow_browser:
        # 有 browser/real_browser 标记、或 HTTP 400+ 的栏目：补一次浏览器渲染
        # 再判定。瑞数类站点对裸请求必回 412/403，原来一律计成"解析 0 条"，
        # 把生产端实际在正常出数的栏目（安大信息公开、石河子等）误报成问题。
        html = _render(url)   # 浏览器渲染（URL 已过边界校验）
        if html:
            items = parse.parse_list(html, url, domain, max_items=10,
                                     stype=stype)
            rec["parse_count"] = len(items)
            rec["via"] = "browser"
            rec["bytes"] = max(rec["bytes"], len(html))
            if items:
                rec["newest_date"] = max(
                    (i.get("date") or "" for i in items), default="")
                rec["sample_title"] = items[0]["title"][:60]
    rec["ms"] = int((time.time() - t0) * 1000)
    return rec


def classify(rec, browser):
    """机械分类：只用探到的证据，不做推测。

    先看解析条数：能解析出通知就是"正常"，哪怕状态码是 412（真实浏览器
    通道过挑战后照样出数据）——原实现先看 err/status，制造了"不可达却
    parse=10"这种自相矛盾的分类。
    """
    if rec["parse_count"] > 0:
        return "正常"
    if rec["err"]:
        return "不可达/被拒"
    if rec["status"] >= 400:
        return f"HTTP {rec['status']}"
    if rec["bytes"] < 3000:
        return "空页(疑似WAF)"
    low = rec.get("_head", "")
    if any(k in low for k in JS_SHELL):
        return "JS空壳"
    if rec["links"] < 5:
        return ("无静态链接(疑似JS渲染)" if rec.get("scripts", 0) >= 3
                else "无静态链接")
    return "结构不符/需浏览器" if browser else "解析0条"


def run_sweep(args, vantage):
    sources = load_sources(args.limit, args.school)
    stats = db_stats(args.db)["sources"] if not args.no_db else {}
    out = []
    browser_rows = [s for s in sources if s["browser"] or s["real_browser"]]
    plain_rows = [s for s in sources if s not in browser_rows]
    print(f"[{vantage}] 实探 {len(sources)} 栏目（普通 {len(plain_rows)} / "
          f"需浏览器 {len(browser_rows)}），并发 {args.workers}", flush=True)

    def one(s):
        # 带 browser/real_browser 标记的栏目本来就走渲染通道，探针也照做
        allow_b = (not args.no_browser) and (s["browser"] or s["real_browser"])
        rec = probe(s["url"], s["domain"], s["stype"], allow_browser=allow_b)
        st = stats.get(s["url"].rstrip("/"), {})
        rec.update({"school": s["school"], "source": s["name"], "url": s["url"],
                    "stype": s["stype"],
                    "browser": bool(s["browser"] or s["real_browser"]),
                    "db_notices": st.get("notices", 0),
                    "db_with_content": st.get("with_content", 0),
                    "db_empty_date": st.get("empty_date", 0),
                    "db_newest_published": st.get("newest_published") or "",
                    "db_newest_fetched": st.get("newest_fetched") or "",
                    "db_errors": st.get("errors", 0),
                    "db_last_run": st.get("last_run") or ""})
        rec["category"] = classify(rec, rec["browser"])
        rec.pop("_head", None)
        print(f"  {rec['category']:16s} {rec['school']}/{rec['source']} "
              f"parse={rec['parse_count']} db={rec['db_notices']}", flush=True)
        return rec

    if args.no_browser:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            out = list(ex.map(one, sources))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            out = list(ex.map(one, plain_rows))
        for s in browser_rows:           # 浏览器实例非线程安全，串行
            out.append(one(s))

    cats = {}
    for r in out:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    payload = {"vantage": vantage, "generated_at": time.strftime("%F %T"),
               "sources_total": len(out), "categories": cats, "sources": out}
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"[{vantage}] JSON → {p}")
    if args.brief_out:
        # 紧凑版：给工作流/脚本消费，字段位置固定、体积小
        rows = [[r["school"], r["source"], r["url"], r["category"],
                 r["parse_count"], r["bytes"], r["db_notices"],
                 r["db_with_content"], r["db_empty_date"], r["db_errors"],
                 r["db_last_run"], r["newest_date"], r["sample_title"]]
                for r in out]
        brief = {"vantage": vantage, "generated_at": payload["generated_at"],
                 "categories": cats, "rows": rows,
                 "cols": ["school", "source", "url", "category", "parse_count",
                          "bytes", "db_notices", "db_with_content",
                          "db_empty_date", "db_errors", "db_last_run",
                          "newest_date", "sample_title"]}
        bp = Path(args.brief_out)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
        print(f"[{vantage}] 精简 JSON → {bp}")
    print(f"[{vantage}] 分类统计: {json.dumps(cats, ensure_ascii=False)}")
    return payload


def main():
    ap = argparse.ArgumentParser(description="栏目可用性全量审计探针")
    ap.add_argument("--sweep", action="store_true", help="实探全部栏目")
    ap.add_argument("--url", help="单栏目复核")
    ap.add_argument("--domain", default="", help="--url 时的学校域名")
    ap.add_argument("--stype", default="研究生教育", help="--url 时的栏目领域")
    ap.add_argument("--db-stats", action="store_true", help="只输出库内统计")
    ap.add_argument("--db", default=None, help="库路径（默认 data/university.db）")
    ap.add_argument("--limit", type=int, default=0, help="最多探多少个栏目")
    ap.add_argument("--school", help="只探名字包含该词的学校")
    ap.add_argument("--no-browser", action="store_true", help="不做浏览器渲染")
    ap.add_argument("--no-db", action="store_true", help="不合并库内数据")
    ap.add_argument("--workers", type=int, default=8, help="并发数")
    ap.add_argument("--json-out", help="JSON 输出路径（可读，供人看）")
    ap.add_argument("--brief-out", help="精简 JSON 输出路径（供脚本消费）")
    ap.add_argument("--vantage", default="local", help="视角标记（local/server）")
    args = ap.parse_args()

    if args.db_stats:
        print(json.dumps(db_stats(args.db)["summary"], ensure_ascii=False,
                         indent=2))
        return
    if args.url:
        # 单栏目复核默认带浏览器兜底：瑞数类站点裸请求必被挡，只按 requests
        # 判定会把生产端正常出数的栏目误报成问题（--no-browser 可关掉）
        rec = probe(args.url, args.domain, args.stype,
                    allow_browser=not args.no_browser)
        rec["category"] = classify(rec, False)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return
    if args.sweep:
        run_sweep(args, args.vantage)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
