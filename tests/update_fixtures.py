# -*- coding: utf-8 -*-
"""回归测试 fixture 更新脚本：抓取各校真实列表页/详情页存入 tests/fixtures/。

用法（项目根目录执行）：
    python tests/update_fixtures.py            # 全量更新
    python tests/update_fixtures.py --list-only  # 只更新列表页

说明：
- 抓取复用 crawler.fetch.http_get（与采集主链路同一套 UA/编码/重试逻辑）；
- 额外加域名白名单校验：只允许下方 LIST_SOURCES 声明的官方域名及其子域；
- 反爬站点（browser: true）不纳入 fixture；每个列表页自动选 1 个正文
  质量达标的详情页一并保存；
- manifest.json 记录每个 fixture 的 url/domain，供测试参数化使用。
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import parse  # noqa: E402
from crawler.fetch import http_get  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# 参与回归测试的栏目：覆盖不同高校 CMS 结构（webplus / 博达 / 自研等）
# 列表页 fixture 名 → (url, domain)
LIST_SOURCES = {
    "list_njust_gs": ("https://gs.njust.edu.cn/", "njust.edu.cn"),
    "list_njust_zsw": ("https://gs.njust.edu.cn/zsw/", "njust.edu.cn"),
    "list_xidian_gr": ("https://gr.xidian.edu.cn/", "xidian.edu.cn"),
    "list_szu_gra": ("https://gra.szu.edu.cn/", "szu.edu.cn"),
    "list_ecnu_yjszs": ("https://yjszs.ecnu.edu.cn/", "ecnu.edu.cn"),
    "list_hust_gszs": ("https://gszs.hust.edu.cn/", "hust.edu.cn"),
    "list_sdu_grad": ("https://www.grad.sdu.edu.cn/", "sdu.edu.cn"),
}

# 需要附带详情页 fixture 的列表页（取解析到的第 1 个达标详情）
DETAIL_OF = ["list_njust_gs", "list_szu_gra", "list_xidian_gr"]

# 域名白名单：只允许 LIST_SOURCES 里声明的官方域名及其子域
_ALLOWED_DOMAINS = {d for _, d in LIST_SOURCES.values()}


def _validate(url):
    """抓取前校验：仅 https 且 host 属于白名单域名（含子域）。"""
    p = urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"仅允许 https 协议：{url}")
    host = (p.hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS):
        raise ValueError(f"域名不在白名单内：{host}")
    return url


def _get(url):
    return http_get(_validate(url), timeout=20, retries=1)


def _pick_detail(html, base_url, domain):
    """从列表页选第 1 个正文质量达标的详情页，返回 (title, url, html)。"""
    items = parse.parse_list(html, base_url, domain, max_items=15)
    for it in items:
        try:
            dhtml = _get(it["url"])
        except Exception:  # noqa: BLE001
            continue
        # 离线评估：屏蔽 iframe/PDF/图片网络兜底，只看静态正文质量
        real_iframe, real_pdf, real_img = (
            parse._extract_iframe_text, parse.extract_pdf_text,
            parse.extract_image_text)
        parse._extract_iframe_text = lambda *a, **k: ""
        parse.extract_pdf_text = lambda *a, **k: ""
        parse.extract_image_text = lambda *a, **k: ""
        try:
            detail = parse.parse_detail(dhtml, it["url"])
        finally:
            (parse._extract_iframe_text, parse.extract_pdf_text,
             parse.extract_image_text) = real_iframe, real_pdf, real_img
        if len(detail["content_md"]) >= 300:
            return it["title"], it["url"], dhtml
        time.sleep(0.3)
    return None, None, None


def main():
    list_only = "--list-only" in sys.argv
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = FIXTURE_DIR / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        old = {}

    for name, (url, domain) in LIST_SOURCES.items():
        print(f"== 抓取 {name}: {url}")
        try:
            html = _get(url)
        except Exception as e:  # noqa: BLE001
            print(f"   !! 抓取失败，保留旧 fixture: {e}")
            if name in old:
                manifest[name] = old[name]
            continue
        items = parse.parse_list(html, url, domain, max_items=50)
        print(f"   解析到 {len(items)} 条")
        if not items:
            print("   !! 解析到 0 条，疑似改版/反爬，保留旧 fixture")
            if name in old:
                manifest[name] = old[name]
            continue
        (FIXTURE_DIR / f"{name}.html").write_text(html, encoding="utf-8")
        manifest[name] = {"url": url, "domain": domain, "min_items": 1}

        if not list_only and name in DETAIL_OF:
            title, durl, dhtml = _pick_detail(html, url, domain)
            if dhtml:
                dname = name.replace("list_", "detail_")
                (FIXTURE_DIR / f"{dname}.html").write_text(dhtml, encoding="utf-8")
                manifest[dname] = {"url": durl, "domain": domain}
                print(f"   详情页 fixture: {dname} ← {title[:30]}")
            else:
                print("   !! 未找到达标详情页，跳过")
        time.sleep(0.5)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nmanifest 已写入 {manifest_path}")


if __name__ == "__main__":
    main()
