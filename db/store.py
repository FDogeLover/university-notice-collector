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
    """建表（幂等，可反复执行）。"""
    own = conn is None
    conn = conn or connect()
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    if own:
        conn.close()


def import_schools(conn, schools):
    """把 yaml 中的学校与栏目写入数据库（已存在则更新）。"""
    for s in schools:
        conn.execute(
            "INSERT INTO schools(name, domain) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET domain=excluded.domain",
            (s["name"], s.get("domain")),
        )
        school_id = conn.execute(
            "SELECT id FROM schools WHERE name=?", (s["name"],)
        ).fetchone()["id"]
        for src in s.get("sources", []):
            conn.execute(
                "INSERT INTO sources(school_id, name, url, category) VALUES(?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET "
                "school_id=excluded.school_id, name=excluded.name, "
                "category=excluded.category",
                (school_id, src.get("name"), src["url"], src.get("category")),
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


def school_id_by_name(conn, name):
    row = conn.execute("SELECT id FROM schools WHERE name=?", (name,)).fetchone()
    return row["id"] if row else None


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
