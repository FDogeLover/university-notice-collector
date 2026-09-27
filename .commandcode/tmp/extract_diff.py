# -*- coding: utf-8 -*-
"""从教育部第二轮双一流官方 PDF 提取 147 所高校名单，与项目已收录 111 所做差集。"""
import re, fitz, json

# ---------- 1. 从教育部官方 PDF 提取高校名单 ----------
doc = fitz.open('moe_round2.pdf')
text = ''.join(page.get_text() for page in doc)
# 截掉附件2（公开警示学科名单），只保留附件1（建设高校及建设学科名单）
idx = text.find('附件2')
if idx > 0:
    print('WARN: 附件2 found at', idx, '- truncated')
    text = text[:idx]
print('附件1 text chars:', len(text))

nospace = re.sub(r'\s+', '', text)
segs = nospace.split('：')
official = []
for seg in segs[1:]:  # seg[0] 是前言+第一个校名，需特殊处理
    name = seg
    if '、' in name:
        name = name.rsplit('、', 1)[1]
    if '）' in name:
        name = name.rsplit('）', 1)[1]
    if name.endswith('大学') or name.endswith('学院'):
        official.append(name)
# 处理 seg[0]：前言 + 北京大学
head = segs[0]
if '）' in head:
    head = head.rsplit('）', 1)[1]
if head.endswith('大学') or head.endswith('学院'):
    official.insert(0, head)

# 去重保序
seen, uniq = set(), []
for n in official:
    if n not in seen:
        seen.add(n)
        uniq.append(n)
official = uniq
print('extracted official count:', len(official))
bad = [n for n in official if not re.fullmatch(r'[\u4e00-\u9fff（）]+', n)]
print('suspicious names:', bad)

# ---------- 2. 项目已收录 111 所（来自任务原文） ----------
given_raw = """南京理工大学、西安电子科技大学、北京邮电大学、深圳大学、华东师范大学、华中科技大学、四川大学、武汉大学、中山大学、山东大学、厦门大学、吉林大学、湖南大学、重庆大学、兰州大学、大连理工大学、清华大学、北京大学、北京航空航天大学、中国人民大学、北京理工大学、北京师范大学、中国农业大学、中央民族大学、天津大学、东北大学、南开大学、复旦大学、上海交通大学、同济大学、哈尔滨工业大学、南京大学、东南大学、浙江大学、中南大学、中国海洋大学、华南理工大学、中国科学技术大学、电子科技大学、西北农林科技大学、西安交通大学、西北工业大学、北京交通大学、北京化工大学、北京科技大学、北京工业大学、北京中医药大学、北京林业大学、中国传媒大学、北京外国语大学、对外经济贸易大学、中央财经大学、中国政法大学、华北电力大学、中国矿业大学（北京）、中国地质大学（北京）、中国石油大学（北京）、天津医科大学、内蒙古大学、太原理工大学、河北工业大学、大连海事大学、延边大学、辽宁大学、东北师范大学、哈尔滨工程大学、东北林业大学、华东理工大学、东华大学、上海外国语大学、东北农业大学、上海财经大学、苏州大学、河海大学、上海大学、南京航空航天大学、江南大学、南京农业大学、南京师范大学、中国矿业大学、中国药科大学、中国石油大学（华东）、福州大学、安徽大学、合肥工业大学、郑州大学、武汉理工大学、华中农业大学、中南财经政法大学、华中师范大学、中国地质大学（武汉）、湖南师范大学、华南师范大学、暨南大学、南昌大学、广西大学、西南交通大学、西南财经大学、四川农业大学、海南大学、云南大学、贵州大学、西藏大学、西北大学、长安大学、陕西师范大学、宁夏大学、新疆大学、青海大学、石河子大学、中央音乐学院"""
given = given_raw.split('、')
print('given count:', len(given))

def norm(s):
    return s.replace('（', '(').replace('）', ')').strip()

official_n = {norm(n): n for n in official}
given_n = {norm(g): g for g in given}

missing = [official_n[k] for k in official_n if k not in given_n]
extra_given = [given_n[k] for k in given_n if k not in official_n]
overlap = len(official_n) - len(missing)

# ---------- 3. 输出 ----------
out = []
out.append(f"官方名单提取数量: {len(official)}")
out.append(f"可疑校名: {bad}")
out.append(f"项目已收录数量: {len(given)}")
out.append(f"交叉验证: 官方∩已收录 = {overlap}; 已收录但非双一流 = {extra_given}; 未收录 = {len(missing)}")
out.append("")
out.append("=== 未收录的官方高校名单 ===")
for i, n in enumerate(missing, 1):
    out.append(f"{i}. {n}")
open('diff_result.txt', 'w', encoding='utf-8').write('\n'.join(out))
json.dump(official, open('official_147.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(missing, open('missing.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('done')
