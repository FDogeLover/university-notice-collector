# -*- coding: utf-8 -*-
"""诊断「零通知 / 采集失败」栏目的真实原因，并为它找可采集的通知列表 URL。

为什么需要它：巡检只给出"哪个栏目有问题"，但原因分几类，处理方式完全不同——
  1) brotli(br) 压缩解不开 → 页面变乱码、解析 0 条（URL 本身没错）
  2) 站点对本机 IP 返回空页（WAF）→ 换 URL 没用，只能换出口或放弃
  3) JS 单页 / CMS 站：正文与栏目菜单都由 JS 渲染 → 得从渲染后的 HTML 找列表地址
  4) 死域名 / 404 → 需要搜替代域名（交给复盘 agent）
  5) 页面能打开但只是导航页 → 从页内导航里找「通知公告」子栏目 URL

本脚本自动分辨 1/2/3/5，并把候选 URL 用 parse_list 验证「真能解析出通知」：
解析条数 > 0 才算通过——HTTP 200 不算数（这是踩过的坑）。

安全边界：只访问公网 http/https。所有直接请求都走 guarded_get()——它在发起
请求前就地校验协议、主机名与 DNS 解析出的每个 IP（拒绝环回/私有/保留地址），
并且不自动跟随跳转，而是逐跳校验后再跟进，避免被重定向带进内网。

用法：
  python scripts/find_list_url.py --diagnose [--limit N]
  python scripts/find_list_url.py --school 中国石油大学（华东） --source 本科招生网 [--apply]
"""
import argparse
import ipaddress
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import fetch, parse  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 只允许公网 http/https：拒绝环回、私有、保留地址与内网主机名
_BAD_HOST_SUFFIX = (".local", ".internal", ".localhost", ".home.arpa")
LIST_HINT = re.compile(r"通知|公告|动态|新闻|要闻|信息|招生|就业|公示|tzgg|gggs|list|news",
                       re.IGNORECASE)
_MAX_HOPS = 3


def safe_url(url):
    """URL 是否只指向公网 http/https（预检用；请求点仍有独立校验）。"""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(_BAD_HOST_SUFFIX):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def guarded_get(url, headers, timeout=12):
    """受控 GET：请求前就地校验目标，跳转不自动跟随，逐跳复检后再跟进。"""
    cur = url
    for _ in range(_MAX_HOPS + 1):
        parsed = urlparse(cur)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"仅允许 http/https: {cur}")
        if not host or host == "localhost" or host.endswith(_BAD_HOST_SUFFIX):
            raise ValueError(f"拒绝内网主机名: {cur}")
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as e:
            raise ValueError(f"域名无法解析: {cur}") from e
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                    or not ip.is_global):
                raise ValueError(f"拒绝非公网地址 {ip}（防 SSRF）: {cur}")
        resp = requests.get(cur, headers=headers, timeout=timeout,
                            verify=True, allow_redirects=False)
        nxt = resp.headers.get("Location")
        if not (resp.is_redirect and nxt):
            return resp
        cur = urljoin(cur, nxt)
    raise ValueError(f"跳转次数过多: {url}")


# --br 时按老口径宣告 brotli：用于复查"有多少栏目是被 br 解压问题坑掉的"
_ADVERTISE_BR = False


def _probe_headers(url):
    headers = fetch._browser_headers(url)
    if _ADVERTISE_BR:
        headers["Accept-Encoding"] = "gzip, deflate, br"
    return headers


def _raw_probe(url):
    """原始探测：状态码、Content-Encoding、正文是否可读（不经解析层）。"""
    try:
        r = guarded_get(url, _probe_headers(url))
    except Exception as e:  # noqa: BLE001
        return {"err": type(e).__name__, "status": 0, "ce": "", "readable": False,
                "len": 0, "html": ""}
    ce = (r.raw.headers.get("Content-Encoding") or "").lower()
    # 与 crawler.fetch.http_get 一致的解码口径：按实际内容猜编码，
    # 否则站点不给 charset 时 requests 会按 ISO-8859-1 解出乱码
    r.encoding = r.apparent_encoding or "utf-8"
    html = r.text or ""
    readable = bool(re.search(r"<a\s|通知|公告", html[:6000]))
    return {"err": None, "status": r.status_code, "ce": ce, "readable": readable,
            "len": len(r.content), "html": html}


def _parse_count(url, domain, html, stype=None):
    """候选验证：解析出的通知条数（>0 才算真的能用）。"""
    try:
        return len(parse.parse_list(html, url, domain, max_items=10, stype=stype))
    except Exception:  # noqa: BLE001
        return 0


def _tcp_reachable(url, timeout=3, total_budget=6):
    """同主机 80 或 443 能否在限定时间内建立 TCP 连接（不做 HTTP 请求）。

    浏览器兜底（_render）代价极高：瑞数类站点首次渲染要等 JS 挑战，不可达主机
    会让真实浏览器通道连续重试——`--school 华中科技大学 --source 研究生院` 曾因
    https 443 挂起、浏览器兜底重试而 280s 不返回（review-2026-09-23 工具侧现象）。
    先做 TCP 预检，连 SYN 都不通的主机直接判定"渲染也没用"，跳过兜底。

    total_budget 是**整个预检的总时间上限**：多 IP 站点（如复旦 3 个 A 记录）逐 IP
    各等一次 timeout 会累加到几十秒，违背"快速跳过"的初衷，故到点即停。
    """
    import time as _time

    p = urlparse(url)
    host = p.hostname
    if not host:
        return False
    deadline = _time.monotonic() + total_budget
    tried = []
    for port in (p.port or (443 if p.scheme == "https" else 80), 80, 443):
        if port in tried:
            continue
        tried.append(port)
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError:
            continue
        for fam, st, proto, cn, sa in infos:
            if fam != socket.AF_INET:      # 与 fetch 层一致：只走 IPv4
                continue
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return False
            s = socket.socket(fam, socket.SOCK_STREAM)
            s.settimeout(min(timeout, remaining))
            try:
                s.connect(sa)
                return True
            except OSError:
                continue
            finally:
                s.close()
    return False


def _render(url):
    """浏览器渲染取 HTML（URL 先过边界校验；实际请求由 crawler 层负责）。

    先试真实浏览器通道（本机 Chrome 或服务器 Xvfb 下能过瑞数类 WAF），再试
    无头。瑞数站点对无头 chromium 直接回空壳，只试无头会把"生产端其实正常
    出数"的栏目误判成拿不到。

    渲染前先 TCP 预检：80/443 都连不上的主机（443 挂起类）渲染同样拿不到，
    直接返回空，避免浏览器通道重试把单栏目诊断拖过数分钟。
    """
    if not safe_url(url):
        return ""
    if not _tcp_reachable(url):
        print(f"  · TCP 预检失败（80/443 均不可达），跳过浏览器渲染: {url}")
        return ""
    for kwargs in ({"use_real_browser": True}, {"use_browser": True}):
        try:
            html = fetch.http_get(url, **kwargs)
            if html:
                return html
        except Exception:  # noqa: BLE001
            continue
    return ""


def _candidates_from(html, base_url, domain):
    """从页面里挑出「可能是通知列表」的同域链接。"""
    out, seen = [], set()
    for href, text in re.findall(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        text = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text))[:24]
        url = urljoin(base_url, href.split("#")[0])
        p = urlparse(url)
        if not p.scheme.startswith("http") or p.netloc != urlparse(base_url).netloc:
            continue
        if re.search(r"\.(pdf|docx?|xlsx?|zip|rar)$", p.path, re.I):
            continue
        if not LIST_HINT.search(url + " " + text):
            continue
        key = (url, text)
        if key in seen:
            continue
        seen.add(key)
        out.append((url, text))
    return out


def diagnose(url, domain, stype=None):
    """判定单个栏目的失败类型，返回 (类型, 说明)。"""
    if not safe_url(url):
        return "非法URL", "非公网 http/https，已跳过"
    pr = _raw_probe(url)
    if pr["err"]:
        kind = "死域名" if "解析" in pr["err"] or "Resolv" in pr["err"] else "网络不可达"
        return kind, pr["err"]
    if pr["status"] >= 400:
        return f"HTTP {pr['status']}", "站点返回错误码"
    if pr["ce"] == "br" and not pr["readable"]:
        return "br 未解压", "站点返回 brotli 压缩，环境无 brotli 库 → 页面变乱码"
    if pr["len"] < 3000 and not pr["readable"]:
        return "空页(WAF?)", f"仅 {pr['len']} 字节空文档，疑似出口 IP 被拦"
    n = _parse_count(url, domain, pr["html"], stype=stype)
    if n:
        return "正常", f"解析 {n} 条"
    html2 = _render(url)
    n2 = _parse_count(url, domain, html2, stype=stype) if html2 else 0
    if n2:
        return "可渲染", f"requests 解析 0 条，浏览器渲染后 {n2} 条（建议加 browser 标记）"
    return "导航页/结构不识", f"可读 {pr['len']} 字节但解析 0 条（导航页或列表结构不支持）"


def find_candidates(school, source, domain, stype=None, use_browser=False):
    """为一个栏目找候选列表 URL：页内导航 + 渲染兜底，逐个用解析条数验证。"""
    url = source["url"]
    pr = _raw_probe(url)
    html = pr["html"]
    if pr["ce"] == "br" and not pr["readable"]:
        print("  ! 该站返回 br 压缩且本地解不开，改用 gzip-only 请求头重取")
        try:
            r = guarded_get(url, {**fetch._browser_headers(url),
                                  "Accept-Encoding": "gzip, deflate"})
            html = r.text
        except Exception as e:  # noqa: BLE001
            print("    重取失败:", e)
    if not html or not re.search(r"<a\s", html):
        print("  · 原始 HTML 无链接，改用浏览器渲染后查找")
        html = _render(url)
    cands = _candidates_from(html, url, domain)
    print(f"  从 {url} 的导航里提取到 {len(cands)} 个候选")
    n_self = _parse_count(url, domain, html, stype=stype)
    print(f"  现 URL 自身解析 {n_self} 条"
          + ("（已可用，无需换 URL）" if n_self else "（确实解析不出通知）"))
    hits = []
    for cand, text in cands:
        cpr = _raw_probe(cand)
        if cpr["err"] or not cpr["html"]:
            continue
        n = _parse_count(cand, domain, cpr["html"], stype=stype)
        if not n and use_browser:
            html2 = _render(cand)
            n = _parse_count(cand, domain, html2, stype=stype) if html2 else 0
        if n:
            hits.append((n, cand, text))
            print(f"    ✓ 解析 {n:2d} 条  {cand}  「{text}」")
    if not hits:
        print("    ✗ 导航里没有能直接解析出通知的链接（可能是纯 JS 渲染站，"
              "需改用其接口 URL 或人工处理）")
    return sorted(hits, reverse=True)


def apply_url(school_name, source_name, new_url):
    """把某校某栏目的 URL 就地改掉（写入后回读校验）。"""
    path = ROOT / "config" / "schools.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    in_school = False
    for i, line in enumerate(lines):
        if re.match(r"^-\s+name:\s*" + re.escape(school_name) + r"\s*$", line):
            in_school = True
            continue
        if in_school and re.match(r"^-\s+name:", line):
            in_school = False
        if not in_school:
            continue
        if re.match(r"^\s+-\s+name:\s*" + re.escape(source_name) + r"\s*$", line):
            for j in range(i + 1, min(i + 6, len(lines))):
                if re.match(r"^\s+url:\s*", lines[j]):
                    old = lines[j]
                    lines[j] = re.sub(r"^\s+url:\s*.*$", f"    url: {new_url}", lines[j])
                    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    print(f"  已更新 {school_name} / {source_name}:\n"
                          f"    {old.strip()}\n    → url: {new_url}")
                    return True
            print(f"  未找到 {source_name} 的 url 行")
            return False
    print(f"  未找到学校 {school_name}")
    return False


def problem_rows(limit=0):
    """读最新巡检报告里的问题栏目：(学校, 栏目, URL)。"""
    reports = sorted((ROOT / "logs").glob("inspection-*.txt"))
    if not reports:
        return None, []
    rows = []
    for line in reports[-1].read_text(encoding="utf-8").splitlines():
        m = re.search(r"\]\s*(\S+?)（.*?）\s*\|\s*(.+?)\s*\|\s*(https?://\S+)\s*\|", line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    return reports[-1].name, (rows[:limit] if limit else rows)


def census():
    """只看响应头与可读性，不做解析/渲染：快速统计各类「拿不到正文」的栏目。"""
    name, rows = problem_rows()
    if not rows:
        print("没有巡检报告，先跑 scripts/inspect_sources.py")
        return
    print(f"{name}：{len(rows)} 个问题栏目，按当前请求头口径普查\n")
    stat = {}
    cases = {}
    for school, src, url in rows:
        pr = _raw_probe(url)
        if pr["err"]:
            key = "死域名/不可达"
        elif pr["status"] >= 400:
            key = f"HTTP {pr['status']}"
        elif pr["ce"] == "br" and not pr["readable"]:
            key = "br 未解压（本次修复的对象）"
        elif pr["len"] < 3000 and not pr["readable"]:
            key = "空页(WAF?)"
        elif pr["readable"]:
            key = "正文可读（问题在解析或列表结构）"
        else:
            key = "其它"
        stat[key] = stat.get(key, 0) + 1
        cases.setdefault(key, []).append(f"{school} / {src}  {url}")
    for k, v in sorted(stat.items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")
        for line in cases[k][:6]:
            print(f"          - {line}")


def main():
    ap = argparse.ArgumentParser(description="诊断问题栏目并找通知列表 URL")
    ap.add_argument("--diagnose", action="store_true", help="诊断巡检报告里的所有问题栏目")
    ap.add_argument("--census", action="store_true",
                    help="只按响应头快速普查（不解析不渲染），配合 --br 可复核旧口径")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条")
    ap.add_argument("--school", help="学校名（与 --source 配合）")
    ap.add_argument("--source", help="栏目名")
    ap.add_argument("--browser", action="store_true", help="候选验证时额外用浏览器渲染试一次")
    ap.add_argument("--br", action="store_true",
                    help="按老口径宣告 br 压缩（复查有多少栏目被 br 解压问题坑掉）")
    ap.add_argument("--apply", action="store_true", help="把最佳候选写入 config/schools.yaml")
    args = ap.parse_args()

    global _ADVERTISE_BR
    _ADVERTISE_BR = args.br

    if args.census:
        census()
        return

    if args.diagnose:
        name, rows = problem_rows(args.limit)
        if not rows:
            print("没有巡检报告，先跑 scripts/inspect_sources.py")
            return
        print(f"{name}：{len(rows)} 个问题栏目\n")
        stat = {}
        for school, src, url in rows:
            dom = urlparse(url).netloc.split(":")[0]
            kind, note = diagnose(url, dom)
            stat[kind] = stat.get(kind, 0) + 1
            print(f"  [{kind}] {school} / {src}\n      {url}\n      {note}", flush=True)
        print("\n分类统计：")
        for k, v in sorted(stat.items(), key=lambda x: -x[1]):
            print(f"  {v:4d}  {k}")
        return

    if not args.school or not args.source:
        ap.print_help()
        return
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "schools.yaml").read_text(encoding="utf-8"))
    school = next((s for s in cfg["schools"] if s["name"] == args.school), None)
    if not school:
        print(f"配置里没有学校：{args.school}")
        return
    src = next((x for x in school.get("sources", []) if x["name"] == args.source), None)
    if not src:
        print(f"{args.school} 配置里没有栏目：{args.source}")
        return
    print(f"{args.school} / {args.source}\n  现 URL: {src['url']}")
    hits = find_candidates(args.school, src, school.get("domain", ""),
                           stype=src.get("stype"), use_browser=args.browser)
    if hits and args.apply:
        apply_url(args.school, args.source, hits[0][1])


if __name__ == "__main__":
    main()
