# -*- coding: utf-8 -*-
"""按审计/复盘清单批量修栏目：换址、加 browser/real_browser/stype 标记、删无价值栏目。

为什么行级编辑而不是 yaml.safe_dump 重写：`config/schools.yaml` 是 111 校
667 栏目的唯一权威副本，文件里有注释与空行，整文件重写会丢注释、也会把
diff 冲成整个文件（评审时看不出改了什么）。这里按"学校块 → 栏目块"定位，
只动需要动的行，和 scripts/find_list_url.py:apply_url 一个路子。

安全约束：
- 换址一律先用 parse_list 复测（解析条数 > 0 才算通过），过不了就不改，
  与项目"HTTP 200 不算数"的既有判定口径一致；
- 删栏目只删 yaml 条目：库里的历史通知由 scripts/sync_sources.py 决定去留
  （无通知才删源，有通知保源），不会丢数据；
- 缺省 dry-run，`--apply` 才写文件。

用法：
    python scripts/apply_source_fixes.py            # 试运行：复测 + 打印将做的改动
    python scripts/apply_source_fixes.py --apply    # 写 config/schools.yaml
"""
import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from crawler import parse  # noqa: E402
from find_list_url import _probe_headers, guarded_get, safe_url  # noqa: E402

CONFIG = ROOT / "config" / "schools.yaml"

# 换址：(学校, 栏目, 新 URL)
REPLACE = [
    ("东北大学", "信息公开网", "http://info.neu.edu.cn/mainm.htm"),
    ("中国海洋大学", "信息公开网", "https://www.ouc.edu.cn/tzgg/list.htm"),
    ("华南理工大学", "研究生院", "https://www2.scut.edu.cn/graduate/"),
    ("电子科技大学", "信息公开网", "https://xxgkw.uestc.edu.cn/index/gggs.htm"),
    ("中央财经大学", "信息公开网", "https://op.cufe.edu.cn/xxgk/xxgk/xxgkjtsx.htm"),
    ("中国地质大学（北京）", "信息公开网", "https://bm.cugb.edu.cn/info/"),
    ("太原理工大学", "太原理工大学研究生院", "http://www.gs.tyut.edu.cn/"),
    ("延边大学", "研究生院", "https://grad.ybu.edu.cn/"),
    ("东北师范大学", "信息公开网", "https://publish.nenu.edu.cn/"),
    ("东北林业大学", "研究生院", "https://gra.nefu.edu.cn/index/tzgg.htm"),
    ("上海外国语大学", "信息公开网", "https://info.shisu.edu.cn/"),
    ("上海财经大学", "信息公开网", "https://gongkai.sufe.edu.cn/"),
    ("苏州大学", "苏州大学信息公开网", "https://www.suda.edu.cn/xxgk/"),
    ("南京农业大学", "研究生院", "https://grasch.njau.edu.cn/"),
    ("中国矿业大学", "研究生招生网", "https://yz.cumt.edu.cn/"),
    ("华南师范大学", "信息公开网", "http://xxgk.scnu.edu.cn/category/"),
    ("四川农业大学", "研究生招生网", "https://yan.sicau.edu.cn/"),
    ("北京科技大学", "研究生招生信息网", "https://yzxc.ustb.edu.cn/"),
    ("云南大学", "研究生院", "http://www.grs.ynu.edu.cn/"),
    ("南京师范大学", "研究生招生网", "https://yjszs.njnu.edu.cn/"),
    ("中国政法大学", "研究生院", "https://yjsy.cupl.edu.cn/tzgg.htm"),
    # 南昌：招生信息网域名已注销，本科招生网原指根地址只解析 1 条 —— 把本科招生网
    # 指到 /zs（parse 10），招生信息网按重复栏目删除（见 DELETE）
    ("南昌大学", "本科招生网", "https://bkzs.ncu.edu.cn/zs"),
]

# 加标记：(学校, 栏目, 字段, 值)
FLAGS = [
    ("南京师范大学", "研究生招生网", "real_browser", "true"),
    ("南京师范大学", "研究生院", "real_browser", "true"),
    ("南京师范大学", "教务处", "real_browser", "true"),
    ("南京师范大学", "信息公开网", "real_browser", "true"),
    ("南京师范大学", "学工处", "real_browser", "true"),
    ("南京师范大学", "本科招生网", "real_browser", "true"),
    ("石河子大学", "教务处", "browser", "true"),
    ("石河子大学", "学工部", "browser", "true"),
    ("电子科技大学", "信息公开网", "stype", "综合信息"),
]

# 删栏目（站点已死/无通知流/语义重复；历史通知由 sync_sources.py 决定去留）
DELETE = [
    ("贵州大学", "贵州大学本科招生网"),   # 与「本科招生网」rso.gzu.edu.cn 语义重复
    ("南昌大学", "南昌大学招生信息网"),   # 域名注销，且与「本科招生网」重复
    ("中国政法大学", "本科招生信息网"),   # vhost 已停用 + 与 zs.cupl.edu.cn 重复
    ("东北林业大学", "学工部"),          # TLS 通但 HTTP 零字节，站点侧挂起
    ("北京航空航天大学", "学生处"),       # 栏目服务端就是空壳
    ("西南交通大学", "就业中心"),         # 只有登录/办事入口菜单
    ("华中师范大学", "信息公开网"),       # CNAME 到 WAF 主机，三出口 TCP 超时
    ("郑州大学", "郑州大学信息公开网"),   # 新址无通知流（换址只会拿到目录链接）
]


def load_yaml():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def find_source(cfg, school, source):
    """在 yaml 里定位 (学校, 栏目)，返回其 dict 或 None。"""
    for sc in cfg.get("schools", []):
        if sc["name"] != school:
            continue
        for src in sc.get("sources", []):
            if src.get("name") == source:
                return src
    return None


def probe_parse(url, domain=""):
    """复测目标地址：返回 (解析条数, 说明)。

    requests 拿不到（WAF 挑战/被拦）时再用真实浏览器通道复测一次——瑞数类
    站点对裸请求必回挑战页，只按 requests 判定会把可用地址误判成不可用
    （南京师范大学、政法研究生院都是这一类）。
    """
    if not safe_url(url):
        return 0, "拒绝非公网地址"
    dom = domain or url.split("/")[2]
    note = ""
    try:
        r = guarded_get(url, _probe_headers(url), timeout=20)
        r.encoding = r.apparent_encoding or "utf-8"
        html = r.text or ""
        if r.status_code < 400:
            items = parse.parse_list(html, url, dom, max_items=10)
            if items:
                return len(items), f"requests，例：{items[0]['title'][:40]}"
            note = f"requests 拿到 {len(html)} 字节但解析 0 条"
        else:
            note = f"requests HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        note = f"requests {type(e).__name__}"
    from crawler import fetch  # 延迟导入：只需要时才起浏览器
    try:
        html = fetch.http_get(url, use_real_browser=True)
    except Exception as e:  # noqa: BLE001
        return 0, f"{note}；浏览器通道也失败：{type(e).__name__}: {str(e)[:60]}"
    items = parse.parse_list(html, url, dom, max_items=10)
    if items:
        return len(items), f"浏览器通道 {len(html)} 字节，例：{items[0]['title'][:40]}"
    return 0, f"{note}；浏览器通道 {len(html)} 字节仍解析 0 条"


def block_span(lines, school, source):
    """返回该栏目块在文件里的 [起, 止) 行号；定位不到返回 None。

    栏目块 = `  - name: X` 起，到下一个同级条目为止——下一个栏目的
    `  - name:`、学校级键（`  tags:` 这类两空格缩进的键）、或下一所学校
    （顶层 `- name:`）。学校级键必须算结束，否则最后一个栏目的块会
    一路吃到 tags 行、删栏目时连带删掉别的行。
    """
    in_school = False
    start = None
    for i, line in enumerate(lines):
        if line.startswith("- "):                     # 顶层：学校
            if start is not None:
                return start, i
            in_school = line.startswith("- name:") and \
                line.split(":", 1)[1].strip() == school
            continue
        if not in_school:
            continue
        if line.startswith("  - name:"):              # 同级：栏目
            if start is not None:
                return start, i
            if line.split(":", 1)[1].strip() == source:
                start = i
            continue
        if start is not None and line.startswith("  ") and not line.startswith("   "):
            return start, i                           # 学校级键：栏目块结束
    if start is not None:
        return start, len(lines)
    return None


def school_domain(cfg, school):
    for sc in cfg.get("schools", []):
        if sc["name"] == school:
            return sc.get("domain", "")
    return ""


def apply_all(dry=True):
    lines = CONFIG.read_text(encoding="utf-8").splitlines()
    cfg = load_yaml()
    report = []

    for school, source, new_url in REPLACE:
        src = find_source(cfg, school, source)
        if not src:
            report.append(f"跳过（yaml 里没有该栏目）: {school} / {source}")
            continue
        if src.get("url") == new_url:
            report.append(f"已是目标地址: {school} / {source}")
            continue
        n, sample = probe_parse(new_url, school_domain(cfg, school))
        if n <= 0:
            report.append(f"**复测未通过（parse={n}，{sample}），不改**: "
                          f"{school} / {source} → {new_url}")
            continue
        span = block_span(lines, school, source)
        if not span:
            report.append(f"定位失败: {school} / {source}")
            continue
        start, end = span
        changed = False
        for j in range(start, end):
            if lines[j].lstrip().startswith("url:"):
                report.append(f"换址: {school} / {source}\n"
                              f"    - {lines[j].strip()}\n    + url: {new_url}"
                              f"    （parse={n}，{sample}）")
                lines[j] = f"    url: {new_url}"
                changed = True
                break
        if not changed:
            report.append(f"没找到 url 行: {school} / {source}")

    for school, source, field, value in FLAGS:
        src = find_source(cfg, school, source)
        if not src:
            report.append(f"跳过（yaml 里没有该栏目）: {school} / {source}")
            continue
        if str(src.get(field, "")).lower() == value:
            report.append(f"已有标记: {school} / {source} {field}")
            continue
        span = block_span(lines, school, source)
        if not span:
            report.append(f"定位失败: {school} / {source}")
            continue
        start, end = span
        for j in range(start, end):
            if lines[j].lstrip().startswith(f"{field}:"):
                lines[j] = f"    {field}: {value}"
                report.append(f"改标记: {school} / {source} {field}: {value}")
                break
        else:
            # 插在栏目块末尾（url/category/stype 之后），与文件既有写法一致
            last_key = start
            for j in range(start, end):
                key = lines[j].lstrip().split(":", 1)[0]
                if lines[j].startswith("    ") and key in (
                        "url", "category", "stype", "browser", "real_browser"):
                    last_key = j
            lines.insert(last_key + 1, f"    {field}: {value}")
            report.append(f"加标记: {school} / {source} {field}: {value}")

    for school, source in DELETE:
        src = find_source(cfg, school, source)
        if not src:
            report.append(f"跳过（yaml 里没有该栏目）: {school} / {source}")
            continue
        span = block_span(lines, school, source)
        if not span:
            report.append(f"定位失败: {school} / {source}")
            continue
        start, end = span
        report.append(f"删栏目: {school} / {source}  "
                      f"（{src.get('url')}）→ 删 {end - start} 行")
        del lines[start:end]
        # 行号已变，后续删除重新按当前文本解析定位
        cfg = yaml.safe_load("\n".join(lines) + "\n") or {}

    print("\n".join(report) or "（无改动）")
    if not dry:
        CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已写入 {CONFIG}")
    return lines


def main():
    ap = argparse.ArgumentParser(description="批量修栏目：换址/加标记/删栏目")
    ap.add_argument("--apply", action="store_true", help="写入 schools.yaml")
    args = ap.parse_args()
    if not args.apply:
        print("== 试运行（复测 + 预览，不写文件）==")
    apply_all(dry=not args.apply)
    if not args.apply:
        print("== 确认无误后加 --apply 写文件 ==")


if __name__ == "__main__":
    main()
