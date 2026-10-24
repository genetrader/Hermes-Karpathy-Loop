#!/usr/bin/env python
"""scripts/karpathy_nudge_cron.py -- cron entry for the Karpathy nudge.

Cron --script jobs get NO arguments, so this wrapper just runs the module.
"""
import sys
sys.path.insert(0, r"C:\CODING\project-improver")
import karpathy_nudge

if __name__ == "__main__":
    raise SystemExit(karpathy_nudge.main([]))
