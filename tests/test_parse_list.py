# -*- coding: utf-8 -*-
"""parse_list 回归测试：真实高校列表页 fixture + 合成 HTML 行为用例。

fixture 由 tests/update_fixtures.py 抓取；若某校网站改版导致解析破坏，
这里会第一时间红灯，而不是等线上采集时才发现。
"""
import pytest

from conftest import load_fixture, load_manifest

from crawler.parse import NAV_TITLES, parse_list, same_domain

MANIFEST = load_manifest()
LIST_FIXTURES = sorted(k for k in MANIFEST if k.startswith("list_"))


@pytest.mark.parametrize("name", LIST_FIXTURES)
def test_list_fixture_parses(name):
    """真实列表页：解析条数达标，且每条都是同域、非索引页的有效通知。"""
    meta = MANIFEST[name]
    html = load_fixture(name)
    items = parse_list(html, meta["url"], meta["domain"], max_items=50)
    assert len(items) >= meta.get("min_items", 1), \
        f"{name} 只解析到 {len(items)} 条，解析器或网站结构可能已变化"
    seen = set()
    for it in items:
        assert len(it["title"]) >= 6
        assert same_domain(it["url"], meta["domain"]), it["url"]
        low = it["url"].lower()
        assert not any(h in low for h in
                       ("index.htm", "index.html", "list.htm", "default.htm")), it["url"]
        assert it["url"] not in seen
        seen.add(it["url"])


def test_same_domain():
    assert same_domain("https://gs.njust.edu.cn/a/1.htm", "njust.edu.cn")
    assert same_domain("https://njust.edu.cn/", "njust.edu.cn")
    assert not same_domain("https://gs.example.com/", "njust.edu.cn")
    # 伪装域名（后缀拼接而非子域）必须拒绝
    assert not same_domain("https://fakenjust.edu.cn/", "njust.edu.cn")


def _min_page(*links):
    return "<html><body>" + "".join(links) + "</body></html>"


def _link(title, href="x/1.htm"):
    return f'<a href="{href}">{title}</a>'


def test_list_skips_nav_and_form_titles():
    html = _min_page(
        _link("通知公告"),                    # 导航名，剔除
        _link("优秀应届毕业生申请表"),          # 表单类且无招生/推免，剔除
        _link("2026年接收推免生报名的通知"),    # 保留
        _link("导师申请表"),                   # 表单类，剔除
    )
    items = parse_list(html, "https://gs.x.edu.cn/", "x.edu.cn")
    assert [it["title"] for it in items] == ["2026年接收推免生报名的通知"]


def test_list_skips_cross_domain_and_index_links():
    html = _min_page(
        _link("2026年推免生招生通知", "https://other-site.edu.cn/a.htm"),
        _link("2026年推免生招生通知", "index.htm"),
        _link("2026年推免生招生通知", "list.htm"),
        _link("2026年推免生招生通知", "ok/1.htm"),      # 唯一保留
        _link("2026年推免生招生通知", "ok/1.htm"),      # 重复 URL 去重
    )
    items = parse_list(html, "https://gs.x.edu.cn/", "x.edu.cn")
    assert [it["url"] for it in items] == ["https://gs.x.edu.cn/ok/1.htm"]


def test_list_max_items_and_order():
    links = _min_page(*[_link(f"2026年推免生招生通知{i}", f"a/{i}.htm")
                        for i in range(10)])
    items = parse_list(links, "https://gs.x.edu.cn/", "x.edu.cn", max_items=3)
    assert len(items) == 3
    assert items[0]["url"].endswith("a/0.htm")


def test_nav_titles_guard():
    """NAV_TITLES 里的常见栏目名不会被当作通知。"""
    assert "研究生院" in NAV_TITLES
