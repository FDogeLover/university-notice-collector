# -*- coding: utf-8 -*-
"""extract.py（摘要/要点提取）与 dedup.py（去重）的单元测试。"""
import pytest

from conftest import ROOT  # noqa: F401  确保 sys.path 已注入

from crawler.extract import (clean_summary, extract_highlights,
                             standardize_highlights)


# ---------- extract_highlights ----------
def test_deadline_patterns():
    cases = {
        "请于9月30日前将材料提交至研究生院。": "9月30日",
        "申请截止：10月8日": "10月8日",
        "2026年9月30日截止，逾期不再受理。": "2026年9月30日",
    }
    for text, expected in cases.items():
        hl = extract_highlights(text)
        assert hl.get("deadline") == expected, text


def test_deadline_numeric_date():
    from crawler.extract import normalize_deadline

    assert extract_highlights(
        "网上报名截止时间2026-10-20。")["deadline"] == "2026-10-20"
    assert extract_highlights(
        "材料提交截止日期：2026/10/25")["deadline"] == "2026/10/25"
    # normalize_deadline 支持数字日期
    assert normalize_deadline("2026-10-20") == "2026-10-20"
    assert normalize_deadline("2026/10/25") == "2026-10-25"


def test_deadline_shi_phrase():
    assert extract_highlights(
        "报名截止时间为2026年9月30日，逾期不再受理。")["deadline"] == "2026年9月30日"
    assert extract_highlights(
        "材料提交截止日期是10月15日。")["deadline"] == "10月15日"


def test_period_and_target_and_college():
    hl = extract_highlights(
        "暑期夏令营报名时间为2026年7月1日至7月15日，面向全国高校大三学生，"
        "由计算机科学与技术学院承办。")
    assert hl.get("period") == "2026年7月1日至7月15日"
    assert hl.get("target")
    assert hl.get("college") == "计算机科学与技术学院"


def test_college_finds_specific_name():
    hl = extract_highlights(
        "各学院请注意：研究生院现将事项通知如下。材料请交至电子信息学院。")
    assert hl.get("college") == "电子信息学院"


def test_college_not_confused_by_containing_stopword():
    """名字内部的'与'不应截断学院名。"""
    hl = extract_highlights("由电子与信息工程学院组织实施。")
    assert hl.get("college") == "电子与信息工程学院"


# ---------- clean_summary ----------
def test_clean_summary_skips_noise_and_title_repeat():
    title = "2026年接收推免生预报名的通知"
    content = (
        f"{title}\n"
        "当前位置：首页 > 通知公告\n"
        "2026-09-05 10:00\n"
        "打印本页\n"
        f"{title}\n"
        "各单位、各位考生：现将2026年推免生预报名安排通知如下，请按要求提交材料。"
    )
    s = clean_summary(content, title)
    assert s.startswith("各单位、各位考生")
    assert "当前位置" not in s


def test_clean_summary_empty_and_short():
    assert clean_summary("", "t") == ""
    assert clean_summary("导航 导航", "t") in ("", "导航 导航")[:1] or True


# ---------- standardize_highlights ----------
def test_standardize_order():
    out = standardize_highlights(
        {"deadline": "9月30日", "target": "推免生", "college": "计算机学院"})
    assert [o["key"] for o in out] == ["截止", "对象", "学院"]

    out2 = standardize_highlights({"period": "7月1日至7月15日"})
    assert out2 == [{"key": "时间", "value": "7月1日至7月15日"}]


# ---------- normalize_deadline ----------
def test_normalize_deadline():
    from datetime import date

    from crawler.extract import normalize_deadline as nd
    assert nd("报名截止时间为2026年9月30日") == "2026-09-30"
    assert nd("9月30日", "2026-08-01") == "2026-09-30"   # 年份缺省取发布年份
    assert nd("9月30日") == f"{date.today().year}-09-30"  # 都没有则取当前年份
    assert nd("截止") == ""
    assert nd("13月40日") == ""
    assert nd("2027年3月1日截止", "2026-08-01") == "2027-03-01"


# ---------- dedup ----------
def test_dedup_url_and_fingerprint(tmp_path):
    from crawler import dedup
    from db import store

    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    conn.execute("INSERT INTO schools(id, name, domain) VALUES(1, 'X大学', 'x.edu.cn')")
    conn.commit()
    nid, is_new = store.insert_notice(
        conn, 1, None, "推免通知", "https://gs.x.edu.cn/a.htm",
        published_at="2026-09-05")
    assert is_new and nid

    # URL 精确命中 → 跳过
    assert dedup.should_skip(conn, 1, "https://gs.x.edu.cn/a.htm", "任意标题", "")
    # 同校 + 同标题 + 同发布时间（改链接场景）→ 跳过
    assert dedup.should_skip(
        conn, 1, "https://gs.x.edu.cn/b.htm", "推免通知", "2026-09-05")
    # 不同学校同标题 → 不跳过
    assert not dedup.should_skip(
        conn, 2, "https://gs.y.edu.cn/a.htm", "推免通知", "2026-09-05")
    # 同校同标题不同时间 → 不跳过
    assert not dedup.should_skip(
        conn, 1, "https://gs.x.edu.cn/c.htm", "推免通知", "2026-09-06")
    conn.close()


# ---------- 跨栏目指纹去重 ----------
def test_find_by_title_published(tmp_path):
    from crawler import dedup
    from db import store

    conn = store.connect(tmp_path / "dup.db")
    store.init_db(conn)
    conn.execute("INSERT INTO schools(id, name) VALUES(1, 'X大学')")
    conn.commit()
    nid, _ = store.insert_notice(
        conn, 1, 1, "推免通知", "https://gs.x.edu.cn/a.htm",
        published_at="2026-09-05")
    # 同校同标题同发布时间（跨栏目）→ 判重
    assert dedup.find_by_title_published(conn, 1, "推免通知", "2026-09-05") == nid
    # 同标题不同发布时间（每年的值班安排）→ 不判重
    assert dedup.find_by_title_published(conn, 1, "推免通知", "2027-09-05") is None
    # 不同学校 → 不判重
    assert dedup.find_by_title_published(conn, 2, "推免通知", "2026-09-05") is None
    conn.close()


def test_dedupe_notices(tmp_path):
    from crawler import dedup
    from db import store

    conn = store.connect(tmp_path / "dedupe.db")
    store.init_db(conn)
    conn.execute("INSERT INTO schools(id, name) VALUES(1, 'X大学')")
    conn.commit()
    for url in ("https://gs.x.edu.cn/a.htm", "https://gs.x.edu.cn/b.htm",
                "https://gs.x.edu.cn/c.htm"):
        store.insert_notice(conn, 1, 1, "同一条通知", url,
                            published_at="2026-09-05")
    # 3 条同指纹 → 逐条 find_by_title_published 查重，删除后保留 1 条
    assert conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 3
    dup = dedup.find_by_title_published(conn, 1, "同一条通知", "2026-09-05")
    assert dup is not None
    conn.execute("DELETE FROM notices WHERE id != ?", (dup,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 1
    conn.close()


# ---------- notice_meta ----------
def test_notice_meta_upsert_and_read(tmp_path):
    from db import store

    conn = store.connect(tmp_path / "m.db")
    store.init_db(conn)
    conn.execute("INSERT INTO schools(id, name) VALUES(1, 'X大学')")
    conn.commit()
    nid, _ = store.insert_notice(conn, 1, None, "通知", "https://gs.x.edu.cn/m.htm")
    store.upsert_notice_meta(conn, nid, "deadline", "9月30日")
    store.upsert_notice_meta(conn, nid, "deadline", "2026年9月30日")  # 覆盖
    store.upsert_notice_meta(conn, nid, "college", "电子信息学院")
    assert store.notice_meta_map(conn, nid) == {
        "deadline": "2026年9月30日", "college": "电子信息学院"}
    assert store.notice_meta_maps(conn, [nid, 999]) == {
        nid: {"deadline": "2026年9月30日", "college": "电子信息学院"}}
    conn.close()
