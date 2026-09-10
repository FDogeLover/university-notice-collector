# -*- coding: utf-8 -*-
"""把 config/schools.yaml 的栏目配置同步到数据库（就地修正，不产生幽灵）。

- 匹配优先级：同校 + 同 URL → 同校 + 同栏目名 → 新增
- yaml 里消失的栏目：若无通知则删除，有通知则保留（避免丢历史数据）
- 用法：python scripts/sync_sources.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from db import store  # noqa: E402

CONFIG = ROOT / "config" / "schools.yaml"


def main():
    store.init_db()
    conn = store.connect()
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    updated = added = removed = 0

    for s in cfg.get("schools", []):
        row = conn.execute("SELECT id FROM schools WHERE name=?",
                           (s["name"],)).fetchone()
        if not row:
            continue
        school_id = row["id"]
        want = {src["url"]: src for src in s.get("sources", [])}
        have = {r["url"]: r["id"] for r in conn.execute(
            "SELECT id, url FROM sources WHERE school_id=?", (school_id,))}

        # 1) 已存在的 URL：更新元数据
        for url, src in want.items():
            if url in have:
                conn.execute(
                    "UPDATE sources SET name=?, category=?, "
                    "stype=COALESCE(?, stype) WHERE id=?",
                    (src.get("name"), src.get("category"),
                     src.get("stype"), have[url]))

        # 2) 同栏目名但 URL 变化 → 就地迁移（保留通知关联）
        for url, src in want.items():
            if url in have:
                continue
            old = conn.execute(
                "SELECT id FROM sources WHERE school_id=? AND name=? "
                "AND url != ?", (school_id, src.get("name"), url)).fetchone()
            if old:
                conn.execute(
                    "UPDATE sources SET url=?, category=?, "
                    "stype=COALESCE(?, stype) WHERE id=?",
                    (url, src.get("category"), src.get("stype"), old["id"]))
                have[url] = old["id"]
                updated += 1

        # 3) 仍缺失 → 新增
        for url, src in want.items():
            if url in have:
                continue
            conn.execute(
                "INSERT INTO sources(school_id, name, url, category, stype) "
                "VALUES(?,?,?,?,COALESCE(?, '研究生教育'))",
                (school_id, src.get("name"), url, src.get("category"),
                 src.get("stype")))
            added += 1

        # 4) yaml 中已消失的栏目：无通知则删，有通知保留
        for url, sid in have.items():
            if url in want:
                continue
            n = conn.execute("SELECT COUNT(*) c FROM notices WHERE source_id=?",
                             (sid,)).fetchone()["c"]
            if n == 0:
                conn.execute("DELETE FROM sources WHERE id=?", (sid,))
                removed += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
    print(f"同步完成：更新 {updated}、新增 {added}、清理 {removed}；栏目总数 {total}")
    conn.close()


if __name__ == "__main__":
    main()
