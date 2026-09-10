# -*- coding: utf-8 -*-
"""采集层：下载页面 HTML，自动探测编码。

默认用 requests 直接抓取；对启用反爬（如 HTTP 412 JS 挑战）的站点，
可用 use_browser=True 走 Playwright 真实浏览器渲染，跨请求复用同一浏览器实例。
"""
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# UA 池：轮换使用，降低被单一 UA 反爬识别/拦截的概率
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
    "Gecko/20100101 Firefox/127.0",
]
_ua_idx = 0


def _next_ua():
    global _ua_idx
    ua = UA_POOL[_ua_idx % len(UA_POOL)]
    _ua_idx += 1
    return ua


def _browser_headers(url):
    """带浏览器特征与动态 Referer 的请求头。"""
    headers = dict(HEADERS)
    headers["User-Agent"] = _next_ua()
    p = urlparse(url)
    if p.scheme and p.netloc:
        headers["Referer"] = f"{p.scheme}://{p.netloc}/"
    return headers


HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# Playwright 浏览器复用实例
_browser = None
_playwright = None

# 单个页面浏览器操作的整体超时（秒）。站点 JS 死循环（无限循环/持续导航）
# 会让 evaluate/content 永久挂起——playwright 任何超时参数都无法中断
# 渲染进程内的死循环，唯一可靠手段是杀掉 chromium 进程（连接断开后
# 阻塞调用立即抛 TargetClosedError 返回）。
_BROWSER_PAGE_TIMEOUT = 60
# chromium 进程匹配特征（拼装避免 pkill 匹配到调用方自身 cmdline）
_CHROMIUM_MATCH = "chrome-headless" + "-shell-linux64" + "/chrome-headless-shell"


class _BrowserTimeout(Exception):
    """浏览器单页操作超时（站点 JS 卡死，浏览器已强杀重建）。"""


def _kill_browser_processes():
    """强杀当前 playwright chromium 进程树（含卡死的渲染进程）。"""
    import subprocess

    subprocess.run(["pkill", "-9", "-f", _CHROMIUM_MATCH],
                   check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def _browser_call_with_timeout(fn, *args, **kwargs):
    """以超时护栏执行浏览器操作 fn。

    主线程同步执行 fn（playwright 必须与启动它的线程一致），daemon 线程
    计时；超时后由 watchdog 强杀 chromium 进程 → fn 内阻塞调用因连接
    断开抛异常 → 恢复控制并抛 _BrowserTimeout（浏览器已损坏，调用方需
    重建实例）。
    """
    import threading

    timed_out = threading.Event()
    finished = threading.Event()

    def watchdog():
        if not finished.wait(_BROWSER_PAGE_TIMEOUT):
            timed_out.set()
            _kill_browser_processes()

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        return fn(*args, **kwargs)
    except _BrowserTimeout:
        raise
    except Exception as e:  # noqa: BLE001
        if timed_out.is_set():
            raise _BrowserTimeout(
                f"browser page exceeded {_BROWSER_PAGE_TIMEOUT}s (chromium killed): {e}"
            ) from e
        raise
    finally:
        finished.set()


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
    """获取真实 Chrome 持久化上下文。

    本机有 Chrome/Edge 时用有头持久化上下文（可过瑞数等强反爬，窗口置于
    屏幕外）；服务器/无桌面环境没有本机 Chrome 时，回退 Playwright 自带
    chromium 无头渲染，保证 real_browser 栏目在服务器也能运行。
    """
    global _real_ctx, _real_pw
    if _real_ctx is None:
        from playwright.sync_api import sync_playwright

        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            # Linux 常见位置（服务器无头环境）
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium", "/usr/bin/chromium-browser",
        ]
        exe = next((c for c in chrome_candidates
                    if Path(c).exists()), None)
        profile = Path.home() / ".university_info_profile"
        profile.mkdir(parents=True, exist_ok=True)
        _real_pw = sync_playwright().start()
        if exe is None:
            # 服务器兜底：Playwright 自带 chromium 无头
            _real_ctx = _real_pw.chromium.launch_persistent_context(
                str(profile), headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-first-run", "--no-default-browser-check"],
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1366, "height": 900},
            )
        else:
            _real_ctx = _real_pw.chromium.launch_persistent_context(
                str(profile), executable_path=exe, headless=False,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-first-run", "--no-default-browser-check",
                      # 窗口定位到屏幕外：不遮挡用户桌面、不抢前台焦点。
                      # 不能用最小化——最小化会让页面进入后台可见性状态，
                      # 反而触发部分 WAF（如瑞数）的检测。
                      "--window-position=-32000,-32000"],
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1366, "height": 900},
            )
    return _real_ctx


def close_real_browser():
    """关闭真实 Chrome 模式。"""
    global _real_ctx, _real_pw, _real_page
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
    _real_page = None


def _get_real_content(page, attempts=4, wait_ms=3000):
    """取页面 HTML；瑞数等站点会持续跳转导致 content() 报
    "page is navigating"，等一拍重试即可。"""
    last_err = None
    for _ in range(attempts):
        try:
            return page.content()
        except Exception as e:  # noqa: BLE001
            last_err = e
            page.wait_for_timeout(wait_ms)
    raise last_err


def _http_get_real(url, wait_ms=5000):
    """用真实 Chrome（有头 + 持久化 cookie）访问，可过瑞数 JS 挑战。

    复用同一个长生命周期页面：不 close 页面，否则持久化上下文会随
    最后一个页面关闭而整体关闭（后续 new_page 报 context closed）。
    同样受超时护栏保护，卡死时强杀 chromium 并重建上下文。
    """
    try:
        return _browser_call_with_timeout(_http_get_real_inner, url, wait_ms)
    except _BrowserTimeout:
        close_real_browser()
        raise RuntimeError(f"真实浏览器渲染超时（>{_BROWSER_PAGE_TIMEOUT}s，已重建）: {url}")


def _http_get_real_inner(url, wait_ms=5000):
    global _real_page
    ctx = _get_real_context()
    if _real_page is None or _real_page.is_closed():
        _real_page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        _real_page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _real_page.wait_for_timeout(wait_ms)
        # 瑞数挑战：首访后刷新一次
        html = _get_real_content(_real_page)
        if "$_ts" in html or len(html) < 1000:
            _real_page.reload(wait_until="domcontentloaded")
            _real_page.wait_for_timeout(wait_ms)
        _real_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _real_page.wait_for_timeout(1200)
        _real_page.evaluate("window.scrollTo(0, 0)")
        _real_page.wait_for_timeout(600)
        return _get_real_content(_real_page)
    except Exception:  # noqa: BLE001
        try:
            return _get_real_content(_real_page, attempts=2, wait_ms=2000)
        except Exception:  # noqa: BLE001
            raise


def close_browser():
    """关闭无头浏览器实例，释放资源。"""
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
    受 watchdog 超时保护：站点 JS 卡死时强杀 chromium 并抛异常，由上层
    降级逻辑继续下一站点，避免单个站点拖垮整个采集。
    """
    try:
        return _browser_call_with_timeout(_http_get_browser_inner, url, wait_ms)
    except _BrowserTimeout:
        # 浏览器已损坏（chromium 被强杀），置空以便下次自动重建
        global _browser, _playwright
        _browser = None
        _playwright = None
        raise RuntimeError(
            f"浏览器渲染超时（>{_BROWSER_PAGE_TIMEOUT}s，chromium 已重建）: {url}")


def _http_get_browser_inner(url, wait_ms=4500):
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
                url, headers=_browser_headers(url), timeout=timeout, verify=True
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
                url, headers=_browser_headers(url), timeout=timeout, verify=True
            )
            resp.raise_for_status()
            return resp.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"下载失败 {url}: {last_err}")

