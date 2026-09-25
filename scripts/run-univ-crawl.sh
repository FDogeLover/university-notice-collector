#!/bin/bash
# hermes cron script: 分片增量采集
#
# 全量栏目约 667 个、单片约 28 分钟，为避免 hermes cron 的 60 分钟硬超时，
# 按"当前小时 % 片数"自动选择分片：每小时的 :30 跑一片，3 小时覆盖全量。
# 2026-09-25: --max-items 20 → 50。20 会让约 22% 栏目单轮截断，排在前 20 个
# 命中链接之外的新条目长期漏采（首页多分节站点尤甚）；去重先于详情抓取，
# 提高上限只多抓"新条目"的详情，稳态成本不变（上线当日实测见 git log）。
set -e
cd /home/university-notice-collector

SHARDS=3
SHARD=$(( $(date +%-H) % SHARDS + 1 ))

echo "===== $(date '+%F %T') 分片 ${SHARD}/${SHARDS} ====="
./venv/bin/python run.py --shard "${SHARD}/${SHARDS}" \
  --max-items 50 --sleep 0.15 --workers 8 >> logs/cron-crawl.log 2>&1
