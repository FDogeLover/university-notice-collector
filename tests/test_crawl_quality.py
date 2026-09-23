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
    """替换 parse.parse_detail：payload 即返回的 content_md。

    发布时间默认是页面自己写明的（label）——这档才压过列表行日期。
    """

    def fake_parse_detail(html, url):
        return {"title": "t", "published_at": "2026-09-01",
                "published_src": "label", "content_md": html}

    monkeypatch.setattr(run_mod.parse, "parse_detail", fake_parse_detail)


def test_published_detail_label_beats_list_date(fake_fetch, fake_parse):
    """详情页写明发布时间时用它，列表行日期只作补充。"""
    fake_fetch.script = [("html", "详情页正文" * 20)]
    _, published = run_mod._fetch_detail_content(
        "https://gs.x.edu.cn/e.htm", use_browser=False,
        list_date="2026-08-20")
    assert published == "2026-09-01"


def test_published_list_date_beats_detail_scan(monkeypatch, fake_fetch):
    """详情页只能靠扫正文猜日期时，站点标在列表行上的日期更可信。"""
    monkeypatch.setattr(
        run_mod.parse, "parse_detail",
        lambda html, url: {"title": "t", "published_at": "2026-09-01",
                           "published_src": "scan", "content_md": html})
    fake_fetch.script = [("html", "详情页正文" * 20)]
    _, published = run_mod._fetch_detail_content(
        "https://gs.x.edu.cn/f.htm", use_browser=False,
        list_date="2026-08-20")
    assert published == "2026-08-20"


def test_published_blank_when_nothing_reliable(monkeypatch, fake_fetch):
    """两处都没有发布时间 → 留空，不硬填一个正文里扫到的日期。"""
    monkeypatch.setattr(
        run_mod.parse, "parse_detail",
        lambda html, url: {"title": "t", "published_at": "",
                           "published_src": "", "content_md": html})
    fake_fetch.script = [("html", "详情页正文" * 20)]
    _, published = run_mod._fetch_detail_content(
        "https://gs.x.edu.cn/g.htm", use_browser=False)
    assert published == ""


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


def test_public_target_guard_matches_diagnostic_tool():
    """生产采集路径与诊断脚本的出网口径必须一致（审计 P2-14）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from find_list_url import safe_url as diag_safe

    cases = [
        "https://gs.njust.edu.cn/",          # 公网：两边都放行
        "http://127.0.0.1:8000/admin",       # 环回
        "http://10.1.2.3/x",                 # 私有
        "https://localhost/a",               # 内网主机名
        "http://169.254.169.254/latest",     # 云元数据（链路本地）
        "ftp://example.com/x",               # 非 http/https
        "http://no-such-host-zzz.example/",  # DNS 失败
    ]
    for url in cases:
        mine = not run_mod.fetch.target_problem(url)
        assert mine == diag_safe(url), f"口径不一致: {url}"


def test_public_target_guard_blocks_private_targets():
    """附件/iframe 的目标来自页面内容，采集前必须挡住内网地址。"""
    for url in ("http://127.0.0.1:8000/x", "http://10.1.2.3/x",
                "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(ValueError):
            run_mod.fetch.http_get(url, retries=0)


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


# ---------- 附件 URL 识别 ----------
def test_is_file_url():
    from crawler.parse import is_file_url

    assert is_file_url("https://admission.pku.edu.cn/docs/20260831.pdf")
    assert is_file_url("https://gs.x.edu.cn/upload/a.doc")
    assert is_file_url("https://gs.x.edu.cn/upload/a.docx?ver=1")
    assert is_file_url("https://gs.x.edu.cn/files/表格.xlsx")
    assert not is_file_url("https://gs.x.edu.cn/info/1002/3520.htm")
    assert not is_file_url("https://gs.x.edu.cn/notice/list.jsp?id=3")


def test_detail_skips_file_url(fake_fetch, fake_parse):
    """附件 URL 不触发下载，直接返回空正文（防二进制乱码）。"""
    fake_fetch.script = [("html", "不该被请求" * 10)]
    content, published = run_mod._fetch_detail_content(
        "https://admission.pku.edu.cn/docs/a.pdf", use_browser=False)
    assert content is None and published == ""
    assert fake_fetch.script  # 未被消费，说明没发请求


def test_published_ignores_insane_year():
    """解析错误的未来年份（如 2052）不应作为发布时间入库。"""
    html = """
    <html><body><div id="content">
    <p>发布时间：2052年9月1日</p>
    <p>正文第一段：根据上级要求，现将有关安排通知如下。</p>
    <p>正文第二段：请各单位按时完成材料报送工作，逾期不再受理。</p>
    </div></body></html>
    """
    d = parse.parse_detail(html, "https://gs.x.edu.cn/p/3.htm")
    assert d["published_at"] != "2052-09-01"
    assert d["published_at"] == ""

    # 合理年份仍正常提取
    html2 = html.replace("2052年", "2026年")
    d2 = parse.parse_detail(html2, "https://gs.x.edu.cn/p/4.htm")
    assert d2["published_at"] == "2026-09-01"


# ---------- 出口不可达标记（unreachable）----------
def _crawl_args(**kw):
    args = type("A", (), {})()
    args.max_items = 20
    args.no_detail = True
    args.sleep = 0
    args.workers = 1
    for k, v in kw.items():
        setattr(args, k, v)
    return args


@pytest.fixture()
def crawl_db(tmp_path, monkeypatch):
    """临时库：一校一栏目，URL 可由用例指定。"""
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "crawl.db"))
    from db import store

    store.init_db()
    conn = store.connect()
    return store, conn


def test_unreachable_source_logs_skipped_not_error(crawl_db, monkeypatch):
    """打了 unreachable 的栏目抓取失败时记 skipped，不计入错误率。"""
    store, conn = crawl_db
    url = "https://uc.unreachable.edu.cn/"
    store.import_schools(conn, [{
        "name": "测试大学", "domain": "unreachable.edu.cn",
        "sources": [{"name": "本科生院", "url": url, "category": "通知公告",
                     "unreachable": True}],
    }])
    sid = store.school_id_by_name(conn, "测试大学")

    def boom(*a, **k):
        raise RuntimeError("Connection aborted")

    monkeypatch.setattr(run_mod.fetch, "http_get", boom)
    run_mod.RUN_STATS.update(sources=0, errors=0, zero_output=0, new=0,
                             skipped=0)
    school = {"name": "测试大学", "domain": "unreachable.edu.cn"}
    source = {"name": "本科生院", "url": url, "unreachable": True}
    n = run_mod.crawl_source(conn, school, sid, source, _crawl_args())

    assert n == 0
    assert run_mod.RUN_STATS["errors"] == 0   # 不计失败
    assert run_mod.RUN_STATS["skipped"] == 1  # 记一次跳过
    row = conn.execute(
        "SELECT status, message FROM fetch_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "skipped"
    assert "出口不可达" in row["message"]
    conn.close()


def test_unmarked_source_still_counts_error(crawl_db, monkeypatch):
    """未打标的栏目失败仍记 error、计入错误率（行为不回退）。"""
    store, conn = crawl_db
    url = "https://gs.normal.edu.cn/"
    store.import_schools(conn, [{
        "name": "测试大学", "domain": "normal.edu.cn",
        "sources": [{"name": "研究生院", "url": url, "category": "通知公告"}],
    }])
    sid = store.school_id_by_name(conn, "测试大学")

    def boom(*a, **k):
        raise RuntimeError("ConnectTimeout")

    monkeypatch.setattr(run_mod.fetch, "http_get", boom)
    run_mod.RUN_STATS.update(sources=0, errors=0, zero_output=0, new=0,
                             skipped=0)
    school = {"name": "测试大学", "domain": "normal.edu.cn"}
    source = {"name": "研究生院", "url": url}
    n = run_mod.crawl_source(conn, school, sid, source, _crawl_args())

    assert n == 0
    assert run_mod.RUN_STATS["errors"] == 1
    assert run_mod.RUN_STATS["skipped"] == 0
    row = conn.execute(
        "SELECT status FROM fetch_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "error"
    conn.close()


# ---------- fetch 层强制 IPv4 / 协议回退 ----------
def test_force_ipv4_limits_address_family():
    """强制 IPv4：消除双栈优先 v6 + 无 v6 路由的 [Errno 101] 误报。"""
    import socket

    from crawler import fetch
    from urllib3.util import connection as conn_util

    fetch.force_ipv4(True)
    assert conn_util.allowed_gai_family() == socket.AF_INET

    # 可关闭（出口确实只有 v6 的机器）
    assert fetch.force_ipv4(False) is False


def test_alt_scheme_url():
    """协议回退 URL：同主机同路径换 http↔https。"""
    from crawler.fetch import _alt_scheme_url

    assert _alt_scheme_url("https://gs.x.edu.cn/a.htm") == "http://gs.x.edu.cn/a.htm"
    assert _alt_scheme_url("http://gs.x.edu.cn/a.htm") == "https://gs.x.edu.cn/a.htm"
    assert _alt_scheme_url("ftp://gs.x.edu.cn/a.htm") == ""


def test_http_get_falls_back_to_alt_scheme(monkeypatch):
    """https 拿不到时回退 http，且只在落点仍是 http 时采纳。"""
    from crawler import fetch

    calls = []

    class _Resp:
        def __init__(self, text, url):
            self.text = text
            self.url = url
            self.encoding = "utf-8"
            self.apparent_encoding = "utf-8"

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        calls.append(url)
        if url.startswith("https://"):
            raise ConnectionError("443 挂起")
        return _Resp("<html>" + "x" * 900 + "</html>", "http://gs.x.edu.cn/")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch, "ensure_public_target", lambda u: u)
    monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)
    out = fetch.http_get("https://gs.x.edu.cn/", retries=0)
    assert "x" * 100 in out
    assert calls[-1].startswith("http://")


def test_http_get_rejects_scheme_bounce_back(monkeypatch):
    """回退后又被跳回原协议（http→301→https）视为没救，不误判成功。"""
    from crawler import fetch

    class _Resp:
        def __init__(self, text, url):
            self.text = text
            self.url = url
            self.encoding = "utf-8"
            self.apparent_encoding = "utf-8"

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        if url.startswith("https://"):
            raise ConnectionError("443 挂起")
        # http 请求最终落到 https：相当于 301 跳回
        return _Resp("<html>" + "y" * 900 + "</html>", "https://gs.x.edu.cn/")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch, "ensure_public_target", lambda u: u)
    monkeypatch.setattr(fetch.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError):
        fetch.http_get("https://gs.x.edu.cn/", retries=0)


# ---------- find_list_url TCP 预检 ----------
def test_tcp_reachable_skips_unreachable_host(monkeypatch):
    """80/443 都连不上的主机：跳过浏览器渲染（避免 280s 拖死）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import socket as _socket

    import find_list_url as fl

    monkeypatch.setattr(fl, "safe_url", lambda u: True)

    # 解析得出 v4 地址，但 connect 一律失败（模拟 SYN 不通）
    monkeypatch.setattr(
        fl.socket, "getaddrinfo",
        lambda *a, **k: [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "",
                          ("203.0.113.9", 443))])

    class _DeadSock:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, *a):
            pass

        def connect(self, *a):
            raise OSError("Connection timed out")

        def close(self):
            pass

    monkeypatch.setattr(fl.socket, "socket", _DeadSock)
    assert fl._tcp_reachable("https://dead.example.edu.cn/") is False

    called = {"render": False}

    def fake_http_get(*a, **k):
        called["render"] = True
        return "<html></html>"

    monkeypatch.setattr(fl.fetch, "http_get", fake_http_get)
    assert fl._render("https://dead.example.edu.cn/") == ""
    assert called["render"] is False   # 预检失败就不该起浏览器
