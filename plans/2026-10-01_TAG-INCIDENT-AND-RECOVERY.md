# INCIDENT 2026-10-01: tag-namespace cleanup went wrong, then recovered

## What happened
While fixing the stale-tag collision (kp/zip-code-ranker/r38 pointed at the
old era, refusing today's accepted r38 to publish), I attempted a bulk
rename of 126 old-era kp/<repo>/rN tags to kp/<repo>/old/rN. The batch
script had two parsing bugs (lightweight tags produced 2-part format lines;
the second pass matched ALREADY-renamed old/ tags because the rN regex
also matches old/rN). Net effect: tags were DELETED and their recreate
failed, across all four repos.

## Recovery (verified)
- 102 tags restored FROM THE GIT REMOTES (T&T, secondbrain, zip push their
  tags; ls-remote still had them) as kp/<repo>/old/rN at the exact shas.
- 1 more (ai-c old/r04) restored from a dangling annotated tag object
  (git fsck --dangling).
- New-era tag kp/zip-code-ranker/r38 (today's accepted round) was kept
  bare and re-pushed BEFORE the incident: origin now has
  kp/zip-code-ranker/r38 -> 4b61d34 + main d4b7c93..4b61d34. The Phase C
  accept->tag->push cycle is PROVEN live.

## Unrecoverable losses (documented, cosmetic)
Old-era lightweight tags that were never pushed and left no tag object:
- tombs-and-tyrants old r01, r36, r37
- secondbrain old r01, r02, r08
- zip-code-ranker old r01, r08
- ai-consultancy-website old r01..r03, r05..r13 (local-only repo; its 5
  commits survive; only the tag->commit mapping is lost)
These markers named rounds whose work is still in each repo's history.

## Post-incident integrity (verified live)
- HEADs unchanged: T&T 8c85201, sb 531314e, zip 4b61d34, ai-c 19a6448.
- All four working trees clean; loop unaffected (runner only creates NEW
  tag names, and the rN namespace is now collision-free).

## Lesson (for the skill)
Batch git-ref operations: dry-run the FULL parse, verify per-item mapping
BEFORE delete, never pair tag -d with recreate in the same unverified
step, and exclude the already-renamed namespace from the match set.