# -*- coding: utf-8 -*-
"""build_site.py 静态站生成器测试。"""
import json

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

from db import store


def _seed(conn):
    store.import_schools(conn, [{
        "name": "X大学", "domain": "x.edu.cn",
        "sources": [{"name": "研究生院", "url": "https://gs.x.edu.cn/",
                     "category": "通知公告"}],
    }])
    sid = store.school_id_by_name(conn, "X大学")
    nid, is_new = store.insert_notice(
        conn, sid, None, "2026年推免生接收通知", "https://gs.x.edu.cn/a.htm",
        content_md="报名截止时间为2026年9月30日。" + "正文" * 60,
        published_at="2026-09-01", type_tag="推免")
    assert is_new
    return nid


def test_build_site_generates_valid_files(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "s.db"))
    store.init_db()
    conn = store.connect()
    nid = _seed(conn)
    from build_site import collect, write_site

    notices, stats, deadlines, contents, school_tags = collect(conn, 20000)
    conn.close()

    out = write_site(tmp_path / "site", notices, stats, deadlines, contents,
                     school_tags)
    assert (out / "index.html").exists()
    assert (out / "assets" / "app.js").exists()
    assert (out / "assets" / "style.css").exists()

    raw = (out / "data" / "notices.js").read_text(encoding="utf-8")
    assert raw.startswith("window.SITE_DATA = ") and raw.endswith(";")
    data = json.loads(raw[len("window.SITE_DATA = "):-1])
    assert data["stats"]["notices"] == 1
    assert data["stats"]["schools"] == 1
    assert data["notices"][0]["title"] == "2026年推免生接收通知"
    assert data["notices"][0]["has_content"] is True
    # "最近更新"必须是抓取时间，不能是原文发布日期
    from datetime import date

    assert data["stats"]["last_fetch"].startswith(date.today().isoformat())
    assert not data["stats"]["last_fetch"].startswith("2026-09-01")

    # 正文快照文件：window.SITE_CONTENT 注入格式，含正文与 JSON 合法
    ctext = (out / "data" / "content" / f"{nid}.js").read_text(encoding="utf-8")
    prefix = 'window.SITE_CONTENT=window.SITE_CONTENT||{};window.SITE_CONTENT["'
    assert ctext.startswith(prefix)
    payload = json.loads(ctext[ctext.index("=", ctext.index(";") + 1) + 1:].rstrip(";"))
    assert "报名截止时间" in payload["content"]


def test_build_site_excludes_disabled_school(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIV_DB", str(tmp_path / "s2.db"))
    store.init_db()
    conn = store.connect()
    nid = _seed(conn)
    conn.execute("UPDATE schools SET enabled=0")
    conn.commit()
    from build_site import collect

    notices, stats, _, _, _ = collect(conn, 20000)
    conn.close()
    assert notices == [] and stats["notices"] == 0
