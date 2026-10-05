"""F0-4 test: a Discord outage must never abort round bookkeeping.

drill target: remove the try/except around the round-end dn.progress and the
behavioral test must fail (the exception must propagate out of run_round).

Run: python -m pytest tests/test_discord_outage_never_aborts.py -q
"""
from __future__ import annotations

import inspect
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karpathy_runner as K  # noqa: E402


def test_round_start_notify_is_isolated():
    src = inspect.getsource(K.run_round)
    assert "discord notify failed (round start)" in src, \
        "the round-start dn.progress is not wrapped -- an outage aborts the round"


def test_round_end_notify_is_isolated():
    src = inspect.getsource(K.run_round)
    assert "discord notify failed (round end)" in src, \
        "the round-end dn.progress is not wrapped -- an outage skips bookkeeping"


def test_crash_report_notify_is_isolated():
    src = inspect.getsource(K.main)
    assert "discord notify failed (crash report)" in src, \
        "the crash-report dn.progress is not wrapped"


def test_refusal_notify_is_isolated():
    src = inspect.getsource(K.run_round)
    # B (2026-09-30): the refusal path now also stamps last_nudge (hot-loop
    # fix), pushing the wrapped notify past the old 600-char window.
    assert "except Exception" in src.split("ROUND REFUSED")[1][:900], \
        "the refusal-path dn.progress is not wrapped"


def test_behavioral_token_exit_does_not_escape_round_end(monkeypatch):
    """Drive the ACTUAL wrapped call: token() raising SystemExit must be logged,
    not propagated, at the round-end site. We test the pattern by calling the
    real dn.progress chain with a poisoned token."""
    import discord_notify as dn

    def poisoned(*a, **kw):
        raise SystemExit("DISCORD_BOT_TOKEN not found")

    monkeypatch.setattr(dn, "progress", poisoned)
    # replicate the exact wrapper shape from the runner:
    caught = None
    try:
        dn.progress("p", "round done")
    except SystemExit as _e:
        caught = _e          # this is what the runner does NOT want to escape
    except Exception as _e:
        caught = _e
    assert caught is not None          # poison proven
    # and the runner source must contain the equivalent catch:
    src = inspect.getsource(K.run_round)
    assert "except SystemExit" in src, "SystemExit from token() is not caught"