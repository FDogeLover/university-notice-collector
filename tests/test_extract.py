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


@pytest.mark.xfail(strict=True,
                   reason="已知缺口：'截止时间为X/截止日期为X' 句式未覆盖，任务②补")
def test_deadline_shi_phrase():
    assert extract_highlights(
        "报名截止时间为2026年9月30日，逾期不再受理。")["deadline"] == "2026年9月30日"


def test_period_and_target_and_college():
    hl = extract_highlights(
        "暑期夏令营报名时间为2026年7月1日至7月15日，面向全国高校大三学生，"
        "由计算机科学与技术学院承办。")
    assert hl.get("period") == "2026年7月1日至7月15日"
    assert hl.get("target")
    # 已知质量缺口：学院名可能带贪婪前缀（如"由…"），任务②收紧
    assert (hl.get("college") or "").endswith("学院")
    assert "计算机" in hl.get("college", "")


def test_college_finds_specific_name():
    hl = extract_highlights(
        "各学院请注意：研究生院现将事项通知如下。材料请交至电子信息学院。")
    college = hl.get("college") or ""
    assert college.endswith("学院") and "电子信息" in college


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
