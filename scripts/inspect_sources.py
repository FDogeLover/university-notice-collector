# -*- coding: utf-8 -*-
"""失败/零通知栏目巡检：生成报告到 logs/inspection-<日期>.txt。

用法：python scripts/inspect_sources.py
配合 cron 每周自动巡检；报告中"零通知"栏目优先核查 URL，
"连续失败"栏目常见原因为反爬升级或站点迁移。
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import store  # noqa: E402


def main():
    conn = store.connect()
    rows = conn.execute(
        "SELECT sc.name AS school, sc.tags, s.name AS src, s.url, s.enabled,"
        "       (SELECT COUNT(*) FROM notices n WHERE n.source_id=s.id) AS cnt,"
        "       (SELECT COUNT(*) FROM fetch_logs f WHERE f.source_id=s.id"
        "        AND f.status='error') AS errs,"
        "       (SELECT MAX(f.run_at) FROM fetch_logs f"
        "        WHERE f.source_id=s.id) AS last_run "
        "FROM sources s JOIN schools sc ON sc.id=s.school_id "
        "WHERE sc.enabled=1 ORDER BY errs DESC, cnt ASC"
    ).fetchall()
    conn.close()

    lines = [f"栏目巡检报告 {date.today().isoformat()}"]
    bad = 0
    for r in rows:
        if r["cnt"] == 0 or r["errs"] > 0:
            bad += 1
            lines.append(
                f"[零通知={r['cnt']} 失败{r['errs']}次] {r['school']}"
                f"（{r['tags'] or '无标签'}） | {r['src']} | {r['url']}"
                f" | 最近尝试 {r['last_run']}")
    lines.append(f"\n问题栏目 {bad} / {len(rows)}；建议：先核对 URL，"
                 f"再视反爬情况加 browser 标记重采")

    out = ROOT / "logs" / f"inspection-{date.today().isoformat()}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"问题栏目 {bad} / {len(rows)}，报告：{out}")


if __name__ == "__main__":
    main()
