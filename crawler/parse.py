# -*- coding: utf-8 -*-
"""解析层：列表页抽链接、详情页抽标题/时间/正文、类型打标。"""
import re

from datetime import date

# 抑制 pypdf 对无效 PDF（下载到 HTML）的无害警告
import logging
logging.getLogger("pypdf").setLevel(logging.CRITICAL)
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString

from crawler.extract import clean_notice_title

# 内容标题关键词：命中才视为有效通知（过滤导航/杂项链接）
KEYWORDS = re.compile(
    r"(推免|推荐免试|预推免|夏令营|直博|保研|招生|简章|报考|报名|"
    r"通知|公告|公示|复试|调剂|录取|免试|优秀应届|研究生|博士|硕士|"
    r"申请|办法|名单|日程|宣讲|接收)",
    re.I,
)

# 各信息领域的链接过滤白名单：按栏目 stype 选择。
# 存量栏目均为"研究生教育"（沿用原全局 KEYWORDS，行为不变）；
# 新领域通过在 schools.yaml 栏目上声明 stype 来扩展采集面。
TYPE_KEYWORDS = {
    "研究生教育": KEYWORDS,
    "本科招生": re.compile(
        r"(高考|本科|录取分数|招生章程|招生计划|分省|新生|强基|"
        r"艺术类|体育类|专项计划|保送|综合评价|报名|录取|招生)",
        re.I,
    ),
    "讲座学术": re.compile(
        r"(讲座|报告会|论坛|研讨会|学术|沙龙|会议|研修|直播|开班)", re.I),
    "就业招聘": re.compile(
        r"(招聘|就业|双选会|宣讲会|校园招聘|人才引进|招考|事业单位|求职|用人)", re.I),
    "奖助资助": re.compile(
        r"(奖学金|助学金|资助|贷款|补贴|津贴|评定|助管|助教|三助)", re.I),
    "综合信息": re.compile(r"(通知|公告|公示|启事|安排|通报|任免|招标|采购)", re.I),
}

# 全部已知领域（供 API/前端下拉使用）
SOURCE_TYPES = ["研究生教育", "本科招生", "讲座学术", "就业招聘", "奖助资助", "综合信息"]


def same_domain(url, domain):
    host = (urlparse(url).netloc or "").lower()
    return host == domain or host.endswith("." + domain)


# 常见导航/栏目名，命中即跳过（避免把导航链接当通知）
NAV_TITLES = {
    "研究生招生网", "研究生院官网", "研究生院", "本科招生网", "信息公开",
    "信息公开网", "通知公告", "新闻动态", "首页", "招生信息", "硕士招生",
    "博士招生", "公告通知", "硕士最新通知", "博士最新通知", "最新通知",
    "培养工作通知", "学位申请及授予", "优秀博士学位论文", "硕士研究生招生",
    "博士研究生招生", "招生简章", "招生政策", "招生专业目录", "更多",
    "常用信息", "网站首页", "招生宣传",
}

# 明显是列表页/首页的地址片段，命中即跳过
_INDEX_URL_HINTS = (
    "index.htm", "index.chtml", "index.jsp", "index.html",
    "list.htm", "list.psp", "list.html",
    "default.htm", "default.aspx", "default.jsp",
)

# 办事材料/表单类标题：不是通知，命中即跳过（除非标题同时含 招生/推免）
_FORM_TITLES = re.compile(
    r"(申请表|登记表|审批表|申请书|空白表|模板|流程|办事指引|"
    r"换算方法|借用教室|考核表|开题报告|报销|备案表|汇总表)",
    re.I,
)


# 页面里的"发布时间/发布日期：yyyy年M月d日"式标签（比全文首个日期更可信）。
# 标签与日期之间常有括号与空白："[发表时间]：2026-09-06"（北外研究生院）。
_PUBLISHED_LABEL_RE = re.compile(
    r"(?:发布时间|发布日期|发表时间|更新时间|更新日期|发布于|信息发布|发布者)"
    r"\s*[\]】)）]?\s*[:：]?\s*"
    r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")

# 常见发布时间 meta 标签名（小写比较）
_PUBLISHED_META_KEYS = {
    "pubdate", "publishdate", "pub_date", "date", "dc.date",
    "article:published_time", "og:published_time", "publishdateidentifier",
}


def _published_iso(year, month, day):
    """把日期三元组规范成发布时间 ISO 串；不能当发布时间用时返回 ""。

    两条硬约束，缺一条卡片上就会出现"未来发布"或"2026-09-87"这类假日期：

    - 必须是真实存在的日历日期。图片/附件路径（``../images/2026-09/7abc.png``）
      会被日期正则匹配成 2026-09-70，只有构造 date 才能挡掉；
    - 不得晚于今天。正文开头的日程（"报名自2027年9月6日起"、"竞赛时间：
      2026年11月14日"、"双选会举办时间 2026-10-23"）比发布时间更靠前，
      误取就会把卡片日期填到未来。
    """
    try:
        d = date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return ""
    if not 2000 <= d.year <= date.today().year:
        return ""
    if d > date.today():
        return ""
    return d.isoformat()


def _extract_published(html, soup):
    """提取发布时间，返回 (ISO 日期, 来源)。

    来源 "meta"/"label" 表示页面自己写明了发布时间，可信；返回 ("", "")
    表示页面没有发布标记（调用方再考虑兜底或改用列表页日期）。
    """
    for m in soup.find_all("meta"):
        key = (m.get("name") or m.get("property") or "").strip().lower()
        if key not in _PUBLISHED_META_KEYS:
            continue
        dm = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})",
                       (m.get("content") or ""))
        if dm:
            iso = _published_iso(*dm.groups())
            if iso:
                return iso, "meta"
    m = _PUBLISHED_LABEL_RE.search(html)
    if m:
        return _published_iso(*m.groups()), "label"
    return "", ""


# 兜底扫描用：任意位置的完整日期；日程/期限语义的引导词
_ANY_DATE_RE = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
_SCHEDULE_LEAD_RE = re.compile(
    r"(?:截止|截至|不晚于|最晚|不超过|有效期|施行)\s*[:：]?\s*$"
    r"|(?:报名|申请|考试|竞赛|比赛|举办|举行|开课|上课|授课|报到|答辩|复试|"
    r"面试|活动|会议|评审|公示|征集|提交|返校|开学|放假)\s*"
    r"(?:时间|日期|截止)?\s*[:：]?\s*$"
    r"|(?:自|至|到)\s*$"
)
# 只扫可见文本时要排除的标签：脚本/样式里全是数字串与路径
_NON_TEXT_TAGS = {"script", "style", "noscript", "head", "title"}


# 页脚/版权类容器：日期正则扫到"版权所有 2006-07-02"就会被当成发布时间
_FOOTER_HINT_RE = re.compile(r"foot|copyright|copy|banquan|版权|备案|icp", re.I)
# 日期前后出现这些词说明它属于版权/备案信息，不是发布时间
_NOISE_NEAR_RE = re.compile(r"版权|备案|©|copyright|icp|公安局|保留所有权利", re.I)


def _strip_footer_blocks(soup):
    """移除页脚/版权类容器（`div.footer` 这类不在 decompose 标签列表里）。

    按快照遍历并跳过已被祖先 decompose 掉的节点（其 attrs 已置空）。
    """
    for el in list(soup.find_all(True)):
        if el.attrs is None or not el.name:      # 祖先已先被删掉
            continue
        cls = " ".join(el.get("class") or [])
        eid = el.get("id") or ""
        if _FOOTER_HINT_RE.search(f"{cls} {eid}"):
            el.decompose()


def _first_plausible_date(soup):
    """页面可见文本里第一个"像发布时间"的日期；没有返回 ""。

    兜底用（页面没有任何发布标记时）。只扫可见文本：属性和脚本里的
    日期串（图片路径 ``2026-09/7abc.png``）不属于页面内容，正是库里
    "2026-09-70" 这类假日期的来源。引导词是日程/期限语义的日期同样跳过——
    那些是"什么时候截止/举办"，不是"什么时候发布的"；页脚版权行里的日期
    （"版权所有 2006-07-02"）同样跳过，苏州大学那批 2006-07-02 就是它来的。
    """
    chunks = []
    for s in soup.find_all(string=True):
        parent = s.parent
        if parent is None or parent.name in _NON_TEXT_TAGS:
            continue
        t = s.strip()
        if t:
            chunks.append(t)
    text = re.sub(r"\s+", " ", " ".join(chunks))
    for m in _ANY_DATE_RE.finditer(text):
        iso = _published_iso(*m.groups())
        if not iso:
            continue
        before = text[max(0, m.start() - 12):m.start()]
        if _SCHEDULE_LEAD_RE.search(before):
            continue
        if _NOISE_NEAR_RE.search(text[max(0, m.start() - 30):m.end() + 30]):
            continue
        return iso
    return ""


# 列表行日期（列表页是站点自己给出的发布时间，比详情页正文里的日期可靠）
# 每条：(正则, 分组顺序 y/m/d, 是否只认行首行尾)
_ROW_DATE_PATTERNS = [
    # 完整日期：2026-09-11 / 2026/9/11 / 2026.09.11 / 2026年9月11日
    (re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})"),
     "ymd", False),
    # 日在前："17 2026-09 综合办公室 关于做好…"（中国矿业大学（北京）研究生院）
    (re.compile(r"(?<!\d)(\d{1,2})\s+([12]\d{3})\s*[-/年.]\s*(\d{1,2})"),
     "dym", False),
    # 无年份的月日：09-18 / 9/18（列表页惯例：日期贴在行首或行尾）
    (re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)"),
     "md", True),
    (re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "md", True),
]


def _year_guess_iso(month, day):
    """只有月日的列表日期：按"不晚于今天"回推年份（列表上的日期都已发布过）。"""
    today = date.today()
    for year in (today.year, today.year - 1):
        iso = _published_iso(year, month, day)
        if iso:
            return iso
    return ""


# 行首行尾的装饰字符：判断"日期是否贴着行首/行尾"时先忽略它们
_EDGE_CHARS = " \t[]【】()（）〔〕『』「」<>《》-—–·:：,，、|"


def _row_date(text, explicit_year=False, edge_only=False):
    """从列表行文本里取发布时间（YYYY-MM-DD），取不到返回 ""。

    explicit_year=True 时只认带年份的写法（"2026-09-11"）：列表只给月日
    （华中科技大学列表长期只有 "03/17"）时年份靠回推，拿去覆盖库内已有的
    历史日期会把 2022 年的存档改成今年。

    edge_only=True 时任何写法都只认贴在行首/行尾的日期——扫标题文本时用：
    标题正文里的"2026年9月20日至10月23日"是日程，不是发布时间。
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    # 首尾的括号/分隔符不算内容："…招新通知[2026-07-09]" 的日期仍算贴着行尾
    core_start = len(text) - len(text.lstrip(_EDGE_CHARS))
    core_end = len(text.rstrip(_EDGE_CHARS))
    for pat, order, pat_edge in _ROW_DATE_PATTERNS:
        if explicit_year and "y" not in order:
            continue
        for m in pat.finditer(text):
            if (edge_only or pat_edge) and not (m.start() <= core_start
                                                or m.end() >= core_end):
                continue  # 行中间的日期不是发布时间（"第3-4周"、标题里的日程）
            vals = dict(zip(order, m.groups()))
            if "y" in vals:
                iso = _published_iso(vals["y"], vals["m"], vals["d"])
            else:
                iso = _year_guess_iso(vals["m"], vals["d"])
            if iso:
                return iso
    return ""


# 附件类文件后缀：详情页若直接是附件（PDF/Word/Excel 等），无法当正文解析。
# 采集器应跳过正文抓取（存标题+链接），避免把二进制当文本存入导致乱码。
FILE_EXT_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|rar|zip|7z|jpe?g|png|gif|bmp|wps|et|dps)"
    r"(\?|#|$)", re.I)


def is_file_url(url):
    """判断 URL 是否直接指向附件文件（而非 HTML 详情页）。"""
    return bool(url and FILE_EXT_RE.search(url))


# 行容器文本上限：超过就当成整页容器（会混进别条通知的日期），不再上溯
_ROW_TEXT_MAX = 400


def _list_item_date(anchor, raw_text, explicit_year=False):
    """列表行里的发布时间：链接文本（"09-18 教通知…"、"17 2026-09 综合…"）
    → 相邻节点（"…的通知 2026-09-11"、"<div>14</div><div>2026-08</div>"）。

    标题正文里的日期不参与（"关于2026年9月20日至10月23日…的通知"里的
    日程不是发布时间），故把标题整段剔除后再匹配行内其余部分。
    """
    # 链接文本就是标题本身：只认贴在标题首尾的日期（"09-18 教通知…"、
    # "…的通知 2026-09-11"），标题正文里的日程日期不参与
    from_link = _row_date(raw_text, explicit_year, edge_only=True)
    if from_link:
        return from_link
    node = anchor.parent
    for _ in range(2):  # 父节点 → 祖父节点（表格一行的日期常在相邻单元格）
        if node is None:
            break
        # 只认"一条通知一行"的容器：含多个链接（整列/整页）时日期不再是这条的
        if len(node.find_all("a", href=True)) == 1:
            row = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            if raw_text and raw_text in row:
                row = row.replace(raw_text, " ")
            if len(row) <= _ROW_TEXT_MAX:
                date_hint = _row_date(row, explicit_year)
                if date_hint:
                    return date_hint
        node = node.parent
    return ""


def parse_list(html, base_url, domain, max_items=50, stype=None):
    """从栏目列表页抽取相关通知链接。

    返回 [{title, url, date, date_exact}]，只保留同域名、标题命中该栏目领域
    白名单（stype 缺省为研究生教育）、且不是导航/栏目页的链接，按出现顺序去重。
    date 为列表行标出的发布时间（YYYY-MM-DD，取不到为 ""）：详情页常常没有
    任何发布时间标记，列表行的日期才是站点给出的权威发布时间；date_exact 是
    其中带年份的那份（列表只写月日时为空），用于覆盖库内已有的历史日期。
    """
    keyword = TYPE_KEYWORDS.get(stype or "研究生教育", KEYWORDS)
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        raw_text = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        title = clean_notice_title(raw_text)
        href = (a.get("href") or "").strip()
        if not title or len(title) < 6:
            continue
        if title in NAV_TITLES:
            continue
        if _FORM_TITLES.search(title) and not re.search(r"招生|推免|免试", title):
            continue
        url = urljoin(base_url, href)
        if not same_domain(url, domain):
            continue
        if urlparse(url).path in ("", "/"):  # 裸域名根链接（如“xx大学官网”）
            continue
        if any(h in url.lower() for h in _INDEX_URL_HINTS):
            continue
        if not keyword.search(title):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url,
                      "date": _list_item_date(a, raw_text),
                      "date_exact": _list_item_date(a, raw_text,
                                                    explicit_year=True)})
        if len(items) >= max_items:
            break
    return items




_BLOCK_SELECTOR = "p, div, section, li, tr, h1, h2, h3, h4, h5, h6, td, blockquote"


def _extract_block_text(container):
    """按块级元素聚合文本：每个块一行，块内行内文本拼在一起。

    解决 Word 式行内碎片（每个字符一个 <span>）被 get_text("\n") 逐字拆行的问题。
    """
    lines, seen = [], set()
    _LEAF_BLOCKS = ("p", "h1", "h2", "h3", "h4", "h5", "h6",
                    "li", "blockquote", "td", "tr")
    _WRAPPERS = ("div", "section", "article", "ul", "ol", "table",
                 "tbody", "thead", "span", "center")

    def add_block(el):
        t = re.sub(r"[ \t\u3000]+", " ", el.get_text(" ", strip=True)).strip()
        if t and t not in seen:
            seen.add(t)
            lines.append(t)

    def has_leaf(node):
        return node.find(_LEAF_BLOCKS) is not None

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                continue
            name = child.name
            if name == "br":
                continue
            if name in _LEAF_BLOCKS:
                add_block(child)
            elif name in ("table", "tbody", "thead"):
                # 表格：每行一个" | "连接的文本行
                for tr in child.find_all("tr"):
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                    cells = [c for c in cells if c]
                    if cells:
                        line = " | ".join(cells)
                        if line not in seen:
                            seen.add(line)
                            lines.append(line)
            elif name in _WRAPPERS:
                # 优先：先收集容器内的叶子块（避免 span 碎片被 get_text 拆行）
                leaves = child.find_all(_LEAF_BLOCKS)
                if leaves:
                    for lf in leaves:
                        add_block(lf)
                else:
                    add_block(child)

    walk(container)
    return lines




# 常见高校 CMS 的正文容器选择器（按优先级）
_CONTENT_SELECTORS = [
    ".v_news_content", "[id^=vsb_content]", ".wp_articlecontent",
    ".article-content", ".entry-content", ".content-content",
    ".docx", "#content", ".content", ".article", ".main-content",
    ".news-content", ".zsxx-content", ".main_right", ".main-right-content",
]

# 导航菜单行特征：2~8 个字的栏目名
_MENU_LINE = re.compile(r"^[\u4e00-\u9fa5A-Za-z]{2,8}$")


def _find_content_container(soup):
    """定位正文容器：显式选择器 → 启发式最内层最大文本块。

    返回 (container, explicit)：explicit=True 表示命中了显式正文选择器，
    此时即使正文较短也信任该容器（门禁放宽）。
    """
    for sel in _CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el is not None:
            return el, True
    # 启发式：在带 class/id 的候选容器里选"文本量最大"的最内层块
    # 页脚/导航容器特征，跳过
    _NAV_HINT = ("lnk", "nav", "foot", "menu", "header", "banner",
                 "daohang", "link", "friend")
    best, best_score = None, 0
    for el in soup.find_all(["div", "section", "article", "td"]):
        if not (el.get("class") or el.get("id")):
            continue
        cls_str = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
        cls_low = cls_str.lower()
        if any(h in cls_low for h in _NAV_HINT):
            continue
        text = el.get_text(" ", strip=True)
        if len(text) < 150:
            continue
        # 偏好：长段落多 → 正文；短行多 → 菜单。用平均行长打分
        paras = [p for p in el.find_all("p") if len(p.get_text(strip=True)) > 20]
        score = len(text) + sum(len(p.get_text(strip=True)) for p in paras) * 2
        # 更深层的容器略优先（避免选到包住整页的外层）
        depth = len(list(el.parents))
        score += depth * 30
        if score > best_score:
            best, best_score = el, score
    return (best or soup.body or soup), False


def _content_quality_ok(text, min_len=80):
    """正文质量门禁：过滤菜单型/碎片型快照。min_len 可放宽（显式正文容器）。"""
    if not text or len(text) < min_len:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return len(text) >= min_len
    menu_like = sum(1 for ln in lines if _MENU_LINE.match(ln))
    avg_len = sum(len(ln) for ln in lines) / len(lines)
    # 菜单型：一半以上是 2-8 字短行 且 平均行长 < 12
    if menu_like / len(lines) > 0.5 and avg_len < 12:
        return False
    # 碎片型：平均行长 < 4
    if avg_len < 4:
        return False
    return True




# 常见懒加载属性：真实 src 藏在 data-original / data-src / data-lazy-src 等
_LAZY_ATTRS = ("data-original", "data-src", "data-lazy-src", "data-lazyload",
               "data-url", "data-real-src", "file")


def resolve_img_src(img, base_url):
    """从 <img> 取真实 src：优先 data-* 懒加载属性，其次 src。"""
    for attr in _LAZY_ATTRS:
        v = img.get(attr)
        if v and not v.startswith("data:"):
            from urllib.parse import urljoin
            return urljoin(base_url, v.strip())
    src = img.get("src") or ""
    if src and not src.startswith("data:"):
        from urllib.parse import urljoin
        return urljoin(base_url, src.strip())
    return ""


_easyocr_reader = None


def _ocr_image_bytes(img_bytes):
    """OCR 图片文字。优先 easyocr（中文），退回 pytesseract。失败返回空串。"""
    global _easyocr_reader
    import io

    # 1) easyocr
    try:
        if _easyocr_reader is None:
            import easyocr

            _easyocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False,
                                             verbose=False)
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        result = _easyocr_reader.readtext(
            img, detail=0, paragraph=True, text_threshold=0.6)
        if result:
            return "\n".join(result)
    except Exception:  # noqa: BLE001
        pass
    # 2) pytesseract
    try:
        from PIL import Image
        from pytesseract import pytesseract

        img = Image.open(io.BytesIO(img_bytes))
        img.thumbnail((2200, 2200))
        return pytesseract.image_to_string(img, lang="chi_sim") or ""
    except Exception:  # noqa: BLE001
        return ""


def extract_image_text(soup, page_url):
    """检测正文区域内的正文图片并处理。

    双轨：
    1. 下载图片保存到 data/attachments/（信息不丢失）
    2. 尝试 OCR（easyocr → pytesseract）；OCR 可用则附文字，不可用则标注附件路径
    返回拼接文本（限前 3 张）。
    """
    from crawler.fetch import http_get_bytes

    # 找候选正文容器
    container = None
    for sel in _CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) < 500:
            container = el  # 正文容器文本很少 → 疑似图片正文
            break
    if container is None:
        container = soup.body or soup

    imgs = container.find_all("img")
    targets = []
    for im in imgs:
        src = resolve_img_src(im, page_url)
        low = src.lower()
        if "upload" in low or "file" in low or "attach" in low or low.startswith(("http", "/")):
            targets.append(src)
        if len(targets) >= 3:
            break
    if not targets:
        return ""

    # 附件目录：项目 data/attachments
    import hashlib

    from db import store

    att_dir = store.get_db_path().parent / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    for idx, src in enumerate(targets[:3], 1):
        try:
            data = http_get_bytes(src)
            # 保存附件
            ext = Path(src.split("?", 1)[0]).suffix or ".img"
            if len(ext) > 5:
                ext = ".img"
            fname = hashlib.md5(src.encode()).hexdigest()[:12] + ext
            fpath = att_dir / fname
            fpath.write_bytes(data)
            # OCR
            text = _ocr_image_bytes(data)
            if text and len(text.strip()) > 30:
                parts.append(f"【正文图片{idx} OCR】\n{text.strip()[:4000]}")
            else:
                parts.append(f"【正文图片{idx}】已保存附件: {fpath.name}（请打开官方原文查看）")
        except Exception:  # noqa: BLE001
            parts.append(f"【正文图片{idx}】下载失败: {src[:60]}")
    return "\n\n".join(parts).strip()


def _content_img_heavy(soup):
    """判断正文是否疑似"图片型"（正文容器文本 <80 但含大上传图）。"""
    for sel in _CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el is None:
            continue
        t = len(el.get_text(strip=True))
        up = [i for i in el.find_all("img")
              if any(k in (resolve_img_src(i, "") or "").lower()
                     for k in ("upload", "file", "attach"))]
        if t < 80 and up:
            return True
    return False


def _extract_iframe_text(soup, page_url, max_frames=2):
    """从页面内嵌的**同域 iframe** 提取正文（content.jsp 类 CMS 常见）。

    页面正文常被塞进 <iframe>（同站子页），直接解析拿不到。此处逐个抓取
    同域 iframe 内页，再用正文定位逻辑抽取文本，拼接到一起。
    """
    from crawler.fetch import http_get

    frames = [f for f in soup.find_all("iframe") if f.get("src")]
    parts = []
    for f in frames[:max_frames]:
        src = (f.get("src") or "").strip()
        if not src or src.lower().startswith(("javascript:", "data:", "about:")):
            continue
        frame_url = urljoin(page_url, src)
        # 只处理同域 iframe（跨域无权限且多为无关内容）
        if not same_domain(frame_url, urlparse(page_url).netloc):
            continue
        try:
            frame_html = http_get(frame_url, timeout=20, retries=1)
            fsoup = BeautifulSoup(frame_html, "html.parser")
            for tag in fsoup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            fcontainer, _ = _find_content_container(fsoup)
            flines = _extract_block_text(fcontainer) if fcontainer else []
            ftext = "\n".join(flines).strip()
            ftext = re.sub(r"\n{3,}", "\n\n", ftext)
            if ftext and _content_quality_ok(ftext, min_len=60):
                parts.append(f"【内嵌正文（iframe）】\n{ftext[:20000]}")
        except Exception:  # noqa: BLE001
            continue
    return "\n\n".join(parts).strip()


def parse_detail(html, url):
    """抽取详情页的标题、发布时间、正文文本（转近似 Markdown/纯文本快照）。

    返回 {title, published_at, published_src, content_md}。published_src 说明
    发布时间怎么来的：meta/label = 页面自己写明（可信），scan = 页面里扫到的
    第一个合理日期（正文日程已尽量排除，但仍属猜测），"" = 没有发布时间。
    """
    soup = BeautifulSoup(html, "html.parser")

    # 标题：优先 h1，其次 <title>
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    # 正文快照：定位正文容器 → 按块级聚合 → 质量门禁 → PDF 附件兜底
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    _strip_footer_blocks(soup)   # <div class="footer"> 这类不在上面的标签列表里

    # 发布时间：meta 标签 / "发布时间：" 标签 → 页面可见文本里首个合理日期兜底
    published, published_src = _extract_published(html, soup)
    if not published:
        published = _first_plausible_date(soup)
        published_src = "scan" if published else ""
    container, explicit = _find_content_container(soup)
    lines = _extract_block_text(container) if container else []
    if not lines and container is not None:
        # 块级提取失败时兜底：直接取容器全部文本
        raw = container.get_text("\n", strip=True)
        raw = re.sub(r"\n{2,}", "\n", raw)
        if raw.strip():
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > 20000:
        text = text[:20000] + "\n……（正文过长已截断）"
    min_len = 40 if explicit else 80
    if not explicit or len(text) < 200:
        # 正文不可靠或过短时：先尝试同域 iframe（content.jsp 类 CMS 正文嵌在 iframe）
        iframe_text = _extract_iframe_text(soup, url)
        if iframe_text:
            text = (text + "\n\n" + iframe_text).strip()
        # 仍不足时再尝试 PDF 附件/内嵌 PDF 提取
        pdf_text = extract_pdf_text(soup, url)
        if pdf_text and len(pdf_text) > len(text):
            text = (text + "\n\n" + pdf_text).strip()
    if len(text) < 80 and _content_img_heavy(soup):
        # 图片型正文：正文容器文本极少但含大上传图 → OCR
        img_text = extract_image_text(soup, url)
        if img_text:
            text = (text + "\n\n" + img_text).strip()
    if not _content_quality_ok(text, min_len=min_len):
        if not _content_quality_ok(text, min_len=1):
            text = ""  # 质量不达标宁可置空，不留导航垃圾

    return {"title": title, "published_at": published,
            "published_src": published_src, "content_md": text}


def infer_type(title):
    """根据标题打类型标签，用于后续筛选。

    奖助类（奖学金/助学金/资助/补助…）单独成类：这类通知散布在学生处、
    学工部、教务处等多个栏目，按标题识别比按栏目归类更准确。
    """
    if re.search(r"预报名|预推免", title):
        return "预推免"
    if re.search(r"推免|推荐免试|免试|保研|接收", title):
        return "推免"
    if "夏令营" in title:
        return "夏令营"
    if "复试" in title:
        return "复试"
    if "调剂" in title:
        return "调剂"
    if re.search(r"奖学金|助学金|助学贷款|资助|补助|补贴|减免|津贴|"
                 r"勤工助学|困难生|家庭经济困难|经济困难|绿色通道|奖助|"
                 r"评奖评优|三助|生源地信用助学|国家助学|学费代偿", title):
        return "奖助"
    if re.search(r"招生|简章|报考|报名", title):
        return "招生"
    if re.search(r"公示|名单", title):
        return "公示"
    return "通知"

def extract_pdf_text(soup, page_url):
    """从页面里找 PDF 附件，下载并抽取文字（前 2 个附件）。

    覆盖两类：
    1. 普通 <a> 指向 PDF 附件（很多通知正文就一句话"请点击附件查看"）
    2. iframe.wp_pdf_player 内嵌 PDF（南理工 webplus CMS：正文区只有 PDF 播放器）
    """
    from urllib.parse import urljoin, unquote, parse_qs

    links = []

    def _add(u):
        if u and u not in links and len(links) < 2:
            links.append(u)

    # 1) wp_pdf_player 内嵌 PDF（南理工 webplus CMS）
    #    <iframe class="wp_pdf_player" src="..."> 或 <span class="wp_pdf_player" pdfsrc="...">
    for f in soup.find_all(class_="wp_pdf_player"):
        src = f.get("pdfsrc") or f.get("src") or f.get("data-src") or ""
        if not src:
            continue
        if ".pdf" in src.lower():
            _add(urljoin(page_url, src))
            continue
        # 解析 query 参数里的文件路径
        for k in ("filepath", "fileurl", "url", "src", "wbfileid"):
            qs = parse_qs(unquote(src.split("?", 1)[1] if "?" in src else ""))
            if k in qs and qs[k][0]:
                val = qs[k][0]
                _add(urljoin(page_url, val))
                break
        if "download" in src.lower() and not links:
            _add(urljoin(page_url, src))

    # 2) 普通 <a> PDF 附件
    if len(links) < 2:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            hit = (".pdf" in href.lower()
                   or "download.jsp" in href.lower()
                   or ".pdf" in text.lower())
            if hit:
                _add(urljoin(page_url, href))
            if len(links) >= 2:
                break

    if not links:
        return ""
    parts = []
    for pdf_url in links:
        try:
            text = _pdf_to_text(pdf_url)
            if text and len(text) > 50:
                parts.append(f"【附件正文：{pdf_url.rsplit('/', 1)[-1]}】\n{text}")
        except Exception:  # noqa: BLE001
            continue
    out = "\n\n".join(parts).strip()
    return out[:20000] if out else ""


def _pdf_to_text(pdf_url):
    """下载单个 PDF 并抽取文字（pypdf；不可用时退回 pdfminer）。"""
    from crawler.fetch import http_get_bytes

    data = http_get_bytes(pdf_url)
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for pg in reader.pages[:15]:
            try:
                pages.append(pg.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(p for p in pages if p.strip())
    except ImportError:
        pass
    try:
        from pdfminer.high_level import extract_text

        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            return extract_text(tmp) or ""
        finally:
            os.unlink(tmp)
    except ImportError:
        raise RuntimeError("未安装 pypdf（pip install pypdf）")

