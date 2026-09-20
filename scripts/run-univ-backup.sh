#!/bin/bash
# hermes cron script: 每日备份生产库（在线备份 + 保留 7 天 + integrity 校验）
#
# 为什么必须单独备份：data/university.db 是 8961 条通知 + 正文快照的唯一副本
# （data/ 不进 git，正文快照不可再生），而 WAL 模式下 cp 可能拿到半截数据。
# 备份放仓库外 /home/backups/univ，避免被 git clean/reset 波及。
set -e
cd /home/university-notice-collector
BACKUP_DIR="${UNIV_BACKUP_DIR:-/home/backups/univ}"
./venv/bin/python scripts/backup_db.py --dir "$BACKUP_DIR" --keep 7 \
  >> logs/cron-backup.log 2>&1
