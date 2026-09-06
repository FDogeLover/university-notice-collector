# -*- coding: utf-8 -*-
"""分类体系多领域化：分领域白名单解析 + stype 入库与筛选。"""
import pytest

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

from crawler.parse import SOURCE_TYPES, parse_list


def test_type_whitelist_filtering():
    """同一页面按栏目领域过滤：讲座/招聘/推免各归各的领域。"""
    html = (
        '<a href="a/1.htm">关于举办人工智能前沿学术讲座的通知</a>'
        '<a href="a/2.htm">2026年接收推免生预报名的通知</a>'
        '<a href="a/3.htm">2026届毕业生秋季校园招聘公告</a>'
        '<a href="a/4.htm">国家奖学金评定工作的通知</a>'
    )
    url, domain = "https://www.x.edu.cn/", "x.edu.cn"
    assert [i["title"] for i in parse_list(html, url, domain, stype="讲座学术")] \
        == ["关于举办人工智能前沿学术讲座的通知"]
    assert [i["title"] for i in parse_list(html, url, domain, stype="就业招聘")] \
        == ["2026届毕业生秋季校园招聘公告"]
    assert [i["title"] for i in parse_list(html, url, domain, stype="奖助资助")] \
        == ["国家奖学金评定工作的通知"]
    # 缺省（研究生教育）：白名单较宽（含 通知/公告 等），四条均命中
    default = [i["title"] for i in parse_list(html, url, domain)]
    assert len(default) == 4
    assert "2026年接收推免生预报名的通知" in default


def test_source_types_catalog():
    assert "研究生教育" in SOURCE_TYPES and "综合信息" in SOURCE_TYPES


def test_stype_import_and_api_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "stype.db"))
    from db import store

    store.init_db()
    conn = store.connect()
    store.import_schools(conn, [{
        "name": "X大学", "domain": "x.edu.cn",
        "sources": [
            {"name": "研究生院", "url": "https://gs.x.edu.cn/",
             "category": "通知公告"},
            {"name": "讲座网", "url": "https://talks.x.edu.cn/",
             "category": "讲座", "stype": "讲座学术"},
        ],
    }])
    sid = store.school_id_by_name(conn, "X大学")
    conn.close()
    # 栏目类型入库；未声明 stype 的默认研究生教育
    conn = store.connect()
    stypes = {r["name"]: r["stype"] for r in
              conn.execute("SELECT name, stype FROM sources")}
    sid = store.school_id_by_name(conn, "X大学")
    store.insert_notice(conn, sid, None, "2026年推免生通知",
                        "https://gs.x.edu.cn/a.htm",
                        content_md="正文" * 60, published_at="2026-09-01")
    conn.close()
    assert stypes == {"研究生院": "研究生教育", "讲座网": "讲座学术"}

    from fastapi.testclient import TestClient

    from web.app import app
    api = TestClient(app)
    r = api.get("/api/notices?stype=讲座学术").json()
    assert r["total"] == 0  # 该领域暂无通知，但筛选条件可正常执行
    items = api.get("/api/notices").json()["items"]
    assert items and items[0]["source_type"] == "研究生教育"


def test_reimport_keeps_stype(tmp_path, monkeypatch):
    """yaml 条目缺 stype 时不应清掉库里已有栏目类型。"""
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "keep.db"))
    from db import store

    store.init_db()
    conn = store.connect()
    store.import_schools(conn, [{
        "name": "X大学", "domain": "x.edu.cn",
        "sources": [{"name": "讲座网", "url": "https://talks.x.edu.cn/",
                     "category": "讲座", "stype": "讲座学术"}],
    }])
    store.import_schools(conn, [{
        "name": "X大学", "domain": "x.edu.cn",
        "sources": [{"name": "讲座网", "url": "https://talks.x.edu.cn/",
                     "category": "讲座"}],
    }])
    row = conn.execute(
        "SELECT stype FROM sources WHERE url='https://talks.x.edu.cn/'"
    ).fetchone()
    conn.close()
    assert row["stype"] == "讲座学术"
