# SURFACES — verified state at 2026-09-28 ~22:15 EDT

This file is the honest ledger. It exists because a "delegation completed"
notification is NOT a result: two agents reported as finished having written
**nothing** to disk. Always inspect evidence before believing a completion.

## VERIFIED DONE (I ran these myself)

**SecondBrain Today-screen fix — DONE AND ON THE PHONE.**
- Root cause: `attention_items.id` is `INTEGER PRIMARY KEY AUTOINCREMENT`
  (`server/app/db.py`); `GET /briefing` embeds `attention.summary()`
  (`server/app/routers/life.py:1067`) which ships `dict(r)` off a `SELECT *`;
  the Kotlin DTO declared `AttentionItemDto.id: String`. kotlinx threw
  `Expected quotation mark '"', but had '1'`, killing the WHOLE briefing.
- Two edits, both compiling (`:app:compileDebugKotlin` exit 0):
  `ApiModels.kt` — `id: Int`, `@SerialName("signal_id") signalId`,
  `@SerialName("action_text") actionText`, `createdAt`/`acknowledgedAt`,
  `tripped` on the summary; `SecondBrainApi.kt` — `acknowledgeAttention(id: Int)`.
- Regression test `android/app/src/test/.../AttentionContractRegressionTest.kt`
  (6482 B) pins it with a **byte-for-byte live payload** plus a reflective
  check that `getId()` returns `int`.
  **JUnit XML: tests=4 failures=0 errors=0 skipped=0 — all 4 PASS.**
- **Installed on the device:** `lastUpdateTime=2026-09-28 22:05:58`,
  `firstInstallTime=2026-08-19` unchanged (settings/token/queue survived).

| Piece | State | Evidence |
|---|---|---|
| `surface_pick.py` (consumes a surface list, round-robins) | DONE | 13 tests |
| `scope.py` (SURFACE-DONE / CROSS_CUTTING, campaign state machine) | DONE | 27 tests |
| Surface threaded into the round prompt (`round_prompt.py`) | DONE | legacy 4-arg call stays byte-clean |
| Campaign advance wired into the runner, parsed BEFORE truncation | DONE | `_parse_scope` on full `_out` |
| Surface chip in the projects panel | DONE | render proof 25/25; gate 6/6 dev+installed |
| Full loop test suite | GREEN | **58 passed, 0 failed** |

## NOT DONE — do not claim otherwise

**Discovery and propagation produced NOTHING** on the first attempt
(`deleg_31456dea`, both tasks killed mid-run; zero files on disk).
Re-dispatched as `deleg_aec56dc2` with a write-the-artifact-first mandate.

**THE ROTATION IS NOT HANDLED.** This is the crux and it is unfixed:

```python
# karpathy_runner.py:547
projs = sorted(projs, key=lambda p: (reg.get(p["name"], {}).get("last_nudge") or 0))
```

- The sort knows nothing about surfaces or campaigns → a campaign 2-of-5 done
  loses its project to the next repo and **never finishes**.
- `karpathy_runner.py:304` publishes `entry["current_surface"]` and
  **nothing reads it back**.
- `loopctl.py` has **zero** surface/campaign references — no `--show-campaign`.

So today the feature would consume a hand-written list, work one surface, and
rotate away. The plumbing is real; the loop cannot yet act on it.

## Still open

- S7 live-round payload measurement (2554 B is the IDLE number; the measured
  hazard was a payload inflated by a live feed).
- S8: SecondBrain's 5 surfaces are not declared in `improve.yaml` — correctly
  gated behind the discovery design (propose + human confirm; the manifest is
  authoritative, so nothing the rotation cannot reach may be created).
- `run-server.ps1`'s watchdog still treats **any 401** as proof of health
  (`if ($_.Exception.Response) { exit 0 }`), so a stray process can hold
  :8823 and stall the queue. Unfixed.
- Android defects A3–A6: `UploadWorker.kt:52` `return Result.success()` on
  config failure; zero connectivity callbacks; `:62-66` silent file delete;
  `auto_delete_empty` awaiting Gene's call. Needs a new APK build.

## Lessons worth keeping

1. **A completion notification is not a deliverable.** Check the disk.
2. **A green build is not a passing test.** Gradle reports BUILD SUCCESSFUL
   when a `--tests` filter matches nothing; read the JUnit XML
   (`tests=/failures=/skipped=`) to prove execution.
3. **Your own check can be the bug.** Twice in this project a check was
   unsound and produced a confident wrong finding (`node --check` on ESM;
   `importlib.find_spec` against a venv launcher). I re-validate every new
   check by injecting the failure it claims to catch.