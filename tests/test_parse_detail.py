# -*- coding: utf-8 -*-
"""parse_detail 回归测试：真实详情页 fixture + 合成 HTML 用例（全部离线）。"""
import re

import pytest

from conftest import load_fixture, load_manifest

from crawler import parse as crawl_parse
from crawler.parse import infer_type, parse_detail

MANIFEST = load_manifest()
DETAIL_FIXTURES = sorted(k for k in MANIFEST if k.startswith("detail_"))


@pytest.mark.parametrize("name", DETAIL_FIXTURES)
def test_detail_fixture_content(name):
    """真实详情页：标题、发布时间格式、正文质量（fixture 选取时已达标）。"""
    meta = MANIFEST[name]
    detail = parse_detail(load_fixture(name), meta["url"])
    assert detail["title"], f"{name} 未解析出标题"
    if detail["published_at"]:
        assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", detail["published_at"])
    assert len(detail["content_md"]) >= 300, \
        f"{name} 正文仅 {len(detail['content_md'])} 字，正文定位可能已退化"
    # 正文不应是菜单碎片：平均行长有下限
    lines = [ln for ln in detail["content_md"].splitlines() if ln.strip()]
    assert sum(len(ln) for ln in lines) / len(lines) >= 8


def test_detail_title_and_time_variants():
    html = """
    <html><head><title>旧标题 - 研究生院</title></head><body>
    <script>var a=1;</script>
    <h1>2026年接收推荐免试研究生预报名的通知</h1>
    <div class="v_news_content">
      <p>发布日期：2026年9月5日</p>
      <p>各单位、各位考生：现将2026年推免生预报名具体安排通知如下。</p>
      <p>报名系统开放时间为2026-09-10至2026-09-24，请按时提交材料。</p>
    </div>
    </body></html>
    """
    d = parse_detail(html, "https://gs.x.edu.cn/t/1.htm")
    assert d["title"] == "2026年接收推荐免试研究生预报名的通知"
    assert d["published_at"] == "2026-09-05"
    assert "预报名具体安排" in d["content_md"]
    assert "var a=1" not in d["content_md"]


def test_detail_title_falls_back_to_title_tag():
    html = """
    <html><head><title>关于2026年硕士研究生招生考试报名的公告</title></head>
    <body><div id="content"><p>正文段落一：报名即将开始，请考生留意。</p>
    <p>正文段落二：逾期不再补报，务必在规定时间内完成网上报名。</p>
    <p>正文段落三：如有疑问请联系研究生招生办公室，联系电话见官网。</p></div></body></html>
    """
    d = parse_detail(html, "https://gs.x.edu.cn/t/2.htm")
    assert d["title"].startswith("关于2026年硕士研究生招生考试报名")


def test_detail_published_finds_bracketed_label():
    """"[发表时间]：2026-09-06" 这类带方括号的标签要认（北外研究生院）。"""
    html = """
    <html><body>
    <h1>关于北外接收推荐免试攻读研究生（含直博生）的问题解答</h1>
    <p>发布者：[发表时间]：2026-03-06 [来源]：研究生院</p>
    <div class="v_news_content">
      <p>各位考生：我校接收推荐免试攻读研究生考试报名自2026年9月6日起，
         请各位考生务必于报名时间段内登录系统完成报名。</p>
    </div></body></html>
    """
    d = parse_detail(html, "https://graduate.bfsu.edu.cn/info/1074/4466.htm")
    assert d["published_at"] == "2026-03-06"
    assert d["published_src"] == "label"   # 页面自己写明的，采集时优先采信


def test_detail_published_rejects_future_and_invalid_dates():
    """发布时间不能落在未来，也不能是页面路径匹配出来的"2026-09-87"。

    正文开头的日程（报名自X日起）常比发布时间更靠前，误当发布时间就会让
    卡片右上角显示成未来日期——线上"发布 2027-09-06"正是这么来的。
    """
    from datetime import date, timedelta

    future = date.today() + timedelta(days=40)
    cn = f"{future.year}年{future.month}月{future.day}日"
    html = f"""
    <html><head><meta name="description" content="我校考试报名自{cn}起"></head>
    <body>
    <h1>关于接收推荐免试攻读研究生考试报名的通知</h1>
    <div class="v_news_content">
      <p>各位考生：我校接收推荐免试攻读研究生考试报名自{cn}起，
         请按时登录系统完成报名，逾期不再受理。</p>
      <img src="../images/2026-09/7abc123.png">
      <p>特此通知。</p>
    </div></body></html>
    """
    assert parse_detail(html, "https://gs.x.edu.cn/t/f.htm")["published_at"] == ""
    assert crawl_parse._published_iso("2026", "09", "70") == ""   # 路径里的假日期


def test_detail_published_skips_schedule_dates_in_visible_text():
    """兜底扫描：日程/期限语境的日期跳过，普通日期仍可作发布时间。"""
    schedule = """
    <html><body><div id="content">
      <p>2026-11-14（星期六）上午举行数学竞赛，即日起开始报名。</p>
      <p>竞赛时间：2026-11-14 上午9:00-11:30，请提前半小时入场。</p>
      <p>各学院请于报名截止时间前将名单报送教务处，逾期不再受理。</p>
    </div></body></html>
    """
    assert parse_detail(schedule, "https://jwc.x.edu.cn/t/1.htm")["published_at"] == ""

    plain = """
    <html><body><div id="content">
      <p>2026-03-05</p>
      <p>各学院：现将研究生培养方案修订工作安排通知如下，请遵照执行。</p>
    </div></body></html>
    """
    d = parse_detail(plain, "https://gs.x.edu.cn/t/2.htm")
    assert d["published_at"] == "2026-03-05"
    assert d["published_src"] == "scan"    # 扫描得到：采集时让位给列表行日期


def test_detail_published_skips_footer_copyright_dates():
    """页脚版权行里的日期不是发布时间（苏州大学那批 2006-07-02 就是它来的）。"""
    html = """
    <html><body>
    <h1>苏州大学2026年全日制普通本科招生章程</h1>
    <div class="v_news_content">
      <p>第一章 总则。为保证学校招生工作顺利进行，切实维护考生合法权益，
         根据相关法律法规和教育部有关规定，结合学校实际制定本章程。</p>
      <p>第二章 组织机构。学校成立招生工作领导小组，负责招生工作的
         组织、协调与监督，招生办公室负责具体实施。</p>
    </div>
    <div class="footer">版权所有 2006-07-02 苏州大学 苏ICP备05012345号</div>
    </body></html>
    """
    d = parse_detail(html, "https://zsb.suda.edu.cn/t/1.htm")
    assert d["published_at"] == ""
    assert d["published_src"] == ""


def test_detail_garbage_page_yields_empty_content():
    """纯导航/菜单页（多行短行）：宁可置空也不留垃圾快照。"""
    links = "".join(f"<p><a href='a{i}.htm'>栏目{i}</a></p>" for i in range(20))
    html = f"<html><body><div class='menu'>{links}</div></body></html>"
    d = parse_detail(html, "https://gs.x.edu.cn/nav.htm")
    assert d["content_md"] == ""


def test_infer_type():
    cases = {
        "2026年接收推免生预报名的通知": "预推免",
        "关于开展2026年推荐免试研究生接收工作的通知": "推免",
        "第十三届优秀大学生暑期夏令营报名启动": "夏令营",
        "2026年硕士研究生招生简章": "招生",
        "2026年复试基本分数线及复试名单的通知": "复试",
        "2026年硕士研究生招生调剂公告": "调剂",
        "2026年拟录取名单公示": "公示",
        "关于做好期末考核工作的通知": "通知",
        # 奖助类（散布在学生处/学工部等多个栏目，按标题识别）
        "关于开展2026年研究生学业奖学金评审工作的通知": "奖助",
        "国家助学贷款申请办理通知": "奖助",
        "关于家庭经济困难学生认定工作的通知": "奖助",
        "2026年勤工助学岗位招聘启事": "奖助",
        "关于发放临时困难补助的通知": "奖助",
    }
    for title, tag in cases.items():
        assert infer_type(title) == tag, title
