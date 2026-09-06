# -*- coding: utf-8 -*-
"""批量添加 985/211 高校采集配置：AI 智能填写 × 官方域名校验，写入 schools.yaml 并入库。

用法（项目根目录执行）：
    python add_985_211.py            # 补齐缺失学校（可重复执行，自动跳过已有）
    python add_985_211.py --list     # 只打印名单与当前覆盖情况

说明：
- 复用前端"AI 智能填写"的同一套提示词与校验（栏目 URL 必须在官方主域名下）；
- 校名以本清单为准（避免 AI 返回简称/别名），tags 写入 yaml 与 schools.tags；
- 每成功一所立即写盘入库，中断后重跑即续；
- 军事院校（国防科大/军医大学）站点特殊，不在采集范围。
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "config" / "schools.yaml"
CONCURRENCY = 3

# 985/211 全名单（排除军事院校）；值为标签
SCHOOLS_985_211 = [
    # ---- 985（38 所，不含国防科技大学）----
    ("清华大学", "985"), ("北京大学", "985"), ("中国人民大学", "985"),
    ("北京航空航天大学", "985"), ("北京理工大学", "985"), ("北京师范大学", "985"),
    ("中国农业大学", "985"), ("中央民族大学", "985"), ("南开大学", "985"),
    ("天津大学", "985"), ("大连理工大学", "985"), ("东北大学", "985"),
    ("吉林大学", "985"), ("哈尔滨工业大学", "985"), ("复旦大学", "985"),
    ("同济大学", "985"), ("上海交通大学", "985"), ("华东师范大学", "985"),
    ("南京大学", "985"), ("东南大学", "985"), ("浙江大学", "985"),
    ("中国科学技术大学", "985"), ("厦门大学", "985"), ("山东大学", "985"),
    ("中国海洋大学", "985"), ("武汉大学", "985"), ("华中科技大学", "985"),
    ("湖南大学", "985"), ("中南大学", "985"), ("中山大学", "985"),
    ("华南理工大学", "985"), ("四川大学", "985"), ("电子科技大学", "985"),
    ("重庆大学", "985"), ("西安交通大学", "985"), ("西北工业大学", "985"),
    ("兰州大学", "985"), ("西北农林科技大学", "985"),
    # ---- 211（非 985）----
    ("北京交通大学", "211"), ("北京工业大学", "211"), ("北京科技大学", "211"),
    ("北京化工大学", "211"), ("北京邮电大学", "211"), ("北京林业大学", "211"),
    ("北京中医药大学", "211"), ("北京外国语大学", "211"), ("中国传媒大学", "211"),
    ("中央财经大学", "211"), ("对外经济贸易大学", "211"), ("中国政法大学", "211"),
    ("华北电力大学", "211"), ("中国矿业大学（北京）", "211"),
    ("中国石油大学（北京）", "211"), ("中国地质大学（北京）", "211"),
    ("天津医科大学", "211"), ("河北工业大学", "211"), ("太原理工大学", "211"),
    ("内蒙古大学", "211"), ("辽宁大学", "211"), ("大连海事大学", "211"),
    ("延边大学", "211"), ("东北师范大学", "211"), ("哈尔滨工程大学", "211"),
    ("东北农业大学", "211"), ("东北林业大学", "211"), ("华东理工大学", "211"),
    ("东华大学", "211"), ("上海外国语大学", "211"), ("上海财经大学", "211"),
    ("上海大学", "211"), ("苏州大学", "211"), ("南京航空航天大学", "211"),
    ("南京理工大学", "211"), ("河海大学", "211"), ("江南大学", "211"),
    ("南京农业大学", "211"), ("中国药科大学", "211"), ("南京师范大学", "211"),
    ("中国矿业大学", "211"), ("中国石油大学（华东）", "211"),
    ("安徽大学", "211"), ("合肥工业大学", "211"), ("福州大学", "211"),
    ("南昌大学", "211"), ("郑州大学", "211"), ("武汉理工大学", "211"),
    ("华中农业大学", "211"), ("华中师范大学", "211"),
    ("中南财经政法大学", "211"), ("中国地质大学（武汉）", "211"),
    ("湖南师范大学", "211"), ("暨南大学", "211"), ("华南师范大学", "211"),
    ("广西大学", "211"), ("海南大学", "211"), ("四川农业大学", "211"),
    ("西南交通大学", "211"), ("西南财经大学", "211"), ("贵州大学", "211"),
    ("云南大学", "211"), ("西藏大学", "211"), ("西北大学", "211"),
    ("西安电子科技大学", "211"), ("长安大学", "211"), ("陕西师范大学", "211"),
    ("青海大学", "211"), ("宁夏大学", "211"), ("新疆大学", "211"),
    ("石河子大学", "211"), ("中央音乐学院", "211"),
]


def load_yaml():
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return data.get("schools") or []


def save_yaml(school_list):
    """保留文件头注释后整体写回。"""
    raw = CONFIG.read_text(encoding="utf-8").splitlines()
    header = []
    for line in raw:
        if line.startswith("#"):
            header.append(line)
        else:
            break
    data = {"schools": school_list}
    CONFIG.write_text(
        "\n".join(header) + "\n" + yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8")


async def ai_fill(cfg, name, sem):
    """AI 生成单校配置（复用 web.app 的提示词与校验），校名以清单为准。"""
    from web.app import AI_SCHOOL_PROMPT, _clean_ai_school, _extract_json_obj
    from web.ai_client import AIError, chat_once

    async with sem:
        last_err = None
        for attempt in range(3):
            try:
                text = await chat_once(
                    cfg, [{"role": "user", "content": f"大学名称/线索：{name}"}],
                    system=AI_SCHOOL_PROMPT)
                obj = _extract_json_obj(text)
                school, errors = _clean_ai_school(obj)
                if errors:
                    last_err = "；".join(errors)
                else:
                    school["name"] = name  # 校名以本清单为准
                    return school, None
            except (AIError, Exception) as e:  # noqa: BLE001
                last_err = str(e)[:80]
            await asyncio.sleep(2 * (attempt + 1))
        return None, f"{name}: {last_err}"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印覆盖情况")
    args = ap.parse_args()

    school_list = load_yaml()
    by_name = {s["name"]: s for s in school_list}

    if args.list:
        for name, tag in SCHOOLS_985_211:
            mark = "✓" if name in by_name else "✗"
            print(f"  [{mark}] {tag}  {name}")
        missing = [n for n, _ in SCHOOLS_985_211 if n not in by_name]
        print(f"共 {len(SCHOOLS_985_211)} 所（不含军校），缺失 {len(missing)} 所")
        return

    # 1) 给已有学校补 tags
    tags_map = {n: t for n, t in SCHOOLS_985_211}
    changed = False
    for s in school_list:
        tag = tags_map.get(s["name"])
        want = [tag] if tag else None
        if (s.get("tags") or None) != want:
            if want:
                s["tags"] = want
            else:
                s.pop("tags", None)
            changed = True
    if changed:
        save_yaml(school_list)

    # 2) AI 生成缺失学校
    missing = [(n, t) for n, t in SCHOOLS_985_211 if n not in by_name]
    print(f"缺失 {len(missing)} 所，开始 AI 智能填写（并发 {CONCURRENCY}）…")
    if not missing:
        return

    from web.ai_client import load_config
    from db import store
    store.init_db()  # 幂等迁移（如 schools.tags 列）
    cfg = load_config()
    sem = asyncio.Semaphore(CONCURRENCY)
    ok, fail = 0, []

    async def worker(name, tag):
        nonlocal ok
        school, err = await ai_fill(cfg, name, sem)
        cur = load_yaml()
        if school:
            school["tags"] = [tag]
            cur.append(school)
            save_yaml(cur)
            conn = store.connect()
            try:
                store.import_schools(conn, [school])
            finally:
                conn.close()
            ok += 1
            src_n = len(school.get("sources") or [])
            print(f"  [{ok}] + {name}（{src_n} 个栏目，{school.get('domain')}）")
        else:
            fail.append(err)
            print(f"  ! 失败 {err}")

    await asyncio.gather(*(worker(n, t) for n, t in missing))

    # 3) 全量幂等导入（含中断续跑时 yaml 已有但库缺失的学校）
    conn = store.connect()
    try:
        store.import_schools(conn, load_yaml())
        total = conn.execute("SELECT COUNT(*) c FROM schools").fetchone()["c"]
    finally:
        conn.close()
    print(f"\n完成：本轮新增 {ok} 所，失败 {len(fail)} 所；库内学校共 {total} 所")
    for f in fail:
        print("  !", f)
    if fail:
        print("（失败学校可重跑本脚本重试）")


if __name__ == "__main__":
    asyncio.run(main())
