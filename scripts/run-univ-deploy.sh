#!/bin/bash
# hermes cron script: 每日发布静态站到 GitHub Pages
set -e
cd /home/university-notice-collector
./venv/bin/python deploy_site.py >> logs/cron-deploy.log 2>&1
