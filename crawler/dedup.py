# -*- coding: utf-8 -*-
"""去重层：URL 精确去重 + 标题/发布时间指纹去重（应对同一通知改链接）。"""
from db import store


def exists_by_url(conn, url):
    row = conn.execute("SELECT id FROM notices WHERE url=?", (url,)).fetchone()
    return bool(row)


def find_by_fingerprint(conn, school_id, title, published_at):
    """按 学校 + 标题 + 发布时间 指纹查找，返回已存在的 notice_id 或 None。"""
    if not title or not published_at:
        return None
    row = conn.execute(
        "SELECT id FROM notices WHERE school_id=? AND title=? AND published_at=?",
        (school_id, title, published_at),
    ).fetchone()
    return row["id"] if row else None


def should_skip(conn, school_id, url, title, published_at):
    """判定某条是否已入库（url 精确匹配或指纹匹配即跳过）。"""
    if store and exists_by_url(conn, url):
        return True
    return find_by_fingerprint(conn, school_id, title, published_at) is not None
