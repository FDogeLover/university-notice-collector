# -*- coding: utf-8 -*-
"""run.py 详情抓取升级链 与 parse.py 采集质量改进的测试。"""
import pytest

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

import run as run_mod
from crawler import parse


@pytest.fixture()
def fake_fetch(monkeypatch):
    """替换 run 模块使用的 fetch.http_get，按序列返回预置结果。

    fake.script = [ ("html", "<html>.."), ("raise", RuntimeError("x")), ... ]
    每次调用按顺序消费一项（耗尽后重复最后一项）。
    """
    fake = type("F", (), {"script": []})()

    def fake_http_get(url, timeout=20, retries=2, encoding=None,
                      use_browser=False, use_real_browser=False):
        mode = fake.script.pop(0) if len(fake.script) > 1 else fake.script[0]
        kind, payload = mode
        if kind == "raise":
            raise payload
        return payload

    monkeypatch.setattr(run_mod.fetch, "http_get", fake_http_get)
    return fake


@pytest.fixture()
def fake_parse(monkeypatch):
    """替换 parse.parse_detail：payload 即返回的 content_md。"""

    def fake_parse_detail(html, url):
        return {"title": "t", "published_at": "2026-09-01",
                "content_md": html}

    monkeypatch.setattr(run_mod.parse, "parse_detail", fake_parse_detail)


def test_detail_escalates_on_exception(fake_fetch, fake_parse):
    """首级抓取抛异常时继续降级，而不是放弃。"""
    fake_fetch.script = [
        ("raise", RuntimeError("412 Precondition Failed")),
        ("html", "无头渲染拿到的完整正文" * 10),
    ]
    content, published = run_mod._fetch_detail_content("https://gs.x.edu.cn/a.htm",
                                                       use_browser=False)
    assert content.startswith("无头渲染")
    assert published == "2026-09-01"


def test_detail_prefirst_success_no_fallback(fake_fetch, fake_parse):
    """首级成功且正文充足时不做多余请求。"""
    fake_fetch.script = [("html", "requests 直接拿到的正文" * 10)]
    content, _ = run_mod._fetch_detail_content("https://gs.x.edu.cn/b.htm",
                                               use_browser=False)
    assert content.startswith("requests")
    assert len(fake_fetch.script) == 1  # 未发起后续请求


def test_detail_all_fail_returns_best(fake_fetch, fake_parse):
    """全部失败时返回拿到的最长兜底正文；全挂则返回 None。"""
    fake_fetch.script = [
        ("raise", RuntimeError("fail")),
        ("html", "短文"),
        ("raise", RuntimeError("fail")),
    ]
    content, published = run_mod._fetch_detail_content("https://gs.x.edu.cn/c.htm",
                                                       use_browser=False)
    assert content == "短文" and published == "2026-09-01"

    fake_fetch.script = [("raise", RuntimeError("down"))]
    content, _ = run_mod._fetch_detail_content("https://gs.x.edu.cn/d.htm",
                                               use_browser=False)
    assert content is None


# ---------- parse.py 采集质量 ----------
def test_list_strips_cms_junk_in_titles():
    html = (
        '<a href="a/1.htm">082026.05吉林大学2026级研究生新生入学须知</a>'
        '<a href="a/2.htm">某校2026年推免生接收办法重要2025.11.24</a>'
        '<a href="a/3.htm">2026年硕士研究生招生简章</a>'  # 正常年份标题不受影响
    )
    items = parse.parse_list(html, "https://gs.x.edu.cn/", "x.edu.cn")
    titles = [it["title"] for it in items]
    assert titles[0] == "吉林大学2026级研究生新生入学须知"
    assert titles[1] == "某校2026年推免生接收办法重要"
    assert titles[2] == "2026年硕士研究生招生简章"


def test_detail_published_from_meta_and_label():
    html_meta = """
    <html><head><meta name="PubDate" content="2026-08-15 10:00:00"></head>
    <body><div class="v_news_content"><p>正文内容足够长足够长足够长。</p>
    <p>补充段落，用于让正文通过质量门禁，内容无实际含义。</p>
    <p>再补一段正文内容，保证解析出的快照足够完整稳定。</p></div></body></html>
    """
    d = parse.parse_detail(html_meta, "https://gs.x.edu.cn/p/1.htm")
    assert d["published_at"] == "2026-08-15"

    html_label = """
    <html><body><div id="content">
    <p>发布时间：2026年7月2日</p>
    <p>正文第一段：现将有关事项通知如下，请各单位遵照执行相关安排。</p>
    <p>正文第二段：本通知自发布之日起施行，由研究生院负责解释说明。</p>
    </div></body></html>
    """
    d2 = parse.parse_detail(html_label, "https://gs.x.edu.cn/p/2.htm")
    assert d2["published_at"] == "2026-07-02"
