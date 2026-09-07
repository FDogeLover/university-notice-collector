# -*- coding: utf-8 -*-
"""每日采集质量复盘：驱动 hermes 分析当日采集日志与巡检报告。

- 只读分析 + 给建议：hermes 不修改任何配置/代码，只产出复盘报告
- 报告存 logs/review-<日期>.md；经验教训写入项目 MEMORY.md 供 hermes 长期积累
- 用法：python scripts/daily_review.py [--since 'YYYY-MM-DD HH:MM']
  由 cron 在每日采集后调用（如 05:20）
"""
import argparse
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
HERMES = "/usr/local/lib/hermes-agent/.venv/bin/hermes"


def tail_log(path, max_lines=400):
    """取日志末尾若干行（当天采集输出）。"""
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def recent_inspection():
    """最近的巡检报告文件名与内容片段。"""
    files = sorted(LOG_DIR.glob("inspection-*.txt"))
    if not files:
        return ""
    return files[-1].name + "\n" + files[-1].read_text(
        encoding="utf-8", errors="replace")[:1500]


def run_review(since):
    crawl_tail = tail_log(LOG_DIR / "cron-crawl.log")
    inspect = recent_inspection()
    if not crawl_tail:
        print("cron-crawl.log 为空，跳过复盘")
        return

    prompt = f"""你是高校信息采集项目的质量复盘员。今天是 {date.today().isoformat()}。

请复盘「大学信息收集」项目最近一次的自动采集（cron 采集自 {since} 起）。
只读分析，不要修改任何文件。任务：
1. 读 logs/cron-crawl.log 末尾与巡检报告，找出采集中的问题（抓取失败、栏目零通知、疑似反爬、URL 错误等）
2. 分类归纳：致命问题 / 待观察 / 正常，给出每类条数与典型例子
3. 对每个可改进点给出具体建议（改哪个 schools.yaml 栏目、是否需 browser 标记、URL 是否需核实等）
4. 把可复用的经验教训追加到项目 MEMORY.md 的「采集经验」一节（若该节不存在则创建），
   用简短的教训条目，例如「XX大学的正确域名是 yy.edu.cn」之类，避免重复已有条目

采集日志末尾（最近采集输出）：
-----
{crawl_tail[-6000:]}
-----

最近巡检报告：
-----
{inspect[:2000]}
-----

输出格式：
## 复盘总结
### 致命问题 / 待观察 / 正常
### 具体建议（可执行条目）
### 今日教训（已写入/待写入 MEMORY.md）
保持简洁，用中文。"""
    # 教训积累：报告尾部追加当日教训（供人工/后续抽取）
    lessons_file = LOG_DIR / "lessons.md"
    try:
        if not lessons_file.exists():
            lessons_file.write_text("# 采集经验教训积累\n\n",
                                    encoding="utf-8")
        r = subprocess.run(
            [HERMES, "-z", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=1200, cwd=ROOT,
        )
        body = (r.stdout or "") + ("\n[stderr]\n" + r.stderr[-2000:]
                                   if r.stderr else "")
        out.write_text(body, encoding="utf-8")
        print(f"复盘完成 → {out}（{len(body)} 字符）")
        # 追加到教训文件（去重：同一天只写一次）
        stamp = date.today().isoformat()
        if stamp not in lessons_file.read_text(encoding="utf-8"):
            lessons_file.write_text(
                f"\n## {stamp}\n" + body + "\n", encoding="utf-8")
    except subprocess.TimeoutExpired:
        out.write_text("复盘超时（1200s）", encoding="utf-8")
        print("复盘超时")
    except Exception as e:  # noqa: BLE001
        out.write_text(f"复盘失败：{e}", encoding="utf-8")
        print(f"复盘失败：{e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None,
                    help="采集起始时间，默认昨日 03:30")
    args = ap.parse_args()
    since = args.since or (datetime.now() - timedelta(days=1)
                           ).strftime("%Y-%m-%d 03:30")
    run_review(since)


if __name__ == "__main__":
    main()
