#!/usr/bin/env bash
# one rotation pass + regenerate the monitor — called by cron every 30 min
cd /c/CODING/project-improver || exit 1
python improver.py run    >> logs/rotator.log 2>&1
python monitor.py         >> logs/rotator.log 2>&1
