# -*- coding: utf-8 -*-
"""学校标签（985/211）：入库、通知卡片携带、层级筛选。"""
import pytest

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

from db import store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "tags.db"))
    store.init_db()
    conn = store.connect()
    store.import_schools(conn, [
        {"name": "甲大学", "domain": "a.edu.cn", "tags": ["985"],
         "sources": [{"name": "研究生院", "url": "https://gs.a.edu.cn/",
                      "category": "通知公告"}]},
        {"name": "乙学院", "domain": "b.edu.cn", "tags": ["211"],
         "sources": [{"name": "研究生院", "url": "https://gs.b.edu.cn/",
                      "category": "通知公告"}]},
        {"name": "丙学院", "domain": "c.edu.cn",
         "sources": [{"name": "研究生院", "url": "https://gs.c.edu.cn/",
                      "category": "通知公告"}]},
    ])
    for name, domain in (("甲大学", "a"), ("乙学院", "b"), ("丙学院", "c")):
        sid = store.school_id_by_name(conn, name)
        store.insert_notice(conn, sid, None, f"{name}2026年推免生通知",
                            f"https://gs.{domain}.edu.cn/a.htm",
                            content_md="正文" * 60, published_at="2026-09-01")
    conn.close()

    from fastapi.testclient import TestClient

    from web.app import app

    return TestClient(app)


def test_tags_imported_and_exposed(client):
    api = client
    schools = {s["name"]: s for s in api.get("/api/schools").json()}
    assert schools["甲大学"]["tags"] == "985"
    assert schools["乙学院"]["tags"] == "211"
    assert schools["丙学院"]["tags"] in (None, "")

    notices = api.get("/api/notices").json()["items"]
    tags = {n["school_name"]: n["school_tags"] for n in notices}
    assert tags["甲大学"] == "985" and tags["乙学院"] == "211"


def test_tag_filter(client):
    api = client
    t985 = api.get("/api/notices?tag=985").json()
    assert t985["total"] == 1
    assert t985["items"][0]["school_name"] == "甲大学"
    t211 = api.get("/api/notices?tag=211").json()
    assert t211["total"] == 1
    assert t211["items"][0]["school_name"] == "乙学院"
    # 非法 tag 值直接 422
    assert api.get("/api/notices?tag=999").status_code == 422


def test_tags_survive_reimport_without_tags(client):
    """yaml 条目缺 tags 时不应清掉库里已有标签。"""
    conn = store.connect()
    store.import_schools(conn, [{"name": "甲大学", "domain": "a.edu.cn"}])
    row = conn.execute("SELECT tags FROM schools WHERE name='甲大学'").fetchone()
    conn.close()
    assert row["tags"] == "985"
