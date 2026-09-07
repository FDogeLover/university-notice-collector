# -*- coding: utf-8 -*-
"""清理库内乱码正文：把"附件二进制被当正文"的 content_md 置空。

判定：正文含 U+FFFD 替换符，或前 300 字符内出现不可打印控制字符
（非 \n\r\t 且码位 < 32，这是 PDF/Word 二进制特征）。
附件类通知的标题与原文链接保留，正文清空后由前端提示"请打开官方原文"。
用法：python scripts/clean_garbled.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import store  # noqa: E402


def is_garbled(content):
    if not content:
        return False
    head = content[:300]
    if "\ufffd" in head:
        return True
    return any(ord(ch) < 32 and ch not in "\n\r\t" for ch in head)


def main():
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, title, url, content_md FROM notices").fetchall()
    n = 0
    for r in rows:
        if is_garbled(r["content_md"]):
            conn.execute(
                "UPDATE notices SET content_md=NULL WHERE id=?", (r["id"],))
            conn.execute("DELETE FROM notice_meta WHERE notice_id=?",
                         (r["id"],))
            n += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"]
    print(f"清理完成：{n} 条乱码正文已置空（保留标题与链接），共 {total} 条通知。")
    conn.close()


if __name__ == "__main__":
    main()
