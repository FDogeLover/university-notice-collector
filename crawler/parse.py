# -*- coding: utf-8 -*-
"""解析层：列表页抽链接、详情页抽标题/时间/正文、类型打标。"""
import re

# 抑制 pypdf 对无效 PDF（下载到 HTML）的无害警告
import logging
logging.getLogger("pypdf").setLevel(logging.CRITICAL)
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString

# 内容标题关键词：命中才视为有效通知（过滤导航/杂项链接）
KEYWORDS = re.compile(
    r"(推免|推荐免试|预推免|夏令营|直博|保研|招生|简章|报考|报名|"
    r"通知|公告|公示|复试|调剂|录取|免试|优秀应届|研究生|博士|硕士|"
    r"申请|办法|名单|日程|宣讲|接收)",
    re.I,
)


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


# 页面里的"发布时间/发布日期：yyyy年M月d日"式标签（比全文首个日期更可信）
_PUBLISHED_LABEL_RE = re.compile(
    r"(?:发布时间|发布日期|发表时间|发布于|信息发布)"
    r"[:：]?\s*(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")

# 列表页标题的 CMS 序号垃圾："082026.05吉林大学…" / "…重要2025.11.24"
_TITLE_JUNK_LEADING = re.compile(r"^\d{1,6}(?:\.\d{1,2}){1,2}(?=[\u4e00-\u9fa5])")
_TITLE_JUNK_TRAILING = re.compile(r"[.\d]{6,}$")

# 常见发布时间 meta 标签名（小写比较）
_PUBLISHED_META_KEYS = {
    "pubdate", "publishdate", "pub_date", "date", "dc.date",
    "article:published_time", "og:published_time", "publishdateidentifier",
}


def _extract_published(html, soup):
    """提取发布时间：meta 标签 → "发布时间：" 标签 → 全文首个日期。"""
    for m in soup.find_all("meta"):
        key = (m.get("name") or m.get("property") or "").strip().lower()
        if key not in _PUBLISHED_META_KEYS:
            continue
        dm = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})",
                       (m.get("content") or ""))
        if dm:
            return f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    for regex in (_PUBLISHED_LABEL_RE,):
        m = regex.search(html)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def parse_list(html, base_url, domain, max_items=50):
    """从栏目列表页抽取相关通知链接。

    返回 [{title, url}]，只保留同域名、标题含关键词、且不是导航/栏目页的链接，
    按出现顺序去重。
    """
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        title = (a.get_text(strip=True) or "").strip()
        # 清理 CMS 序号垃圾（如 "082026.05吉林大学…" / "…重要2025.11.24"）
        title = _TITLE_JUNK_LEADING.sub("", title)
        title = _TITLE_JUNK_TRAILING.sub("", title).strip()
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
        if not KEYWORDS.search(title):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url})
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
    """抽取详情页的标题、发布时间、正文文本（转近似 Markdown/纯文本快照）。"""
    soup = BeautifulSoup(html, "html.parser")

    # 标题：优先 h1，其次 <title>
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    # 发布时间：meta 标签 / "发布时间：" 标签 → 全文首个日期兜底
    published = _extract_published(html, soup)
    if not published:
        m = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", html)
        if m:
            published = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 正文快照：定位正文容器 → 按块级聚合 → 质量门禁 → PDF 附件兜底
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
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

    return {"title": title, "published_at": published, "content_md": text}


def infer_type(title):
    """根据标题打类型标签，用于后续筛选。"""
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

