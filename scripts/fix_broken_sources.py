# -*- coding: utf-8 -*-
"""为采集失败/零解析的栏目搜索修正 URL（hermes web 搜索驱动）。

用法（服务器上跑）：python3 scripts/fix_broken_sources.py
结果写到 /tmp/fix_batch_N.txt，逐批解析后更新 config/schools.yaml。
"""
import json
import subprocess
import time
from pathlib import Path

# 批次：每项 (学校, [(栏目, 现URL, 问题)])
BATCHES = [
    [
        ("宁夏大学", [("信息公开网", "https://xxgk.nxu.edu.cn/", "域名解析失败"),
                    ("研究生院", "https://graduate.nxu.edu.cn/", "导航页无列表"),
                    ("本科生院", "https://bksy.nxu.edu.cn/", "导航页无列表"),
                    ("学生处", "https://xsc.nxu.edu.cn/", "导航页无列表")]),
        ("中央音乐学院", [("教务处", "https://www.ccom.edu.cn/jgk/jxkygldw/jwc/bmjj.htm", "部门介绍页非列表"),
                       ("学生工作部", "https://www.ccom.edu.cn/jgk/dzgljg/xsgzb_tw_wzb/bmjj.htm", "部门介绍页非列表"),
                       ("就业指导中心", "https://www.ccom.edu.cn/jgk/dzgljg/xsjyzdzx/bmjj.htm", "部门介绍页非列表"),
                       ("本科招生", "https://zhaoban.ccom.edu.cn/", "导航页无列表")]),
        ("中南财经政法大学", [("研究生院", "https://yjsy.zuel.edu.cn/", "导航页无列表"),
                        ("教务处", "https://jwc.zuel.edu.cn/", "导航页无列表"),
                        ("学工部", "https://xgb.zuel.edu.cn/", "导航页无列表"),
                        ("就业中心", "https://jyzx.zuel.edu.cn/", "导航页无列表")]),
    ],
    [
        ("武汉理工大学", [("研究生院", "http://jwc.whut.edu.cn/", "域名/网络失败"),
                     ("信息公开网", "https://xxgk.whut.edu.cn/", "域名/网络失败"),
                     ("学工部", "http://stuplaza.whut.edu.cn/", "导航页无列表"),
                     ("就业中心", "https://scc.whut.edu.cn/", "导航页无列表")]),
        ("华中农业大学", [("研究生院", "https://yjsy.hzau.edu.cn/", "域名/网络失败"),
                     ("信息公开网", "https://xxgk.hzau.edu.cn/", "域名/网络失败")]),
        ("西南交通大学", [("本科招生网", "https://zhaosheng.swjtu.edu.cn/", "域名/网络失败"),
                     ("信息公开网", "https://xxgk.swjtu.edu.cn/", "域名/网络失败"),
                     ("本科生院", "https://bksy.swjtu.edu.cn/", "导航页无列表"),
                     ("学工部", "https://xg.swjtu.edu.cn/", "导航页无列表")]),
    ],
    [
        ("广西大学", [("研究生院", "https://yjsy.gxu.edu.cn/", "域名/网络失败"),
                    ("信息公开网", "https://xxgk.gxu.edu.cn/", "域名/网络失败"),
                    ("教务处", "https://jwc.gxu.edu.cn/", "导航页无列表"),
                    ("学工部", "https://xg.gxu.edu.cn/", "导航页无列表")]),
        ("北京林业大学", [("研究生招生网", "https://yz.bjfu.edu.cn/", "域名/网络失败"),
                     ("教务处", "https://jwc.bjfu.edu.cn/", "导航页无列表"),
                     ("学生处", "https://xsc.bjfu.edu.cn/", "导航页无列表"),
                     ("就业网", "https://job.bjfu.edu.cn/", "导航页无列表")]),
        ("武汉大学", [("本科生院", "https://uc.whu.edu.cn/", "域名/网络失败"),
                    ("学工部", "https://xgbnew.whu.edu.cn/", "域名/网络失败"),
                    ("本科招生网", "https://aoff.whu.edu.cn/", "域名/网络失败"),
                    ("就业中心", "https://xsjy.whu.edu.cn/", "导航页无列表")]),
    ],
    [
        ("辽宁大学", [("研究生院", "https://gs.lnu.edu.cn/", "域名/网络失败")]),
        ("石河子大学", [("研究生院", "https://yjsy.shzu.edu.cn/", "域名/网络失败"),
                    ("信息公开网", "https://xxgk.shzu.edu.cn/", "域名/网络失败"),
                    ("学工部", "https://xgb.shzu.edu.cn/", "域名/网络失败")]),
        ("安徽大学", [("研究生院", "https://grs.ahu.edu.cn/", "域名/网络失败"),
                    ("教务处", "https://jwc.ahu.edu.cn/", "导航页无列表"),
                    ("学生处", "https://xsc.ahu.edu.cn/", "导航页无列表"),
                    ("就业网", "https://job.ahu.edu.cn/", "导航页无列表")]),
    ],
]

TMPL = """以下高校栏目网站采集失败（问题：域名失效 或 首页是导航页没有通知列表）。
请用 web 搜索 + 访问验证，为每个栏目找到**可直接采集的通知列表页 URL**：
要求返回 200 且页面包含多条通知/公告链接（形如标题+日期）；若原 URL 是导航页，
请找到其"通知公告/新闻动态"等子列表页的确切 URL。找不到可靠结果的填"无"。

{items}

输出紧凑表格：学校 | 栏目 | 建议URL | 验证(HTTP状态/是否含通知列表)"""


def main():
    for idx, batch in enumerate(BATCHES, 1):
        out = Path(f"/tmp/fix_batch_{idx}.txt")
        if out.exists() and out.stat().st_size > 200:
            print(f"批{idx} 已有结果，跳过", flush=True)
            continue
        items = []
        for school, sources in batch:
            for name, url, problem in sources:
                items.append(f"- {school} | {name} | 现URL: {url} | 问题: {problem}")
        prompt = TMPL.format(items="\n".join(items))
        print(f"== 批{idx}（{len(batch)} 校 {len(items)} 栏目）", flush=True)
        try:
            r = subprocess.run(
                ["hermes", "-z", prompt], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1500)
            out.write_text(r.stdout or "", encoding="utf-8")
            print(f"   完成 {len(r.stdout or '')} 字符", flush=True)
        except subprocess.TimeoutExpired:
            out.write_text("TIMEOUT", encoding="utf-8")
            print("   超时", flush=True)
        time.sleep(3)
    print("全部完成", flush=True)


if __name__ == "__main__":
    main()
