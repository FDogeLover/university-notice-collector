# -*- coding: utf-8 -*-
"""学校管理（停用/启用/删除）接口与过滤行为测试（TestClient + 临时库）。"""
import os

import pytest

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

from db import store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "web.db"))
    store.init_db()
    conn = store.connect()
    store.import_schools(conn, [{
        "name": "测试大学", "domain": "test.edu.cn",
        "sources": [{"name": "研究生院", "url": "https://gs.test.edu.cn/",
                     "category": "通知公告"}],
    }])
    sid = store.school_id_by_name(conn, "测试大学")
    store.insert_notice(conn, sid, None, "2026年推免生招生通知",
                        "https://gs.test.edu.cn/a.htm",
                        content_md="正文" * 100, published_at="2026-09-01")
    conn.close()

    from fastapi.testclient import TestClient

    from web.app import app

    return TestClient(app), sid


def test_school_toggle_hides_data_but_keeps_it(client):
    api, sid = client
    assert api.get("/api/stats").json()["notices"] == 1
    assert len(api.get("/api/notices").json()["items"]) == 1

    # 停用：统计/列表/类型全部隐藏
    r = api.post(f"/api/schools/{sid}/enabled", json={"enabled": False})
    assert r.status_code == 200 and r.json()["ok"]
    stats = api.get("/api/stats").json()
    assert stats["schools"] == 0 and stats["notices"] == 0
    assert api.get("/api/notices").json()["total"] == 0
    assert api.get("/api/types").json() == []
    # 学校清单仍返回（含 enabled=0），数据未删
    schools = api.get("/api/schools").json()
    assert len(schools) == 1 and schools[0]["enabled"] == 0
    assert schools[0]["notice_count"] == 1

    # 启用：恢复显示
    api.post(f"/api/schools/{sid}/enabled", json={"enabled": True})
    assert api.get("/api/stats").json()["notices"] == 1


def test_school_delete_removes_everything(client):
    api, sid = client
    r = api.delete(f"/api/schools/{sid}")
    assert r.status_code == 200 and r.json()["ok"]
    assert api.get("/api/schools").json() == []
    assert api.get("/api/stats").json()["notices"] == 0
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM notice_meta").fetchone()["c"] == 0
    conn.close()


def test_school_api_404_on_unknown(client):
    api, _ = client
    assert api.post("/api/schools/9999/enabled", json={"enabled": False}).status_code == 404
    assert api.delete("/api/schools/9999").status_code == 404


def test_init_db_cleans_orphan_sources(tmp_path, monkeypatch):
    """init_db 自愈：清理指向不存在学校的栏目（模拟旧版删除只删学校留栏目）。"""
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "orphan.db"))
    store.init_db()
    conn = store.connect()
    conn.execute("INSERT INTO schools(id, name) VALUES(8888, '遗留大学')")
    conn.execute(
        "INSERT INTO sources(school_id, name, url) VALUES(8888, '遗留栏目', "
        "'https://gs.left.edu.cn/')")
    # 模拟外部工具（sqlite3 CLI 默认关外键）删校留栏目的历史场景
    conn.commit()  # PRAGMA foreign_keys 在事务内不生效，先提交
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM schools WHERE id=8888")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 1
    conn.close()
    store.init_db()  # 再跑一次触发清理
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 0
    conn.close()
