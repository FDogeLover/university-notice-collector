# -*- coding: utf-8 -*-
"""从通知正文中提取关键信息 + 生成干净摘要（供列表预览使用）。"""

import re

# 导航 / 杂文噪音行特征：命中即视为噪音行，不进入摘要
_NOISE = [
    r"首页|导航|登录|注册|设为首页|加入收藏|站内搜索|友情链接|分享|打印本页|返回上[一页级]",
    r"学信网|研招网|中国研究生招生信息网|阳光高考|中国政府网|教育部政府门户",
    r"©|Copyright|copyright|版权所有|ICP|备案号|公安局|统计局",
    r"(邮编|电话|传真|邮箱|E-?Mail|信箱|地址|网址|网站地图|隐私|条款|系统支持)[:：]?",
    r"\.(edu|ac)\.cn[/\s]|Powered by|Telerik|cms|next\.config",
    r"\w+@[\w.-]+\.\w{2,}|[/\\国际港澳台]*联络[/\\:]|研究生院学堂|在线操作手册",
    r"^第[一二三四五六七八九十百\d]+[章节回]",
    r"Open Menu|Menu|Home|About|Search|Copyright[^,，。]{0,6}|Powered By",
    r"^.+(大学|学院|机构|学校)(新闻)?网(站|首页)?$",
    r"^.+(招生网|信息网|新闻网|研究生院网|官网)$",
    r"^欢迎(来到|访问)|^设为首页$",
    r"^当前位置[：:]?",
    # 信息公开清单栏目导航（详情页正文抓成导航页时的特征）
    r"^(财务、?资产(及收费)?|人事师资|教学质量|学生管理服务|学风建设|"
    r"学位、?学科(信息)?|对外交流与合作|其他(信息)?|目录清单|年度报告|"
    r"制度建设|学校基本信息|招生考试(信息)?|信息(公开)?(指南|事项清单)?)$",
    r"^(通知公告|新闻动态|更多\+?|最新通知|培养工作通知|招生信息|信息公开网|网站首页)$",
    r"^办学规模、校级领导班子|^学校机构设置、学科情况|^各类在校生情况、教师和专业技术人员数量",
    r"^信息公开事项对照清单|^信息公开指南$|^信息公开目录$",
]

# 学院名排除（泛词）
_COLLEGE_EXCLUDE = {
    "研究生院", "各学院", "招生学院", "学院党委", "学院办公室", "教务处",
    "研究生招生信息网", "招生办", "学校招生", "学院研究生", "学部学院",
}

# 截止 / 截至 类时间
_DEADLINE_PATTERNS = [
    re.compile(
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)"
        r"\s*(?:24\s*时\s*)?(?:(?:之)?前|止|截止|结束)"
    ),
    re.compile(
        r"(?:报名|申请|系统|材料|提交|考核|资格|截至|截止)"
        r"(?:截止|截至|关闭|报名截止)[：:（(\s]*"
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)"
    ),
    # "截止时间/截止日期（为/是）X" 句式
    re.compile(
        r"(?:报名|申请|材料|提交)?(?:截止时间|截止日期)\s*(?:为|是)?\s*[:：]?\s*"
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)"
    ),
    re.compile(
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)\s*(?:为)?\s*"
        r"(?:截止时间|逾期|最后期限|报名截止)"
    ),
]

# 起止时间范围
_PERIOD_PATTERN = re.compile(
    r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)"
    r"\s*(?:至|到|—|–|~|～|-)\s*"
    r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)"
)

# 招生对象
_TARGET_PATTERN = re.compile(
    r"(优秀?应届?(?:本科|硕士|博士)?毕业生|推免生|推荐免试研究生|直博生|"
    r"全国?高校(?:在读)?[一二三四五六]年级|夏令营(?:营员|学员)|全体研究生|"
    r"(?:应届)?(?:本科学历|硕士(?:研究生)|博士(?:研究生))|大[三四五六][的年级学])"
)

# 学院名提取：在后缀（学院/学部/研究院）出现处向前回溯取名字，
# 遇到功能词（动词/介词/指示词）即停，避免贪婪匹配吞进"材料请交至…"等前缀
_COLLEGE_SUFFIX_RE = re.compile(r"(学院|学部|研究院)")
_COLLEGE_STOP = set("在由是为于从到向把请交送至对按根经需应要使含据各该本次等之其")
_COLLEGE_MAX_NAME = 12

# 泛指性提法（"报考学院/我校相关学院"等）不是具体学院名
_COLLEGE_GENERIC = re.compile(r"^(我校|报考|录取|相关|所在|承办|所属|课程|接收|招生)")
# 名字里含虚词的一定不是学院名（如"…模板的学院"）
_COLLEGE_INVALID = re.compile(r"[的了们呢吗吧呀让被给]")


def _find_college(head):
    """从文本片段中提取第一个具体学院名，无则返回 ""。"""
    for run_m in re.finditer(r"[\u4e00-\u9fa5]{3,}", head):
        run = run_m.group(0)
        hits = [(m.start(), m.group(0))
                for m in _COLLEGE_SUFFIX_RE.finditer(run)]
        if not hits:
            continue
        for pos, suffix in sorted(hits):
            name = ""
            i = pos - 1
            while i >= 0 and len(name) < _COLLEGE_MAX_NAME \
                    and run[i] not in _COLLEGE_STOP:
                name = run[i] + name
                i -= 1
            if len(name) < 2:
                continue
            name = name + suffix
            if name in _COLLEGE_EXCLUDE or name.endswith("研究生院"):
                continue
            if _COLLEGE_GENERIC.match(name) or _COLLEGE_INVALID.search(name):
                continue
            return name
    return ""


def clean_summary(content, title, max_len=120):
    """生成干净摘要：跳过标题重复、日期行、噪音行，取首个有效正文段。"""
    t = (content or "").strip()
    if not t:
        return ""
    if title and t.startswith(title):
        t = t[len(title):].lstrip(" \n:：-—｜|[]《》\"'")
    # 标题归一化：去掉常见栏目前缀【xxx】与空白，用于识别正文首行的重复标题
    title_norm = re.sub(r"\s+", "", re.sub(r"^【[^】]*】\s*", "", title or ""))
    for ln in re.split(r"[\n\r]+", t):
        s = ln.strip()
        if not s:
            continue
        if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", s):
            continue  # 纯标点/空字符行
        if any(re.search(p, s, re.I) for p in _NOISE):
            continue
        if re.match(r"^(20\d{2})[-/年.]\d{1,2}", s):
            continue
        if len(s) <= 8 and not re.search(r"[，。；：、！？]", s):
            continue  # 过短且不成句，判定为导航/标签
        # 跳过与标题重复的段落（部分网站详情页首段是重复标题）
        s_norm = re.sub(r"\s+", "", s)
        if title_norm and (s_norm == title_norm
                           or (len(title_norm) >= 8 and s_norm.startswith(title_norm))
                           or (len(s_norm) >= 8 and title_norm.startswith(s_norm))):
            continue
        return " ".join(s.split())[:max_len]
    return ""


def extract_highlights(content, title=""):
    """抽取关键信息，返回 dict（可能包含 deadline / period / target / college）。"""
    t = (content or "")[:2000]
    if not t:
        return {}

    hl = {}

    # 截止时间
    for pat in _DEADLINE_PATTERNS:
        m = pat.search(t)
        if m:
            hl["deadline"] = m.group(1)
            break

    # 起止范围（无截止时作为补充信息）
    if "deadline" not in hl:
        m = _PERIOD_PATTERN.search(t)
        if m:
            hl["period"] = "至".join(m.groups())

    # 招生对象
    m = _TARGET_PATTERN.search(t)
    if m:
        hl["target"] = m.group(1)

    # 学院（正文前 400 字符内取首个具体学院名）
    hl_college = _find_college(t[:400])
    if hl_college:
        hl["college"] = hl_college

    return hl


def standardize_highlights(hl, published_at=""):
    """把抽取结果整理成前端展示顺序（deadline > period > target > college）。"""
    ordered = []
    if hl.get("deadline"):
        ordered.append({"key": "截止", "value": hl["deadline"]})
    elif hl.get("period"):
        ordered.append({"key": "时间", "value": hl["period"]})
    for k, label in (("target", "对象"), ("college", "学院")):
        if hl.get(k):
            ordered.append({"key": label, "value": hl[k]})
    return ordered