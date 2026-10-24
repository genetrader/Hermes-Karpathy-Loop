# Surfaces / Karpathy-loop repair — STATE, 2026-09-29

## What was broken (all verified by defect-reintroduction, not by assertion)

| # | Defect | Where | Status |
|---|--------|-------|--------|
| D1 | `scope` MODULE shadowed by the parsed dict → every `scope.next_campaign()` raised AttributeError, swallowed by a broad `except` (~line 430). Campaigns never advanced in the live runner. | `karpathy_runner.py` | FIXED |
| D2 | TWO `except subprocess.TimeoutExpired` handlers (helper ~:249, round ~:399). A timeout left the parsed scope unbound → crash at next_campaign. First fix landed in the WRONG handler. | `karpathy_runner.py` | FIXED (round handler binds `round_scope`) |
| D3 | ANY verdict closed a campaign — `deferred`, `failed`, arbitrary prose. That IS the half-applied drift the feature exists to prevent. | `scope.py` | FIXED (terminal = `done` \| `not-applicable` only) |
| D4 | Nothing could say WHICH surface a campaign still needed. | `scope.py` | FIXED (`outstanding_surfaces()`) |
| D5 | `checkpoint()` tagged + PUSHED TO GITHUB unconditionally — a timed-out/gate-failing round was published as a legitimate `kp/<repo>/rNN`. | `karpathy_runner.py` :518 | FIXED (gated on `rc == 0` AND runner-observed gate ok; records `round_accepted`) |

## Why the original 27-test suite passed while the runner was dead

`tests/test_scope.py` tested **`scope.py` directly**. The module under test was
correct; the **caller** was wrong. Every test passed while the live runner could
not advance a campaign at all. That is a check that cannot fail.

## The regression suite that closes that gap

`tests/test_scope_callsite.py` — 17 tests. Every one exercises the runner's ACTUAL
call shape, or pins a SEMANTIC rule a well-formed-but-wrong value would violate.

**Validated by reintroduction** — each defect was put back and the suite caught it:
- reintroduce D1 → `test_runner_does_not_rebind_the_scope_module` FAILS
- reintroduce D3 → 3 tests FAIL (`deferred`, prose, `failed`)
- reintroduce D5 → `test_checkpoint_is_gated_on_round_success` FAILS

Suite: **75 passed, 0 failed** (58 prior + 17 new). `karpathy_runner.py` compiles.

## Escalations (per Gene's closer mandate)

- **Saul** (`profiles/saul`, brain `gpt-5.6-sol` via `openai-codex`) — consulted
  2026-09-29 on surfaces classification, rotation fairness, termination bounds,
  and minimum evidence for "done". Profile confirmed present; brain confirmed
  configured via `hermes -p saul config get model`.
- **gpt-6-sol** (same provider, direct) — consulted on the two-gate design.
  Independently confirmed D1 context and D5, and contributed the D5 finding.

## Phase A complete — Saul's six blockers (2026-09-30)

Suite: **123 passed, 0 failed.** Every guard below was watched FAILING under defect
reintroduction before being trusted.

| # | Blocker (Saul, 2026-09-29) | Status | How it is closed |
|---|---|---|---|
| 1 | Campaign mutation runs BEFORE the gate/evidence, so a round that later fails can still open/advance/close a campaign | DONE | `scope.propose()` is pure; `scope.commit()` is the only mutator and runs AFTER `_round_ok`. `test_ordering_is_enforced.py` asserts `propose < accept < commit`. |
| 2 | "No gate configured" treated as a PASSED gate | DONE | Tri-state `_gate_state`: True ran+passed, False ran+failed, None not measured. `_round_ok` requires `_gate_ok` truthy — missing is NOT passed. |
| 3 | Comment claimed "HEAD must move"; code never checked it | DONE | Full 40-char SHAs + `_is_ancestor(before, after)` — a *different* sha is not enough (amend/rebase/reset also differ). |
| 4 | Unbounded campaign widening via arbitrary `CROSS_CUTTING` | DONE | Vocabulary filter + `MAX_ADDITIONS` recomputed per candidate. Also fixed an opening-round variant that built the first campaign straight from `cross["surfaces"]`, bypassing the filter entirely. |
| 5 | Rejected-work contamination: a failed round's commit/untracked files inherited and published by a later round | DONE | `_rollback_to()` on rejection → `reset --hard <anchor>` + `clean -ffd`, then VERIFIED (HEAD at anchor AND tree not dirty). Unverifiable → `quarantined=True` + reason. Selector excludes quarantined repos; all-quarantined backs off 900s instead of spinning. |
| 6 | Rotation epoch could deadlock | DONE | Burst+LRU (K=2, persisted); `last_nudge` never advanced by a quarantined repo. |

### Extra defects found during Phase A

- **Saul round 2(a):** `round_evidence.py` recorded SHORT `%h` while the runner compared FULL `rev-parse HEAD` ⇒ `before != after` was ALWAYS true ⇒ the HEAD gate was **dead code**. Fixed.
- **Saul round 2(b):** `_head_ok is not False` accepted `None`, contradicting the comment directly above it. Fixed to `is True`.
- **Saul #7:** `checkpoint()` fired BEFORE the campaign commit and registry write. Reordered to **accept → commit → persist → publish**, gated on `state_persisted`. A tag must describe state that actually reached disk.
- **`proposal_ok: bool`:** a blanket `except` returning a harmless no-op recreated the silent-swallow that hid D1 for weeks. An infrastructure failure must not be indistinguishable from a legitimate no-op.

### Test inventory

| File | Tests | Proves |
|---|---|---|
| `test_scope.py` | — | parsing (`_v()` reads rich `{"verdict","reason"}` dicts) |
| `test_scope_callsite.py` | — | D1–D5 call-site defects, propose/commit order, checkpoint gating |
| `test_ordering_is_enforced.py` | — | a rejected round cannot open/advance/close a campaign |
| `test_acceptance_is_real.py` | 18 | real git in throwaway repos: ancestor check, full-SHA compare |
| `test_rejected_work_is_contained.py` | 9 | `_rollback_to` removes a commit + untracked file, **with a non-vacuous control** showing contamination really happens without it |
| `test_containment_is_wired.py` | 9 | **AST**: the runner really CALLS containment, guard is `not _round_ok`, selector really negates `quarantined` |

### Why `test_containment_is_wired.py` is AST-based

The first version string-matched (`assert "not _round_ok" in SRC[i-900:i]`). It was
worthless: it matched the *helper definition* rather than the call site, and two of
its own tests failed against healthy code. Three real bugs were then found **in my
own test**, each of which had produced a false result:

1. Hand-rolled visitor missed a level — the call sits inside an `Assign` inside two
   nested `If`s and the visitor did not descend the Assign's value. Replaced with a
   **parent map** walked upward.
2. `_names()` missed string constants — `entry["pre_round_head"]` keys on a
   `Constant`, not a `Name`, so the walk silently missed it.
3. Wrong node inspected — checked the comprehension *generator* instead of `g.iter`.

### Defect reintroduction — proof the tests can fail

| Drill | Defect planted | Result |
|---|---|---|
| 1 | containment guard → `if False and ...` | `test_rollback_call_is_guarded_by_rejection` **FAILED** |
| 2 | `_runnable` filter → `list(projs)` pass-through | `test_selector_builds_a_runnable_set_that_excludes_quarantine` **FAILED** |

Restore verified byte-identical (md5 `5314369f5a272cb96433841b29fdf5ac`); no residue.

**Rule:** a test never observed failing on its own defect is a decoration.

---

## NOT yet built (the actual deliverable)

Phase A makes the *existing* mechanism correct. **The surfaces scheduler is NOT
built**: surface classification, the two adversarial gates, propagation, and
rotation remain open (B1–B5). The loop must stay OFF until B is verified (Phase C).

One known open item: a round that *starts* dirty cannot be cleanly rolled back. It
is detected and logged, but not yet refused.