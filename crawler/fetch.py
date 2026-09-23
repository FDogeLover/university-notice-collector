# -*- coding: utf-8 -*-
"""采集层：下载页面 HTML，自动探测编码。

默认用 requests 直接抓取；对启用反爬（如 HTTP 412 JS 挑战）的站点，
可用 use_browser=True 走 Playwright 真实浏览器渲染，跨请求复用同一浏览器实例。
"""
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def force_ipv4(enabled=None):
    """把 requests/urllib3 的解析地址族限制为 IPv4（幂等），返回是否启用。

    为什么需要：校园网大量双栈站点同时有 A 与 AAAA 记录，而本机/服务器**没有
    IPv6 出口路由**时，urllib3 会先试 AAAA 记录、直接 `[Errno 101] Network is
    unreachable`，整站被判不可达（大连海事 5 项、北京理工大学 grd/jwb 的"秒失败
    误报"就是这么来的——v4 实测均 200）。限制为 IPv4 后按 A 记录正常连。

    只改 requests/urllib3 这条链；Playwright 浏览器官道不受影响（浏览器自己处理
    happy-eyeballs）。默认开启，环境变量 `UNIV_FORCE_IPV4=0` 可关闭（出口确实只有
    IPv6 的机器）。
    """
    from urllib3.util import connection as _conn

    if enabled is None:
        enabled = os.environ.get("UNIV_FORCE_IPV4", "1").strip().lower() not in (
            "0", "false", "no", "off")
    if enabled:
        _conn.allowed_gai_family = lambda: socket.AF_INET
    return enabled


force_ipv4()

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


# 出网目标边界（与 scripts/find_list_url.py 同一套口径，见其 safe_url/guarded_get）：
# 只允许公网 http/https，拒绝 localhost/内网主机名，以及 DNS 解析出的任何非
# global 地址（环回、私有、链路本地、保留）。生产路径过去没有这道校验，而附件
# 与 iframe 的目标来自页面内容——页面被改或被投毒就会把采集器指向内网。
_BAD_HOST_SUFFIX = (".local", ".internal", ".localhost", ".home.arpa")


def target_problem(url):
    """目标不可访问的原因（可访问返回 ""）；顺带区分 DNS 失败与非公网地址。"""
    import ipaddress
    import socket

    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        return f"仅允许 http/https（当前 {p.scheme or '无协议'}）"
    host = (p.hostname or "").lower()
    if not host:
        return "缺少主机名"
    if host == "localhost" or host.endswith(_BAD_HOST_SUFFIX):
        return f"拒绝内网主机名 {host}"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return f"DNS 解析失败 {host}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return f"无法解析地址 {info[4][0]}"
        if not ip.is_global:
            return f"拒绝非公网地址 {ip}"
    return ""


def ensure_public_target(url):
    """取数前的目标校验：通过则原样返回 URL，否则抛 ValueError。

    返回 URL（而不是布尔）是为了让校验紧贴调用点，后续改动绕不开它。
    """
    problem = target_problem(url)
    if problem:
        raise ValueError(f"{problem}: {url}")
    return url


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
    # 不宣告 br（brotli）：环境未装 brotli/brotlicffi 时 requests 解不开，
    # 站点返回 br 会让整页变成二进制乱码 → 解析 0 条且不报错（西南交大
    # 招生网就因此长期零通知）。装上 brotli 后可再把 br 加回来。
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# Playwright 驱动与浏览器实例。
# 无头浏览器（browser: true）与真实 Chrome 上下文（real_browser: true）共用
# 同一个驱动：同步 API 会在启动线程里留下一个运行中的事件循环，同进程内再建
# 第二个 sync_playwright() 实例必然报 "Playwright Sync API inside the asyncio
# loop"，即后启动的那一类栏目全部失败。共用一个驱动可规避。
_playwright = None
_browser = None

# 单个页面浏览器操作的整体超时（秒）。站点 JS 死循环（无限循环/持续导航）
# 会让 evaluate/content 永久挂起——playwright 任何超时参数都无法中断
# 渲染进程内的死循环，唯一可靠手段是杀掉 chromium 进程（连接断开后
# 阻塞调用立即抛 TargetClosedError 返回）。
_BROWSER_PAGE_TIMEOUT = 60
# 浏览器上下文统一使用的桌面 Chrome UA。
# Playwright 自带的无头 chromium 默认 UA 带 "HeadlessChrome/…X11; Linux x86_64"，
# 北邮/兰大等站的 WAF 见到就回 39 字节空文档——日志记成"ok 抽到 0 条"，属静默零。
# 固定一个真实桌面 UA（不轮换：同一会话内 UA 跳变同样是反爬特征）。
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# chromium 进程匹配特征（拼装避免 pkill 匹配到调用方自身 cmdline）
_CHROMIUM_MATCH = "chrome-headless" + "-shell-linux64" + "/chrome-headless-shell"
# 本项目持久化 profile 目录名：真实 Chrome 的 cmdline 里带它，强杀时据此
# 只杀本项目自己的浏览器，不误伤同机其它 Chrome
_PROFILE_NAME = ".university_info_profile"
# 取到的内容像"挑战页/拦截页"时的特征（需刷新或换通道重取）。
# 只放硬特征：很多正常页面都有 <noscript>Please enable JavaScript</noscript>，
# 把这类提示语当拦截信号会把好页面误判成被拦。
_CHALLENGE_HINTS = ("$_ts", "$_ss", "dynamic_challenge", "访问被限制",
                    "浏览器环境不被允许")
# 句柄失效类异常的特征串（浏览器崩溃/被强杀后必须重建，不能原样抛给站点层）
_GONE_HINTS = ("has been closed", "targetclosed", "browser has been closed",
               "connection closed", "epipe", "browser closed",
               "target page, context or browser")


def _looks_like_challenge(html):
    """内容是否像反爬挑战页/拦截页（体量过小或含挑战特征）。"""
    if len(html or "") < 3000:
        return True
    low = (html or "").lower()
    return any(h.lower() in low for h in _CHALLENGE_HINTS)


def _is_block_page(html):
    """内容是否已被拦死（刷新也救不回：空文档或明确的拦截/挑战特征）。

    只用来决定"要不要报错"——报错才会在日志里留下痕迹；否则一次 WAF 拦截
    会被记成"ok 抽到 0 条"，静默零（北邮/兰大/政法都这么丢过数据）。
    """
    text = html or ""
    if len(text) < 500:
        return True
    low = text.lower()
    return any(h.lower() in low for h in _CHALLENGE_HINTS)


def _is_browser_gone(err):
    """异常是否表示浏览器/上下文句柄失效（而非站点本身的问题）。"""
    text = f"{type(err).__name__}: {err}".lower()
    return any(h in text for h in _GONE_HINTS)


class _BrowserTimeout(Exception):
    """浏览器单页操作超时（站点 JS 卡死，浏览器已强杀重建）。"""


def _kill_browser_processes():
    """强杀本项目的 playwright 浏览器进程（含卡死的渲染进程）。

    Linux：按 bundled headless shell 路径与本项目 profile 名匹到进程再杀；
    Windows：只能用 PowerShell 按可执行文件路径筛 ms-playwright——绝不按
    进程名杀，那会把用户自己的 Chrome 一起杀掉（本机实测 pkill 不存在，
    原实现在 Windows 上直接抛 FileNotFoundError，看门狗等于没生效）。
    """
    import subprocess

    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or "
              "Name='chrome-headless-shell.exe'\" | Where-Object "
              "{ $_.ExecutablePath -like '*ms-playwright*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
              "-ErrorAction SilentlyContinue }")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           check=False, timeout=30, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001  看门狗线程里绝不能抛异常
            pass
        return
    for pattern in (_CHROMIUM_MATCH, _PROFILE_NAME):
        try:
            subprocess.run(["pkill", "-9", "-f", pattern], check=False,
                           timeout=30, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass


def _invalidate_browser_handles():
    """浏览器进程被强杀后把全局句柄一律作废，下次调用自动重建。

    这是"一个栏目卡死 → 同进程后续所有浏览器栏目报 closed"的根因：原实现
    只用 pkill 杀进程，句柄仍是旧对象，_get_browser()/_get_real_context()
    见非 None 就直接复用，于是接连报 Target page/context closed。
    """
    global _browser, _real_ctx, _real_page
    _browser = None
    _real_ctx = None
    _real_page = None


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
            _invalidate_browser_handles()

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


def _get_playwright():
    """全局唯一的 playwright 驱动（无头与真实浏览器共用）。"""
    global _playwright
    if _playwright is None:
        from playwright.sync_api import sync_playwright

        _playwright = sync_playwright().start()
    return _playwright


def _clear_zombie_loop():
    """清理「僵尸事件循环」：驱动的 node 进程崩溃后（站点 JS 卡死被强杀时
    实测会出现 EPIPE 崩溃），启动线程里仍残留一个标记为 running 的循环，
    它会让后续 sync_playwright() 永远报 asyncio loop 错。playwright 判断
    "是否在事件循环里"只看循环的归属线程标记，这里做最后兜底清掉。"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        loop._thread_id = None  # noqa: SLF001  仅清归属标记，循环对象已不可用
    except Exception:  # noqa: BLE001
        pass


def _restart_playwright():
    """重建驱动：浏览器被强杀后旧连接可能已损坏，先停旧驱动再起新的；
    旧驱动进程已崩溃时（正常停止无效）再兜底清理僵尸循环。"""
    global _playwright
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        _playwright = None
    try:
        return _get_playwright()
    except Exception:  # noqa: BLE001
        _clear_zombie_loop()
        return _get_playwright()


def _get_browser():
    global _browser
    if _browser is not None and not _browser.is_connected():
        _browser = None          # 进程已不在（被看门狗杀过或自身崩溃）
    if _browser is None:
        try:
            _browser = _get_playwright().chromium.launch(headless=True)
        except Exception:  # noqa: BLE001
            _browser = _restart_playwright().chromium.launch(headless=True)
    return _browser


# 真实 Chrome 模式（瑞数反爬用）：持久化上下文，跨请求复用
_real_ctx = None
_real_page = None
_xvfb_display = None


def _ensure_xvfb():
    """无桌面环境（服务器）上准备一个虚拟显示，返回 DISPLAY 值或 None。

    服务器没有系统 Chrome 时，回退的无头 chromium 会被瑞数类 WAF 直接拒绝
    （北邮 400/39 字节），而 xvfb 下的**有头** chromium 实测能过（政法信息
    公开 4774227 字节/parse 10、北邮研招 63687 字节/parse 10）。这里自建
    Xvfb 而不是靠 xvfb-run 包一层：浏览器是在本进程内启动的，包不了。
    """
    global _xvfb_display
    if _xvfb_display:
        return _xvfb_display
    if os.name == "nt" or os.environ.get("DISPLAY"):
        return None                     # 本机/已有显示：直接用
    if not Path("/usr/bin/Xvfb").exists():
        return None
    if not Path("/tmp/.X99-lock").exists():
        import subprocess
        try:
            subprocess.Popen(["/usr/bin/Xvfb", ":99", "-screen", "0",
                              "1366x900x24", "-nolisten", "tcp"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(1.5)
        except Exception:  # noqa: BLE001
            return None
    if not Path("/tmp/.X99-lock").exists():
        return None
    _xvfb_display = ":99"
    os.environ["DISPLAY"] = _xvfb_display
    return _xvfb_display


def _launch_real_context(pw):
    """创建真实浏览器持久化上下文（本机 Chrome 优先，服务器回退自带 chromium）。

    本机有 Chrome/Edge 时用有头持久化上下文（可过瑞数等强反爬，窗口置于
    屏幕外）；服务器/无桌面环境没有本机 Chrome 时，优先在 Xvfb 虚拟显示上
    用**有头** chromium（实测能过瑞数），实在没有 Xvfb 才退无头。

    两种回退都必须显式带桌面 UA：Playwright 默认 UA 是 HeadlessChrome，
    北邮/兰大这类站的 WAF 见到直接回 39 字节空文档。
    """
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
    profile = Path.home() / _PROFILE_NAME
    profile.mkdir(parents=True, exist_ok=True)
    common = {
        "args": ["--disable-blink-features=AutomationControlled",
                 "--no-first-run", "--no-default-browser-check"],
        "ignore_default_args": ["--enable-automation"],
        "viewport": {"width": 1366, "height": 900},
        "user_agent": _BROWSER_UA,      # 见 _BROWSER_UA：去掉 HeadlessChrome 指纹
    }
    if exe is None:
        # 服务器兜底：优先 Xvfb 虚拟显示 + 有头 chromium（能过瑞数），
        # 拿不到虚拟显示才退无头（会被部分 WAF 拒绝，至少不再伪装成"0 条"）
        if _ensure_xvfb():
            return pw.chromium.launch_persistent_context(
                str(profile), headless=False,
                args=common["args"] + ["--window-position=-32000,-32000"],
                ignore_default_args=common["ignore_default_args"],
                viewport=common["viewport"], user_agent=common["user_agent"],
            )
        return pw.chromium.launch_persistent_context(
            str(profile), headless=True,
            args=common["args"],
            ignore_default_args=common["ignore_default_args"],
            viewport=common["viewport"], user_agent=common["user_agent"],
        )
    return pw.chromium.launch_persistent_context(
        str(profile), executable_path=exe, headless=False,
        args=common["args"] + [
            # 窗口定位到屏幕外：不遮挡用户桌面、不抢前台焦点。
            # 不能用最小化——最小化会让页面进入后台可见性状态，
            # 反而触发部分 WAF（如瑞数）的检测。
            "--window-position=-32000,-32000"],
        ignore_default_args=common["ignore_default_args"],
        viewport=common["viewport"], user_agent=common["user_agent"],
    )


def _get_real_context():
    """获取真实浏览器持久化上下文（与无头浏览器共用同一驱动）。

    取用前先探活：句柄指向的上下文已关闭（被强杀/崩溃）时直接作废重建，
    而不是把它原样交出去、让之后每个栏目都报 context closed。
    """
    global _real_ctx
    if _real_ctx is not None:
        try:
            _real_ctx.pages            # 上下文失效时这里就会抛
        except Exception:  # noqa: BLE001
            _real_ctx = None
    if _real_ctx is None:
        try:
            _real_ctx = _launch_real_context(_get_playwright())
        except Exception:  # noqa: BLE001
            _real_ctx = _launch_real_context(_restart_playwright())
        # 持久化上下文已存在的页面也启用资源拦截
        try:
            for _p in _real_ctx.pages:
                _block_heavy_resources(_p)
        except Exception:  # noqa: BLE001
            pass
    return _real_ctx


def close_real_browser():
    """关闭真实 Chrome 持久化上下文（驱动保留，供后续复用）。"""
    global _real_ctx, _real_page
    if _real_ctx is not None:
        try:
            _real_ctx.close()
        except Exception:  # noqa: BLE001
            pass
        _real_ctx = None
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
    """用真实浏览器（有头 + 持久化 cookie）访问，可过瑞数 JS 挑战。

    复用同一个长生命周期页面：不 close 页面，否则持久化上下文会随
    最后一个页面关闭而整体关闭（后续 new_page 报 context closed）。
    同样受超时护栏保护，卡死时强杀 chromium 并重建上下文。
    句柄失效（上次强杀/崩溃留下）时作废重建并重试一次，而不是把它抛给
    站点层——否则一个栏目出问题会连坐同进程之后所有浏览器栏目。
    """
    for attempt in (1, 2):
        try:
            return _browser_call_with_timeout(_http_get_real_inner, url, wait_ms)
        except _BrowserTimeout:
            close_real_browser()
            raise RuntimeError(
                f"真实浏览器渲染超时（>{_BROWSER_PAGE_TIMEOUT}s，已重建）: {url}")
        except Exception as e:  # noqa: BLE001
            if not _is_browser_gone(e) or attempt == 2:
                raise
            close_real_browser()
            _invalidate_browser_handles()


def _http_get_real_inner(url, wait_ms=5000):
    global _real_page
    ctx = _get_real_context()
    if _real_page is None or _real_page.is_closed():
        _real_page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        _real_page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _smart_wait(_real_page, fallback_ms=wait_ms)
        # 反爬挑战：首访后刷新一次再取（阈值放宽到"像挑战页就刷新"——
        # 政法那次 1245 字节的挑战页因为原阈值 1000 被漏过，记成了静默 0）
        html = _get_real_content(_real_page)
        if _looks_like_challenge(html):
            _real_page.reload(wait_until="domcontentloaded")
            _smart_wait(_real_page, fallback_ms=wait_ms)
            html = _get_real_content(_real_page)
        if _is_block_page(html):
            raise RuntimeError(
                f"疑似被反爬拦截（{len(html)} 字节，含挑战/拦截特征）: {url}")
        _real_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _real_page.wait_for_timeout(600)
        _real_page.evaluate("window.scrollTo(0, 0)")
        return _get_real_content(_real_page)
    except Exception:  # noqa: BLE001
        try:
            return _get_real_content(_real_page, attempts=2, wait_ms=2000)
        except Exception:  # noqa: BLE001
            raise


def close_browser():
    """关闭无头浏览器实例（驱动保留，供真实浏览器/后续重建复用）。"""
    global _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:  # noqa: BLE001
            pass
        _browser = None


def close_playwright():
    """彻底关闭 playwright 驱动（进程收尾时调用）。"""
    global _playwright
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        _playwright = None


# 页面加载时跳过的资源类型：图片/字体/媒体对正文抽取无用，拦截可显著提速
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def _block_heavy_resources(page):
    """拦截图片/字体/媒体资源（显著减少页面加载时间）。"""
    def _route(route):
        try:
            if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                route.abort()
            else:
                route.continue_()
        except Exception:  # noqa: BLE001
            try:
                route.continue_()
            except Exception:  # noqa: BLE001
                pass

    try:
        page.route("**/*", _route)
    except Exception:  # noqa: BLE001
        pass


def _smart_wait(page, fallback_ms=5000, idle_ms=3500):
    """智能等待：先等网络空闲（动态列表渲染完成的可靠信号）；

    空闲超时则退化为较短固定等待。替代"无论页面快慢都等固定 5 秒"的做法。
    """
    try:
        page.wait_for_load_state("networkidle", timeout=idle_ms)
    except Exception:  # noqa: BLE001
        try:
            page.wait_for_timeout(min(fallback_ms, 2000))
        except Exception:  # noqa: BLE001
            pass


def _http_get_browser(url, wait_ms=4500):
    """用真实浏览器渲染页面（可过 JS 反爬挑战 / 等待 JS 异步加载 / 懒加载图）。

    滚动到底部再回顶，触发懒加载图片加载；随后返回渲染后的 HTML 文本。
    受 watchdog 超时保护：站点 JS 卡死时强杀 chromium 并抛异常，由上层
    降级逻辑继续下一站点，避免单个站点拖垮整个采集。句柄失效时作废重建
    并重试一次（同 _http_get_real，避免一个栏目连坐后面所有浏览器栏目）。
    """
    for attempt in (1, 2):
        try:
            return _browser_call_with_timeout(_http_get_browser_inner, url,
                                              wait_ms)
        except _BrowserTimeout:
            # 浏览器已损坏（chromium 被强杀）：丢句柄、保留驱动，
            # 下次 _get_browser() 自动重建（驱动若也坏了会自动重启）
            _invalidate_browser_handles()
            raise RuntimeError(
                f"浏览器渲染超时（>{_BROWSER_PAGE_TIMEOUT}s，chromium 已重建）: {url}")
        except Exception as e:  # noqa: BLE001
            if not _is_browser_gone(e) or attempt == 2:
                raise
            _invalidate_browser_handles()   # 句柄失效：重建后重试一次


def _http_get_browser_inner(url, wait_ms=4500):
    browser = _get_browser()
    page = browser.new_page(user_agent=_BROWSER_UA)   # 见 _BROWSER_UA
    _block_heavy_resources(page)
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # 智能等待：多数页面 networkidle 后即可取内容，避免固定 4.5s 空等
        _smart_wait(page, fallback_ms=wait_ms)
        # 滚动触发懒加载（图片已拦截，仅用于触发列表 DOM 渲染）
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        html = page.content()
        if _is_block_page(html):
            raise RuntimeError(
                f"疑似被反爬拦截（{len(html)} 字节，含挑战/拦截特征）: {url}")
        return html
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001
        try:
            return page.content()
        except Exception:  # noqa: BLE001
            raise
    finally:
        page.close()


def _alt_scheme_url(url):
    """返回同主机同路径的另一种协议 URL（http↔https）；无法构造时返回 ""。"""
    p = urlparse(url)
    if p.scheme == "https":
        alt = "http"
    elif p.scheme == "http":
        alt = "https"
    else:
        return ""
    if not p.netloc:
        return ""
    return p._replace(scheme=alt).geturl()


def _requests_get_text(url, timeout, encoding):
    """requests 抓取并解码（单次，不重试）。返回 (text, final_scheme)。"""
    resp = requests.get(
        url, headers=_browser_headers(url), timeout=timeout, verify=True
    )
    resp.raise_for_status()
    if encoding:
        resp.encoding = encoding
    else:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text, urlparse(resp.url or url).scheme


def http_get(url, timeout=20, retries=2, encoding=None, use_browser=False,
             use_real_browser=False):
    """下载页面文本。

    use_real_browser=True 时用真实 Chrome（有头+持久化 cookie）抓取，可过瑞数反爬；
    use_browser=True 时走 Playwright 无头渲染；否则 requests 直接抓取。

    三种通道都先过 ensure_public_target()：附件/iframe 的目标由页面内容决定，
    不校验就等于把内网地址交给采集器（与 scripts/find_list_url.py 同一套口径）。

    requests 通道失败时做一次**协议回退**：同主机换 http↔https 再试。华中科技大学
    就是"443 挂起、80 正常"，改协议即恢复；但武大、对外经贸 yjsy 那类 http 是
    301 跳回 https 的站点探不进——故回退后校验最终落点协议仍是目标协议（被跳回
    原协议即视为没救，不误判成功），只对真能出内容的换协议才采纳。
    """
    ensure_public_target(url)
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
            text, _ = _requests_get_text(url, timeout, encoding)
            return text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))

    # 原协议彻底失败 → 试另一种协议（先探测再回退，落点被跳回原协议则不算数）
    alt = _alt_scheme_url(url)
    if alt:
        try:
            text, final_scheme = _requests_get_text(alt, timeout, encoding)
            if final_scheme == urlparse(alt).scheme and len(text or "") >= 500:
                return text
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"抓取失败 {url}: {last_err}")

def http_get_bytes(url, timeout=30, retries=2):
    """下载二进制内容（PDF/附件等），返回 bytes。"""
    ensure_public_target(url)
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

