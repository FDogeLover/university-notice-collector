# -*- coding: utf-8 -*-
"""库内通知检索：把用户问题匹配到的通知整理成 AI 上下文。

轻量关键词方案（不引入向量/分词依赖）：
1. 识别问题中出现的学校名（用于过滤）
2. 按标点与停用词切分出关键词（如 推免、夏令营、复试名单）
3. SQL LIKE 命中 title / content_md，按命中强度与发布时间排序
4. 取 Top N 摘要后拼装为上下文文本
"""
import re

from crawler.extract import clean_summary  # noqa: E402
from db import store  # noqa: E402

# 段内再切分用的停用词（长词在前，避免被短词截断）
SPLIT_WORDS = (
    "怎么样|怎样|如何|是不是|有没有|能不能|哪些|什么|怎么|多少|"
    "今年|去年|明年|最近|近期|请问|关于|还有|以及|一下|告诉|"
    "介绍|说说|讲讲|看看|知道|想问|情况|信息|数据|库里|数据库|"
    "根据|基于|结合|回答|说明|解释|的|了|吗|呢|吧|啊|和|与|及|或|对|在|是|有"
)
SEG_SPLIT = re.compile(r"(?:%s)+" % SPLIT_WORDS)
PUNCT_SPLIT = re.compile(r"[，。？！、；：“”‘’（）【】《》\s,.?!;:()\[\]<>]+")

# 整段即停用词（不作为检索词）
WHOLE_STOP = {"学校", "大学", "学院", "我", "你", "它", "这", "那"}

# 高频子词：长复合检索词按此拆细（按出现顺序匹配）
SUBWORD_SPLIT = (
    "预报名", "推免", "夏令营", "复试", "调剂", "录取", "名单",
    "公示", "招生", "报名", "通知", "公告", "简章", "初审",
)
_SUB_RE = re.compile("(" + "|".join(SUBWORD_SPLIT) + ")")


def _split_long_term(term):
    """长度 >=5 的检索词，按高频子词拆成多个（保留非空、>=2 字的段）。"""
    if len(term) < 5:
        return [term]
    parts = [p for p in _SUB_RE.split(term) if p and len(p.strip()) >= 2]
    return parts or [term]

# 学校简称 → 配置中的全名（用于识别"北邮"这类俗称）
SCHOOL_ABBRS = {
    "北邮": "北京邮电大学",
    "华师大": "华东师范大学",
    "华科": "华中科技大学",
    "南理工": "南京理工大学",
    "深大": "深圳大学",
}


def extract_terms(question, school_names):
    """返回 (命中的学校列表, 检索词列表)。"""
    q = question.strip()
    hit_schools = []
    for abbr, full in SCHOOL_ABBRS.items():
        if abbr in q and full not in hit_schools:
            hit_schools.append(full)
            q = q.replace(abbr, " ")
    for s in school_names:
        if s in q and s not in hit_schools:
            hit_schools.append(s)
            q = q.replace(s, " ")

    terms = []
    for seg in PUNCT_SPLIT.split(q):
        for part in SEG_SPLIT.split(seg):
            part = part.strip()
            if len(part) < 2 or part in WHOLE_STOP:
                continue
            # 长复合词按高频子词拆细（如"推免预报名"→"推免"+"预报名"），
            # 避免 LIKE 连续串匹配不到"推荐免试…预报名"这类官方表述
            for sub in _split_long_term(part):
                if sub not in terms:
                    terms.append(sub)
    return hit_schools, terms[:6]


def _score(row, terms):
    title = row["title"] or ""
    content = row["content_md"] or ""
    s = 0
    for t in terms:
        if t in title:
            s += 3
        s += min(content.count(t), 5)
    return s


def search_notices(question, limit=8):
    """检索与问题相关的通知，按相关度+发布时间排序。"""
    conn = store.connect()
    try:
        schools = [r["name"] for r in conn.execute("SELECT name FROM schools")]
        hit_schools, terms = extract_terms(question, schools)
        if not terms and not hit_schools:
            return []

        base = (
            "SELECT n.id, n.title, n.url, n.type_tag, n.published_at,"
            " sc.name AS school_name, COALESCE(n.content_md,'') AS content_md "
            "FROM notices n JOIN schools sc ON sc.id=n.school_id WHERE "
        )
        if not terms:
            # 只有学校命中：返回该校最新通知
            marks = ",".join("?" * len(hit_schools))
            sql = base + f"sc.name IN ({marks}) ORDER BY n.published_at DESC"
            rows = [dict(r) for r in conn.execute(sql, hit_schools).fetchall()]
            return rows[:limit]

        conds, params = [], []
        for t in terms:
            like = f"%{t}%"
            conds.append("(n.title LIKE ? OR COALESCE(n.content_md,'') LIKE ?)")
            params += [like, like]
        where = " OR ".join(conds)
        school_filter, school_params = "", []
        if hit_schools:
            marks = ",".join("?" * len(hit_schools))
            school_filter = f" AND sc.name IN ({marks})"
            school_params = list(hit_schools)
        sql = base + f"({where}){school_filter}"
        rows = [dict(r) for r in conn.execute(sql, params + school_params).fetchall()]
    finally:
        conn.close()

    for r in rows:
        r["score"] = _score(r, terms)
    rows.sort(key=lambda r: (r["score"], r["published_at"] or ""), reverse=True)
    return rows[:limit]


def _keyword_excerpt(content, terms, max_len=500):
    """关键词窗口摘要：截取关键词首次命中位置前后的片段。

    与"只取开头 max_len 字"相比，能把正文中部/尾部的关键细节
    （报名时间、条件、材料等）带进上下文。标题不重复拼接
    （结构化行里已有）。
    """
    text = (content or "").strip()
    if not text:
        return ""
    pos = -1
    for t in terms:
        p = text.find(t)
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    if pos == -1:
        # 关键词只在标题里命中：退回取正文开头
        return text[:max_len]
    start = max(0, pos - 80)
    end = min(len(text), start + max_len)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet



def build_context(question, max_notices=8, excerpt_len=500):
    """返回 (上下文文本, 命中条数)。无命中返回 ("", 0)。

    相关度门槛：标题或正文至少有实质命中（score>=3）才纳入，
    过滤"正文顺带提了一次关键词"的弱相关通知。
    """
    rows = search_notices(question, limit=max_notices * 2)
    if not rows:
        return "", 0
    _, terms = extract_terms(question, [])
    rows = [r for r in rows if r["score"] >= 3][:max_notices]
    if not rows:
        return "", 0
    lines = []
    for i, r in enumerate(rows, 1):
        excerpt = _keyword_excerpt(r["content_md"], terms, max_len=excerpt_len)
        if not excerpt:
            excerpt = "（无正文快照，仅标题）"
        pub = r["published_at"] or "时间未知"
        lines.append(
            f"[{i}] 学校：{r['school_name']} | 标题：{r['title']}"
            f" | 类型：{r['type_tag'] or '未分类'} | 发布：{pub}\n"
            f"    要点：{excerpt}\n"
            f"    原文：{r['url']}"
        )
    ctx = (
        f"以下是从本项目数据库检索到的 {len(rows)} 条高校通知"
        f"（按相关度排序），请优先依据它们回答用户问题：\n\n"
        + "\n\n".join(lines)
    )
    return ctx, len(rows)
