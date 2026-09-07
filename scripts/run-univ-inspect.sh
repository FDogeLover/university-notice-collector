#!/bin/bash
# hermes cron script: 每周日栏目巡检
set -e
cd /home/university-notice-collector
./venv/bin/python scripts/inspect_sources.py >> logs/cron-inspect.log 2>&1
