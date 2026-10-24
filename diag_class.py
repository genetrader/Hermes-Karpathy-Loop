"""How broad is the superlative/comparative hole in phrases_in()?

If the fix is a word list, it will fail the same way next time. This probe
measures whether the hole is a CLASS (structural) or a handful of words.
"""
import sys
from pathlib import Path

ROOT = Path(r"D:\ZCODE\TOMBS AND TYRANTS")
sys.path.insert(0, str(ROOT))
from backend.world.ground_truth import phrases_in   # noqa: E402

CASES = [
    # --- superlatives (-est): the bug Gene hit
    "handle your toughest jobs",
    "who is the strongest here",
    "the fastest route out",
    "give me your hardest task",
    "where is the nearest town",
    "the largest building",
    "the deepest cellar",
    "the oldest story",
    "the strangest thing",
    "the richest patron",
    "the best ale",
    # --- comparatives (-er): already partly handled
    "a better plan",
    "a tougher job",
    "a stronger drink",
    "a bigger room",
    "the younger one",
    # --- abstract/role nouns that slip past the article rule
    "tell me about the work",
    "what is the job",
    "ask about the rumor",
    "the situation here",
    "the problem with it",
    # --- genuine fixtures: MUST still be refused
    "read the signpost",
    "walk to the crossroads",
    "open the chest",
    "climb the well",
    "look at the statue",
]

print(f"{'RETURNS':<32} INPUT")
print("-" * 88)
class_hits = []
for c in CASES:
    ph = phrases_in(c)
    flag = ""
    if ph:
        flag = "  <-- WORDS TREATED AS FIXTURES"
        class_hits.append((c, ph))
    print(f"{str(ph):<32} {c}{flag}")

print()
print("=" * 88)
print(f"{len(class_hits)} of {len(CASES)} inputs yielded fixture-phrases")
print("=" * 88)

# Classify the false positives
import re
FALSE = []
REAL = []
for c, ph in class_hits:
    for p in ph:
        is_super = p.endswith("est") and len(p) > 4
        is_comp = p.endswith("er") and len(p) > 4
        bucket = "SUPERLATIVE" if is_super else ("COMPARATIVE" if is_comp else "OTHER")
        (REAL if c in ("read the signpost", "walk to the crossroads",
                       "open the chest", "climb the well", "look at the statue")
         else FALSE).append((p, bucket, c))

print()
print("FALSE POSITIVES (not fixtures, but yielded phrases):")
for p, b, c in FALSE:
    print(f"   {p:<14} [{b:<11}] from: {c}")

print()
print("TRUE POSITIVES (real fixtures, must stay flagged):")
for p, b, c in REAL:
    print(f"   {p:<14} [{b:<11}] from: {c}")