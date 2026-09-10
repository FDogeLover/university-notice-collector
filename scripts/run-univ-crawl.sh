#!/bin/bash
# hermes cron script: 每日全量增量采集
set -e
cd /home/university-notice-collector
./venv/bin/python run.py --max-items 30 --sleep 0.5 --workers 6 >> logs/cron-crawl.log 2>&1
