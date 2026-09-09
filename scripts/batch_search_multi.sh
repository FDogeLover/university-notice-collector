#!/bin/bash
# 批量搜索各校多领域栏目 URL（hermes web 搜索），每批输出独立文件
# 用法: bash batch_search_multi.sh  (读 data/rest_batches.json 自动分批)
set -e
cd /home/university-notice-collector
export PATH=/usr/local/lib/hermes-agent/.venv/bin:$PATH

python3 - <<'PYEOF'
import json, subprocess, sys, time
from pathlib import Path

batches = json.loads(Path('data/rest_batches.json').read_text(encoding='utf-8'))
PROMPT_TMPL = """用 web 搜索工具，查以下大学官方站点 URL：教务处/本科生院、学生工作处/学生部、就业指导中心、本科招生网，每类给官网首页 URL（https、edu.cn），不确定的省略，注意机构名差异（如北大教务部、重大本科生院）。输出简洁表格：大学|教务处|学生工作|就业|本科招生。
大学：{schools}"""

for idx, batch in enumerate(batches, 1):
    outfile = Path(f'/tmp/multi_batch_{idx}.txt')
    if outfile.exists() and outfile.stat().st_size > 200:
        print(f'批{idx} 已有结果，跳过', flush=True)
        continue
    schools = '、'.join(batch)
    prompt = PROMPT_TMPL.format(schools=schools)
    print(f'== 批{idx} ({len(batch)}所): {schools[:40]}...', flush=True)
    try:
        r = subprocess.run(
            ['hermes', '-z', prompt], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=1800)
        outfile.write_text(r.stdout or '', encoding='utf-8')
        print(f'   完成，输出 {len(r.stdout or "")} 字符', flush=True)
    except subprocess.TimeoutExpired:
        outfile.write_text('TIMEOUT', encoding='utf-8')
        print('   超时', flush=True)
    time.sleep(3)

print('全部批次处理完毕', flush=True)
PYEOF
