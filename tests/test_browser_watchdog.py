# -*- coding: utf-8 -*-
"""看门狗击杀名单与句柄作废的回归测试（2026-09-26 driver 冻结修复）。

测试不发真实 pkill（subprocess.run 被捕获断言），避免误杀并发 cron 的
真实浏览器；击杀对真实进程的效力由服务器上的一次性人工验证覆盖（部署日
用假进程验过：chrome 特征与驱动特征的诱饵进程均被击杀）。
"""
import threading
import time

import pytest

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

from crawler import fetch


def test_kill_browser_processes_covers_chromium_and_driver(monkeypatch):
    """击杀名单必须同时含 chromium、playwright 驱动与项目 profile。"""
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(fetch.os, "name", "posix")  # 两平台统一走 Linux 分支
    fetch._kill_browser_processes()
    patterns = [cmd[3] for cmd in calls if cmd[0] == "pkill"]
    assert fetch._CHROMIUM_MATCH in patterns
    assert fetch._DRIVER_MATCH in patterns
    assert fetch._PROFILE_NAME in patterns


def test_invalidate_browser_handles_resets_playwright():
    """句柄作废必须连 _playwright（驱动）一起——否则下次 launch 会在
    卡死的驱动管道上永久阻塞（9/25 冻结根因）。"""
    fetch._playwright = object()
    fetch._browser = object()
    fetch._real_ctx = object()
    fetch._real_page = object()
    try:
        fetch._invalidate_browser_handles()
        assert fetch._playwright is None
        assert fetch._browser is None
        assert fetch._real_ctx is None
        assert fetch._real_page is None
    finally:
        fetch._playwright = None
        fetch._browser = None
        fetch._real_ctx = None
        fetch._real_page = None


def test_timeout_unblocks_blocked_call_and_raises(monkeypatch):
    """回归主场景：fn 阻塞在「驱动管道」上，超时击杀后 fn 收到连接断开
    抛异常，_browser_call_with_timeout 及时以 _BrowserTimeout 返回。"""
    unblock = threading.Event()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        unblock.set()  # 模拟驱动被杀 → 阻塞调用收到连接断开

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(fetch.os, "name", "posix")
    monkeypatch.setattr(fetch, "_BROWSER_PAGE_TIMEOUT", 0.2)

    def blocked_fn():
        # 真实场景等价物：同步调用阻塞在驱动管道上，被杀后抛连接类异常
        assert unblock.wait(5), "watchdog 未在窗口内击杀（fn 持续阻塞）"
        raise ConnectionError("connection closed")

    t0 = time.monotonic()
    with pytest.raises(fetch._BrowserTimeout):
        fetch._browser_call_with_timeout(blocked_fn)
    assert time.monotonic() - t0 < 2  # 及时返回，没等满 fn 的 5 秒
    assert any(cmd and cmd[0] == "pkill" for cmd in calls)
