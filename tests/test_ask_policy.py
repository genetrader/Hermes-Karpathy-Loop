import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ask_policy


def test_budget_is_25_pings_at_5_minutes():
    assert ask_policy.MAX_PINGS == 25
    assert ask_policy.PING_INTERVAL_S == 300


def test_still_pinging_below_budget():
    assert ask_policy.should_ping(pings_sent=3, elapsed_s=900) is True


def test_ping_due_respects_interval():
    assert ask_policy.ping_due(pings_sent=2, since_last_s=100) is False
    assert ask_policy.ping_due(pings_sent=2, since_last_s=301) is True


def test_ping_due_stops_when_exhausted():
    assert ask_policy.ping_due(pings_sent=25, since_last_s=99999) is False


def test_exhausted_after_budget():
    assert ask_policy.exhausted(pings_sent=25) is True
    assert ask_policy.exhausted(pings_sent=24) is False


def test_budget_seconds():
    assert ask_policy.budget_seconds() == 7500
