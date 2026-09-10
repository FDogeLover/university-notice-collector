# -*- coding: utf-8 -*-
"""parse_detail 回归测试：真实详情页 fixture + 合成 HTML 用例（全部离线）。"""
import re

import pytest

from conftest import load_fixture, load_manifest

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
