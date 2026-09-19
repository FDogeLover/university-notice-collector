# -*- coding: utf-8 -*-
"""parse_list 回归测试：真实高校列表页 fixture + 合成 HTML 行为用例。

fixture 由 tests/update_fixtures.py 抓取；若某校网站改版导致解析破坏，
这里会第一时间红灯，而不是等线上采集时才发现。
"""
import re
from datetime import date

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
    today = date.today().isoformat()
    for it in items:
        assert len(it["title"]) >= 6
        # 列表行日期若解析出来，必须是真实且已发生的日期
        if it["date"]:
            assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", it["date"]), it["date"]
            assert it["date"] <= today, f"{name} 抽到未来日期 {it['date']}"
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


def test_list_item_publish_dates():
    """列表行发布时间：链接内前缀 / 相邻节点 / 表格行单元格。

    标题正文里的日程日期（"2026年9月20日至10月23日"）不是发布时间，必须剔除：
    详情页往往没有任何发布标记，列表行日期就是站点给出的权威发布时间。
    """
    html = """
    <html><body><ul>
      <li><span class="d">2026-03-18</span>
          <a href="/a/1.htm">教通知【2026】150号-关于公布项目检查结果的通知</a></li>
      <li><a href="/a/2.htm">关于2026年9月20日至2026年10月23日在外住宿申请工作的通知</a>
          <span class="date">2026-04-11</span></li>
      <li><div class="d1">14</div><div class="d2">2026-08</div>
          <div class="t"><a href="/a/3.htm">关于组织推荐2026年博士生专项计划候选人的通知</a></div></li>
      <li><a href="/a/4.htm">关于做好2026年秋季学期研究生工作的通知</a></li>
    </ul></body></html>
    """
    items = parse_list(html, "https://gs.x.edu.cn/", "x.edu.cn")
    assert [it["date"] for it in items] == [
        "2026-03-18", "2026-04-11", "2026-08-14", ""]


def test_list_item_date_yearless_backfills_year(monkeypatch):
    """只有月日的列表日期：按"不晚于今天"回推年份（列表上的日期都已发布过）。"""
    from datetime import date as real_date

    from crawler import parse

    class _FakeDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 19)

    monkeypatch.setattr(parse, "date", _FakeDate)
    assert parse._row_date("09-18 教通知【2026】150号") == "2026-09-18"
    assert parse._row_date("通知 12-30") == "2025-12-30"   # 还没到的月日回推一年
    assert parse._row_date("第3-4周安排") == ""             # 行中间的数字不是日期


def test_list_max_items_and_order():
    links = _min_page(*[_link(f"2026年推免生招生通知{i}", f"a/{i}.htm")
                        for i in range(10)])
    items = parse_list(links, "https://gs.x.edu.cn/", "x.edu.cn", max_items=3)
    assert len(items) == 3
    assert items[0]["url"].endswith("a/0.htm")


def test_nav_titles_guard():
    """NAV_TITLES 里的常见栏目名不会被当作通知。"""
    assert "研究生院" in NAV_TITLES
