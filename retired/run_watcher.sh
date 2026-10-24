#!/usr/bin/env bash
# one watcher pass — called by cron every minute
cd /c/CODING/project-improver || exit 1
python watch_answers.py >> logs/watcher.log 2>&1
