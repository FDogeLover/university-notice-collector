# -*- coding: utf-8 -*-
"""采集层：下载页面 HTML，自动探测编码。

默认用 requests 直接抓取；对启用反爬（如 HTTP 412 JS 挑战）的站点，
可用 use_browser=True 走 Playwright 真实浏览器渲染，跨请求复用同一浏览器实例。
"""
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Playwright 浏览器复用实例
_browser = None
_playwright = None


def _get_browser():
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright

        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
    return _browser


# 真实 Chrome 模式（瑞数反爬用）：持久化上下文，跨请求复用
_real_ctx = None
_real_pw = None
_real_page = None


def _get_real_context():
    """获取真实 Chrome 持久化上下文（有头 + 反自动化 + 独立用户数据目录）。

    首次调用会弹出真实 Chrome 窗口；之后复用同一上下文与 cookie。
    """
    global _real_ctx, _real_pw
    if _real_ctx is None:
        from playwright.sync_api import sync_playwright

        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        exe = next((c for c in chrome_candidates
                    if Path(c).exists()), None)
        if exe is None:
            raise RuntimeError("未找到本机 Chrome/Edge")
        profile = Path.home() / ".university_info_profile"
        profile.mkdir(parents=True, exist_ok=True)
        _real_pw = sync_playwright().start()
        _real_ctx = _real_pw.chromium.launch_persistent_context(
            str(profile), executable_path=exe, headless=False,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1366, "height": 900},
        )
    return _real_ctx


def close_real_browser():
    """关闭真实 Chrome 模式。"""
    global _real_ctx, _real_pw
    if _real_ctx is not None:
        try:
            _real_ctx.close()
        except Exception:  # noqa: BLE001
            pass
        _real_ctx = None
    if _real_pw is not None:
        try:
            _real_pw.stop()
        except Exception:  # noqa: BLE001
            pass
        _real_pw = None


def _http_get_real(url, wait_ms=5000):
    """用真实 Chrome（有头 + 持久化 cookie）访问，可过瑞数 JS 挑战。

    复用同一个长生命周期页面：不 close 页面，否则持久化上下文会随
    最后一个页面关闭而整体关闭（后续 new_page 报 context closed）。
    """
    global _real_page
    ctx = _get_real_context()
    if _real_page is None or _real_page.is_closed():
        _real_page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        _real_page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _real_page.wait_for_timeout(wait_ms)
        # 瑞数挑战：首访后刷新一次
        html = _real_page.content()
        if "$_ts" in html or len(html) < 1000:
            _real_page.reload(wait_until="domcontentloaded")
            _real_page.wait_for_timeout(wait_ms)
        _real_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _real_page.wait_for_timeout(1200)
        _real_page.evaluate("window.scrollTo(0, 0)")
        _real_page.wait_for_timeout(600)
        return _real_page.content()
    except Exception:  # noqa: BLE001
        try:
            return _real_page.content()
        except Exception:  # noqa: BLE001
            raise


def close_browser():
    """采集结束调用，释放浏览器实例。"""
    global _browser, _playwright
    if _browser is not None:
        try:
            _browser.close()
        except Exception:  # noqa: BLE001
            pass
        _browser = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        _playwright = None


def _http_get_browser(url, wait_ms=4500):
    """用真实浏览器渲染页面（可过 JS 反爬挑战 / 等待 JS 异步加载 / 懒加载图）。

    滚动到底部再回顶，触发懒加载图片加载；随后返回渲染后的 HTML 文本。
    """
    browser = _get_browser()
    page = browser.new_page()
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        # 滚动触发懒加载
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(800)
        return page.content()
    except Exception:  # noqa: BLE001
        try:
            return page.content()
        except Exception:  # noqa: BLE001
            raise
    finally:
        page.close()


def http_get(url, timeout=20, retries=2, encoding=None, use_browser=False,
             use_real_browser=False):
    """下载页面文本。

    use_real_browser=True 时用真实 Chrome（有头+持久化 cookie）抓取，可过瑞数反爬；
    use_browser=True 时走 Playwright 无头渲染；否则 requests 直接抓取。
    """
    if use_real_browser:
        last_err = None
        for i in range(retries + 1):
            try:
                return _http_get_real(url)
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (i + 1))
        raise RuntimeError(f"真实浏览器抓取失败 {url}: {last_err}")
    if use_browser:
        last_err = None
        for i in range(retries + 1):
            try:
                return _http_get_browser(url)
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (i + 1))
        raise RuntimeError(f"浏览器抓取失败 {url}: {last_err}")

    last_err = None
    for i in range(retries + 1):
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=timeout, verify=False
            )
            resp.raise_for_status()
            if encoding:
                resp.encoding = encoding
            else:
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"抓取失败 {url}: {last_err}")

def http_get_bytes(url, timeout=30, retries=2):
    """下载二进制内容（PDF/附件等），返回 bytes。"""
    last_err = None
    for i in range(retries + 1):
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=timeout, verify=False
            )
            resp.raise_for_status()
            return resp.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"下载失败 {url}: {last_err}")

