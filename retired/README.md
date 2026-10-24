# retired/ — moved out of the live tree on 2026-09-30 (F4 T2-7/T3-10/T3-11)
# Nothing in the live loop imports these. Reasons per file:

#   karpathy_nudge.py — orphaned duplicate driver; live cron runs hermes/scripts/karpathy_watchdog.py
#   karpathy_nudge_cron.py — cron entry for the orphaned nudge driver
#   run_rotator.sh — called improver.py run (retired); no live cron/task references it
#   run_watcher.sh — no live cron/task references it; watch_answers runs via pending-question flow
#   surface_discover.py — T3-11: dead code, never imported; Phase B may resurrect
