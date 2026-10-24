# FINAL SWEEP — triage + fix plan, 2026-10-02
Source: 3-lane pre-signoff review (deleg_bf4ac974), every severity claim
re-verified by me before acceptance. Two lane claims were DOWNGRADED on my
verification (noted inline). Suite: 247 passed at sweep time.

## My verification of lane claims

| Claim | My verdict |
|---|---|
| L3-B1 corrupt answer.json never deleted (44 live `answer intake failed`) | **CONFIRMED** — parse-failure path at runner:1017 logs + returns, file stays |
| L3-M1 `strip("")` no-op empties the pause-kill PID list on PowerShell CSV | **CONFIRMED** — live `Get-CimInstance|ConvertTo-Csv` row: pid token `"15064"` → `int()` raises → dropped; 3 sites (:515/:521/:599) |
| L3-M2 `_runner_at_top_level` quote parity from index 0 misses quoted rows | **CONFIRMED** (code-read; leading field quote flips parity) |
| L1-m2 `round_refused` never cleared after later success | **CONFIRMED** — live threads.json: all 3 repos carry stale refusal text with rounds 40-42 |
| L2-MAJOR-1 maintained() lost-update vs runner saves | **CONFIRMED** (shape: load → mutate other keys → save of a stale snapshot) |
| L2-MAJOR-2 bare `SCOPE:` swallows next marker line | **DOWNGRADED to cosmetic** — my repro: the note TEXT swallows the line, but `_DONE_RE`/`_CROSS_RE` scan independently, so the marker STILL PARSES (`surfaces={'a': done}` intact). Lane-2's claimed impact (verdict lost → attempt-cap block) does not reproduce. Real residual: `uncertain_note` can contain a marker line (log noise). |
| L2-MAJOR-3 `_campaign_outstanding` counts non-terminal verdicts as closed | **CONFIRMED** — repro: activity says 0 outstanding, scope says 2 (`deferred`/`done-uncited`) |
| L1-m1 git dep discovery `--*` pathspec invalid | **CONFIRMED** — `git ls-files … --*` → rc=129 in a scratch repo; fallback always used |

## Fix list (execute now, drill-validated)

| # | Fix | Site | Test/drill |
|---|---|---|---|
| F-A | Corrupt answer.json: `f.unlink(missing_ok=True)` in the except | runner `_take_answer` :1017 | behavioral: torn JSON → file gone, next round clean |
| F-B | `strip("")` → `strip('"')` at :515/:521/:599; port `_cmd_field_start`/`_outside_quotes` into `_runner_at_top_level` | loopctl | unit: quoted-CSV row → PID parsed, runner row matched |
| F-C | `entry.pop("round_refused", None)` after worktree creation succeeds | runner :1123 area | behavioral: refuse → run OK → key absent |
| F-D | `_campaign_outstanding` = `len(scope.outstanding_surfaces(camp))` | activity.py:387 | unit: deferred/done-uncited campaign → activity==scope |
| F-E | `"--*"` → `"--", "*"` | runner `_discover_dep_dirs` :397 | repro: git ls-files exits 0 in scratch repo |
| F-G | maintained(): re-`load()` fresh before save; copy ONLY computed keys (tip/chain/title) into it; drop the trailing `except: pass` on save | threads.py:299-352 | drill: interleaved-save replay no longer reverts `rounds` |
| F-H | UNCERTAIN note: refuse a note starting with a known marker prefix; truncate at line boundary | scope regex/parse | unit: `SCOPE: UNCERTAIN\nSURFACE-DONE…` → note empty/marker-free |
| F-I | one log line for `uncited`/`contradicted`/`uncertain` proposal flags | runner propose-log block | grep assertion |
| F-J | unmatched slug verdicts → `unmatched` list + log | scope `_propose` + runner log | unit |
| F-K | is_running: `"python.exe" in cols[0].lower()` (exclude pythonw) | runner :192 | existing test file extended |
| F-L | monitor: surface QUARANTINED in queue table; turns-taken/history from threads.json; delete dead `parked` | monitor.py | regenerate + grep |
| F-M | widget badge: gate the `cot.steps` term on `cot.log_age_s < 900` | plugin.js:1408 | payload-driven badge probe |
| F-N | loopctl show_campaign: guarded int for campaign_holds | loopctl:763 | unit |
| F-O | pin test: pre-merge gate cwd == worktree (L1-M1 residual) | tests | new test |

## ACCEPTED AS DESIGNED / DEFERRED (no code now)

- L1-m3/m6 (quarantine escape + orphaned worktree need TWO simultaneous
  failures); L1-m7 cite-index junction follow (unreachable: junctions are all
  _SKIP_DIRS names, no tracked symlinks in any canonical); L1-m8 branch residue
  on already-gone worktrees; L1-m9 menu-line echo (instruction-only); L1-m10
  pin tests (covered by F-O); L3-M3 parity-after-payload (HYPOTHESIS, needs
  live mid-round repro — revisit if --now ever misses); L3 minor-8
  selfcheck log_age_s (seed None); L2 minor-5/10 (deliberate); tombs probe
  double-scheme (works via repair path) + missing `see:` block (declared gap).
- **Gene decision needed:** the ask/answer PRODUCER is dormant (nag cron
  disabled since 09-23). Consume side works (verified exactly-once). Either
  re-enable `project-improver-nag` cron or we retire the red-dot/OPEN-QUESTION
  UI surfaces. No loop risk either way.
- 6 known deferred lows (plans/2026-10-01_DEBUG-SESSION.md) unchanged.

## Sign-off verdict (pending F-A…F-O execution)

**Endless-run ready: YES, conditional on F-A/F-B/F-G.** The loop is safe to
re-arm today even without the fixes (nothing found blocks rounds), but F-A is
a permanent log stain + suppressed answers, F-B silently disables the hard-stop
kill on wmic-less Windows, and F-G can revert campaign state mid-round. Those
three are cheap; do them, then re-arm.


## F-P (2026-10-04, found live after 101 rounds): checkpoint push rc=128 every round
**Symptom:** every zip round logs `ckpt[failed at push: tag rc=0; push rc=128]`.
**Root cause:** the runner's own push uses `credential.helper=` (empty) by design (dialog guard). On https remotes that means `could not read Username` — guaranteed failure. Local tags landed (work never lost) but nothing reached GitHub.
**Fix:** on push rc=128 WITH the credential-failure signature, retry once with `credential.helper=!gh auth git-credential` (verified live: dry-run + real push succeed, no dialog). Pinned in test_final_sweep_pins.py.
**Manual ops (done):** pushed the backlog — karpathy branch force-with-lease to 301e948 (true tip), tags r95/r96/r98/r99 now on origin.

## Ops notes 2026-10-04 (why the loop appeared stuck on one repo)
1. **T&T refusal loop:** canonical had ~18 files of uncommitted parallel-agent work since 10-01; the F0-1 refusal correctly refused to touch it but the loop kept re-picking it. RESOLVED: committed all work as `058dd42` (commit, never wipe). Loop immediately took T&T and launched a real round.
2. **secondbrain quarantine:** worktree r45 removal failed `Filename too long` (242-char Gradle dex paths). RESOLVED: removed via extended-length `\?\` paths, pruned git records, cleared flag.
3. **ai-consultancy quarantine:** r57 held by an ORPHANED `next start` dev server (PID 84052, port 4157, returning 500). Killed the orphan, removed r57, pruned, cleared flag.
4. **zip quarantine (transient):** r102 removal hit a transient Permission denied during round cleanup; nobody holds it now. Removed, cleared.
**All four repos are now unquarantined and the loop is rotating (T&T round in flight).**


## F-Q (2026-10-04, Gene's screenshot): checkpoints panel shows JSON parse error
**Symptom:** pink error box: "checkpoints did not return JSON (Unexpected token 'o', 'own\":12...' is not valid JSON)" — the received string started 16 bytes INTO the payload (mid-\"shown\"), i.e. the desktop bridge clipped the head of a 4,016-byte stdout stream.
**Root cause:** bridge stdout clipping on larger payloads (the code comment already documented a prior clip at 5KB; this one clipped at 4KB).
**Fix:** payload shrunk to ~1.6-2.9KB — summaries capped at 72 chars (widget shows two lines anyway) + leading newline printed (JSON.parse tolerates it; a clipped head still fails loudly instead of half-parsing). 10/10 consecutive runs parse clean. Pinned in test_final_sweep_pins.py.


## F-R / F-S (2026-10-05, Gene requests: plain checkpoint summaries + broken top elapsed)
**F-R (elapsed blank in the widget):** root cause was NOT the producer — activities() emitted elapsed correctly. compact_payload's floor guard listed `elapsed` in ROW_OPTIONAL, and the payload (6.4KB) popped over the 3400B bridge budget EVERY tick, dropping elapsed (and all but 0-1 CoT steps) while rows still carried ~1.3KB of `see` decoration. Fix: shrink `see` blocks (keep url/kind) BEFORE any sacrifice; `elapsed` removed from the droppable list forever. Post-fix: elapsed ships every tick, payload 3.3KB.
**F-S (plain summaries):** checkpoint.py all now generates a 1-2 sentence fifth-grade explanation per checkpoint tag via the fleet LLM (GLM bottle primary, BASTION qwen3.8 fallback, thinking OFF via chat_template_kwargs — 160 max_tokens returned content:null until disabled). Cached forever in state/plain_summaries.json (one-time cost per tag); a poison 'None' cache from the broken first attempt was wiped and regenerated. LIMIT 12->8 rows to stay under the bridge clip (3.3KB measured). Widget shows the plain line first, technical subject dimmed beneath; reload the plugin once to pick it up.
Pins: test_elapsed_is_never_dropped_by_size_guard, test_checkpoints_carry_plain_summaries. Suite 266 passed.


## F-T (2026-10-05): rotation audit — two repos re-quarantined by leaked dev servers
**Finding (Gene: "double-checking it rotates every two rounds"):** rotation policy is `angles_per_visit: 2` and WAS working for the runnable repos (since 10-04 restart: zip 12 rounds, sb 11, tnt 9, interleaved correctly). But T&T re-quarantined 09:16 (worktree r50 Permission denied — transient holders) and ai-c re-quarantined 14:27 (r58 held by THREE orphaned `next start` dev servers — the child spawns them and the runner never kills them at round end).
**Root cause:** `_kill_tree` only fires on TIMEOUT. A round that spawns a detached dev server and finishes leaves the server alive; it locks the worktree, removal fails, repo quarantines. This is the SECOND occurrence (r57 on 10-04).
**Fix (F-T):** `_sweep_worktree_processes(wt)` — at every worktree removal, kill any process whose command line references the worktree path (precise match, one CIM query, skips self). Drill: planted a holder with the path in its cmdline -> swept 2 procs, holder dead, dir removable after handle release. Pin: test_worktree_process_sweep_is_wired_before_removal (AST: sweep runs BEFORE junction cleanup).
**Manual ops:** killed the 3 orphans (PIDs 118204/119064/92264), removed r50/r58, pruned, cleared both quarantine flags. All 4 repos runnable again. Suite 267 passed.


## F-U (2026-10-05): "running but no repo in rotation" — inter-round visibility gap
**Finding (Gene):** loop badge RUNNING, zero repos shown running. VERDICT: the loop was HEALTHY — watched r114 finish (rc=0, commit d6200ed) and rotate to ai-consultancy live (child PID 103220 confirmed). The display gap: activity.py marks a project running only when a live `--resume` python process exists. Between a round's child exiting and the next Popen there are MINUTES of runner work (gate up to 900s, checkpoint, containment, worktree teardown/setup) with no such process — the widget shows an empty rotation during every one of those windows.
**Fix (F-U):** the producer now falls back to the runner heartbeat: a fresh (<4200s = round timeout) `round-started` heartbeat naming the project keeps it "running" through the gap. Verified live: ai-c shows running + elapsed 0m51s. Pin: test_heartbeat_fallback_covers_inter_round_gap. Suite green.


## F-V (2026-10-05, Gene: fifth-grade summary of CURRENT work)
**Request:** like the checkpoint plain summaries, but for the in-flight round — what is it trying to build/prove/fix right now.
**Built:** `checkpoint.py current` — picks the in-flight project (fresh round-started heartbeat, fallback: freshest current_angle.started), feeds the repo brief + angle lens + the actual round prompt to the fleet LLM (same engine, thinking off, GLM primary / BASTION fallback), caches per <project>/<angle>/<round> in the same plain_summaries cache. Widget gets a new "Working on right now" panel above "Current angle" (60s TTL poll, server-side cache makes it free after the first call).
**First live output (ai-c r59, garbage-input angle):** "The worker is testing the website's contact form by typing in weird or broken information to see if it crashes. This helps make sure the site stays safe and keeps working even when someone tries to trick it with bad data."
Pin: test_current_work_verb_emits_plain. Suite 269 passed. Plugin needs one reload.


## F-X (2026-10-05): "status unavailable / 90s timeout" — widget polls share the agent's shell queue
**Root cause (mechanism established):** the widget's runPy -> requestProfile('shell.exec') calls are served through the profile agent's shell/terminal queue — the SAME queue that executes the human's agent sessions (including Picard's diagnostic terminal calls). When that queue is busy (long agent commands running), the widget's 30s status poll times out, and the OLD code blanked the panel on ONE failed poll: "status unavailable — <error>". Backend commands themselves are fast and healthy (measured: activity 1.8s, status 3.6s, checkpoints 3.2s, current 0.7-3.1s warm).
**Fix (F-X):** every polled hook (status, activity, checkpoints, current-work) now keeps the LAST GOOD payload on failure — a busy queue degrades to slightly stale data instead of a wiped page — and timeouts raised 30s/90s -> 120s. first-error-with-no-prior-data still shows the error honestly.
**Verification:** plugin imports clean, 13 lastGood call sites, repo copy synced (83cf78766587). User must reload the plugin once. Suite unaffected (backend untouched).


## F-Y (2026-10-05): widget "stays loading forever" — shell.exec path is fundamentally starved
**Root cause (measured):** a TRIVIAL hermes oneshot now takes 13-69s to start — gateway-stdio.log shows every spawn paying MCP connect storms (obsidian ssh CancelledError every ~2min, OAuth retries for indeed/cloudflare/hugging_face every 5min) and a torn spawn-ledger write. The widget's shell.exec polls each pay that tax and queue behind the human's agent sessions. F-X's last-known-good couldn't help because the FIRST poll of a fresh page load has no prior data -> infinite loading.
**Fix (F-Y): NEW architecture for the widget data path.** scripts/status_server.py — a tiny localhost HTTP server (127.0.0.1:8765, ThreadingHTTPServer, 5-30s per-endpoint cache) that runs the SAME producers (loopctl._liveness, activity.compact_payload, checkpoint.py all/current) and serves /status.json, /activity.json, /checkpoints.json, /current.json. The plugin now fetch()es these DIRECTLY (15-30s abort timeout) — zero agent involvement, no shell queue, no MCP storms. shell.exec remains as automatic fallback if the server is down (60s retry cadence).
**Persistence:** schtasks 'KarpathyStatusServer' at logon runs start_status_server.ps1 (idempotent: checks port 8765 first). Server currently running and verified: all four endpoints answering with live data.
**User action: reload the Karpathy Loop plugin once.**


## F-Z / F-AA (2026-10-05, from Gene's Discord screenshot): every round "SKIPPED (gate failed)" + "angle None"
**F-Z (gate):** Discord showed EVERY round for days as "checkpoint: SKIPPED (not published: gate failed)". Root cause: _run_gate uses shell=True -> cmd.exe, which cannot execute relative forward-slash tool paths (`.venv/Scripts/python.exe` -> "'.venv' is not recognized"). The gate failed in ~0s EVERY round while the same string passed in bash. Fix: on Windows, rewrite relative forward-slash tokens in the gate command to resolved absolute backslash paths (quoted, spaces-safe), including the `cd X && ...` form secondbrain uses. Drill: tnt 185 passed OK, secondbrain 921 passed OK via _run_gate.
**F-AA (angle None):** round-start Discord message read (angle or {}).get("id") but angle_pick stores the id under `angle`; the resolved _aid (id/angle/name chain) two lines up was already correct. Notify now uses _aid.
**Impact:** every round since the gates were configured was rejected on a false gate failure -- real work was committed in worktrees but rolled back/contained as "rejected". Suite 268 green (237 + 31).
