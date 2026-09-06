# -*- coding: utf-8 -*-
"""数据库层：建表、写库、查询。
数据库文件默认位于项目根/data/university.db，可用环境变量 UNIV_DB 覆盖路径。
"""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "university.db"


def get_db_path():
    return Path(os.environ.get("UNIV_DB", DEFAULT_DB))


def connect(db_path=None):
    db_path = Path(db_path) if db_path else get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn=None):
    """建表（幂等，可反复执行），并做轻量迁移与孤儿数据清理。"""
    own = conn is None
    conn = conn or connect()
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    _migrate(conn)
    _cleanup_orphans(conn)
    conn.commit()
    if own:
        conn.close()


def _migrate(conn):
    """存量库升级：schools 补 enabled/tags 列；sources 补 stype 列；清洗标题垃圾。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schools)")}
    if "enabled" not in cols:
        conn.execute(
            "ALTER TABLE schools ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if "tags" not in cols:
        conn.execute("ALTER TABLE schools ADD COLUMN tags TEXT")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
    if "stype" not in scols:
        conn.execute(
            "ALTER TABLE sources ADD COLUMN stype TEXT NOT NULL "
            "DEFAULT '研究生教育'")
    from crawler.extract import clean_notice_title  # 延迟导入避免循环依赖

    for r in conn.execute("SELECT id, title FROM notices").fetchall():
        clean = clean_notice_title(r["title"])
        if clean and clean != r["title"]:
            conn.execute("UPDATE notices SET title=? WHERE id=?",
                         (clean, r["id"]))


def _cleanup_orphans(conn):
    """清理指向已不存在学校的栏目（如历史测试残留）。"""
    conn.execute(
        "DELETE FROM sources WHERE school_id NOT IN (SELECT id FROM schools)")


def import_schools(conn, schools):
    """把 yaml 中的学校与栏目写入数据库（已存在则更新）。"""
    for s in schools:
        tags = ",".join(s["tags"]) if s.get("tags") else None
        conn.execute(
            "INSERT INTO schools(name, domain, tags) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET domain=excluded.domain, "
            "tags=COALESCE(excluded.tags, schools.tags)",
            (s["name"], s.get("domain"), tags),
        )
        school_id = conn.execute(
            "SELECT id FROM schools WHERE name=?", (s["name"],)
        ).fetchone()["id"]
        for src in s.get("sources", []):
            # stype 缺省：新栏目落'研究生教育'；已存在栏目保留原值不回退
            conn.execute(
                "INSERT INTO sources(school_id, name, url, category, stype) "
                "VALUES(?,?,?,?,COALESCE(?, '研究生教育')) "
                "ON CONFLICT(url) DO UPDATE SET "
                "school_id=excluded.school_id, name=excluded.name, "
                "category=excluded.category, "
                "stype=COALESCE(?, sources.stype)",
                (school_id, src.get("name"), src["url"], src.get("category"),
                 src.get("stype"), src.get("stype")),
            )
    conn.commit()


def insert_notice(conn, school_id, source_id, title, url, content_md=None,
                  published_at=None, type_tag=None):
    """按 url 去重写入通知。返回 (notice_id, is_new)。"""
    try:
        cur = conn.execute(
            "INSERT INTO notices(school_id, source_id, title, url, content_md, "
            "published_at, type_tag) VALUES(?,?,?,?,?,?,?)",
            (school_id, source_id, title, url, content_md, published_at, type_tag),
        )
        conn.commit()
        return cur.lastrowid, True
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id FROM notices WHERE url=?", (url,)).fetchone()
        return (row["id"] if row else None), False


def log_fetch(conn, source_id, school_id, status, new_count, message=""):
    conn.execute(
        "INSERT INTO fetch_logs(source_id, school_id, status, new_count, message) "
        "VALUES(?,?,?,?,?)",
        (source_id, school_id, status, new_count, message),
    )
    conn.commit()


def upsert_notice_meta(conn, notice_id, field_name, field_value):
    """写入/更新通知的结构化字段（截止日期、学院等）。"""
    conn.execute(
        "INSERT INTO notice_meta(notice_id, field_name, field_value) VALUES(?,?,?) "
        "ON CONFLICT(notice_id, field_name) DO UPDATE SET "
        "field_value=excluded.field_value",
        (notice_id, field_name, str(field_value)),
    )
    conn.commit()


def notice_meta_map(conn, notice_id):
    """单条通知的结构化字段，如 {"deadline": "9月30日", ...}。"""
    rows = conn.execute(
        "SELECT field_name, field_value FROM notice_meta WHERE notice_id=?",
        (notice_id,),
    ).fetchall()
    return {r["field_name"]: r["field_value"] for r in rows}


def notice_meta_maps(conn, notice_ids):
    """批量取多条通知的结构化字段：{notice_id: {field: value}}。"""
    ids = [i for i in notice_ids if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT notice_id, field_name, field_value FROM notice_meta "
        f"WHERE notice_id IN ({marks})",
        ids,
    ).fetchall()
    maps = {}
    for r in rows:
        maps.setdefault(r["notice_id"], {})[r["field_name"]] = r["field_value"]
    return maps


def school_id_by_name(conn, name):
    row = conn.execute("SELECT id FROM schools WHERE name=?", (name,)).fetchone()
    return row["id"] if row else None


def enabled_school_names(conn):
    """启用中的学校名集合（停用学校不采集、不显示，但数据保留）。"""
    return {r["name"] for r in
            conn.execute("SELECT name FROM schools WHERE enabled=1")}


def set_school_enabled(conn, school_id, enabled):
    conn.execute("UPDATE schools SET enabled=? WHERE id=?",
                 (1 if enabled else 0, school_id))
    conn.commit()


def delete_school(conn, school_id):
    """彻底删除学校：连同其栏目、通知、结构化字段一并移除（不可恢复）。"""
    conn.execute(
        "DELETE FROM notice_meta WHERE notice_id IN "
        "(SELECT id FROM notices WHERE school_id=?)", (school_id,))
    conn.execute("DELETE FROM notices WHERE school_id=?", (school_id,))
    conn.execute("DELETE FROM sources WHERE school_id=?", (school_id,))
    conn.execute("DELETE FROM schools WHERE id=?", (school_id,))
    conn.commit()


def list_sources(conn, school_id=None, enabled_only=True):
    sql = ("SELECT s.*, sc.name AS school_name FROM sources s "
           "JOIN schools sc ON sc.id = s.school_id")
    conds, args = [], []
    if school_id:
        conds.append("s.school_id=?")
        args.append(school_id)
    if enabled_only:
        conds.append("s.enabled=1")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY sc.id, s.id"
    return conn.execute(sql, args).fetchall()


def search(conn, keyword, school_name=None, limit=50):
    """关键词检索（LIKE 子串匹配，兼容中文）。每条都带官方原文链接，可溯源。"""
    sql = ("SELECT n.*, sc.name AS school_name, s.name AS source_name "
           "FROM notices n JOIN schools sc ON sc.id = n.school_id "
           "LEFT JOIN sources s ON s.id = n.source_id "
           "WHERE (n.title LIKE ? OR COALESCE(n.content_md,'') LIKE ?)")
    args = [f"%{keyword}%", f"%{keyword}%"]
    if school_name:
        sql += " AND sc.name=?"
        args.append(school_name)
    sql += " ORDER BY n.fetched_at DESC, n.id DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def recent(conn, school_name=None, limit=50):
    """最近入库的通知。"""
    sql = ("SELECT n.*, sc.name AS school_name, s.name AS source_name "
           "FROM notices n JOIN schools sc ON sc.id = n.school_id "
           "LEFT JOIN sources s ON s.id = n.source_id")
    conds, args = [], []
    if school_name:
        conds.append("sc.name=?")
        args.append(school_name)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY n.fetched_at DESC, n.id DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()
