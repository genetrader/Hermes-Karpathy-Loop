"""Shared test setup for the project-improver suite.

CRITICAL: KL_RUNNER_LOG must be set BEFORE any test imports karpathy_runner,
because LOG is read at import time. Without this, every orchestration test
that calls run_round() appends junk ("t round 1 rc=1 ...") to the LIVE
logs/runner.log -- which monitor.py renders as Recent activity. That leak put
367 junk lines into the live feed on 2026-10-01 (the operator saw them).
"""
import os
import tempfile
from pathlib import Path

# One temp log per pytest session, NOT the live runner log.
if "KL_RUNNER_LOG" not in os.environ:
    os.environ["KL_RUNNER_LOG"] = str(
        Path(tempfile.gettempdir()) / "kl-test-runner.log")

# Keep the discord notifier inert in tests: no token, no network calls.
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-nonetwork")
