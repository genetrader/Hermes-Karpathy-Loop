"""Reproduce the GroundTruth refusal on Gene's real action text."""
import sys
from pathlib import Path

ROOT = Path(r"D:\ZCODE\TOMBS AND TYRANTS")
sys.path.insert(0, str(ROOT))

from backend.world.ground_truth import (          # noqa: E402
    RoomFacts, check_action, check_suggestion, phrases_in,
)

# The real sentence Gene typed, verbatim.
ACTION = "Henk, what work can you give me? I'm ready to handle your toughest jobs."

# A stand-in for The Blooming Tankard as described in the transcript.
ROOM_DESC = (
    "The Blooming Tankard: candles guttering on every table, the bard still "
    "strumming in the corner, chimney smoke thick under the low beams. Henk "
    "sits near the hearth, knuckles wrapped, ale in hand. Drazenna watches "
    "from behind the bar, rag still."
)
NPC_NAMES = ["Henk Pitfighter", "Drazenna Verthisathurgiesh"]
FEATURES = ["hearth", "bar", "tables", "beams", "candles"]


class _Room:
    def __init__(self):
        self.name = "The Blooming Tankard"
        self.room_type = type("RT", (), {"value": "tavern"})()
        self.description = ROOM_DESC


print("=" * 72)
print("1. phrases_in() — what the checker thinks the player claims exists")
print("=" * 72)
ph = phrases_in(ACTION)
print(f"   phrases_in({ACTION!r})")
print(f"   -> {ph}")

for label, s in [
    ("action", ACTION),
    ("menu: Look around and take stock of what IS here",
     "Look around and take stock of what IS here"),
    ("menu: Consider what you already know",
     "Consider what you already know"),
    ("menu: Take stock of what you are carrying",
     "Take stock of what you are carrying"),
]:
    print(f"\n   {label}\n     phrases_in -> {phrases_in(s)}")

print()
print("=" * 72)
print("2. RoomFacts built the way game_session.py builds it")
print("=" * 72)
try:
    facts = RoomFacts.from_room(_Room())
    print(f"   RoomFacts.from_room -> names={len(facts.names)}")
except Exception as e:
    print(f"   from_room failed: {type(e).__name__}: {e}")
    facts = None

if facts is None:
    print("\n   Trying manual construction to continue the probe...")
    try:
        facts = RoomFacts(names=set(), description=ROOM_DESC)
        print(f"   manual -> names={facts.names!r}")
    except Exception as e:
        print(f"   manual failed too: {e}")
        raise SystemExit(1)

print()
print("=" * 72)
print("3. check_action — THE DECISION THAT REFUSED GENE")
print("=" * 72)
v = check_action(ACTION, facts)
print(f"   ok       = {v.ok}")
print(f"   missing  = {v.missing}")
print(f"   reason   = {v.reason!r}")

if not v.ok:
    print()
    print("   >>> REFUSAL FIRES. game_session.py:3532 emits the hardcoded")
    print("   >>> deflection and returns early — the GM never sees the turn.")
    for m in v.missing:
        print(f"   >>> missing fixture: {m!r}   facts.has({m!r}) = {facts.has(m)}")
        try:
            print(f"   >>> canonical/stem view: {facts._stem(m) if hasattr(facts, '_stem') else 'n/a'}")
        except Exception:
            pass
else:
    print()
    print("   >>> No refusal on this text with these facts.")

print()
print("=" * 72)
print("4. check_suggestion on the three meta-choices that replaced it")
print("=" * 72)
for s in ["Look around and take stock of what IS here",
          "Consider what you already know",
          "Take stock of what you are carrying"]:
    print(f"   {check_suggestion(s, facts)!s:5}  {s}")

print()
print("=" * 72)
print("5. What the room actually contains")
print("=" * 72)
names = sorted(str(n) for n in facts.names)
print(f"   {len(names)} known names: {names[:40]}")
print()
for probe in ["toughest", "jobs", "work", "henk", "hevk", "the river", "a signpost"]:
    print(f"   facts.has({probe!r}) = {facts.has(probe)}")