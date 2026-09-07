#!/bin/bash
# hermes cron script: 每日采集质量复盘（调用 hermes 本身跑 agent 分析）
set -e
cd /home/university-notice-collector
./venv/bin/python scripts/daily_review.py >> logs/cron-review.log 2>&1
