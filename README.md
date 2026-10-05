# Hermes Karpathy Loop

**An autonomous, self-improving code loop for your own repositories.**

The Karpathy Loop runs a continuous improve-review-improve cycle over the git
repos you point it at:

1. **Pick** a project and a review angle (robustness, dead code, invisible
   state, lying tests, ... 8 families, 168+ concrete prompts).
2. **Isolate** — every round works in a disposable `git worktree` branched from
   your canonical checkout. Your working tree is never touched.
3. **Work** — an agent (any OpenAI-compatible model, local or cloud) hunts one
   specific flaw, fixes it, and writes real tests.
4. **Gate** — the round is only accepted if YOUR gate command passes (pytest,
   npm test, whatever you configure). No gate, no merge.
5. **Checkpoint** — accepted work is committed, tagged `kp/<project>/r<NN>`,
   and optionally pushed to GitHub. Rejected work is rolled back and the repo
   quarantined if anything looks unsafe.
6. **Rotate** — to the next project, forever.

## Why it exists

Most "AI improves your codebase" tools demo well and drift. This one is built
around a harsh acceptance chain, because the interesting failure mode of an
autonomous loop is not "it does nothing" — it is "it confidently merges junk":

- **Acceptance = exit 0 AND your gate passed AND the git head advanced AND the
  proposal survived review.** Four conditions, all required.
- **A checkpoint is a claim**: tags are cut only after the round is fully
  persisted, never before.
- **Refusal over rollback**: a dirty tree at round start is refused, never
  wiped. The loop would rather stall than destroy uncommitted human work.
- **Containment**: rejected rounds roll the worktree back to the pre-round
  HEAD; if even that cannot be verified, the repo is quarantined and parked
  until a human looks.
- **Honesty in the UI**: the dashboard computes liveness from the runner's
  heartbeat and live processes — a wedged loop reads as WEDGED, never as
  "running".

## What's in here

| Path | What it is |
|---|---|
| `karpathy_runner.py` | The round engine: worktrees, gates, containment, checkpointing, rotation |
| `loopctl.py` | Operator control plane: `start/pause/stop/status`, liveness + wedge detection |
| `checkpoint.py` | Tag/push checkpointing + the plain-English summarizer (5th-grade explanations of every round) |
| `activity.py`, `monitor.py`, `monitor.html` | Dashboard payloads + a standalone HTML monitor |
| `scope.py`, `surface_pick.py`, `round_prompt.py`, `round_evidence.py` | Campaign/surface tracking and per-round prompt/evidence discipline |
| `angles*.py`, `angles.yaml`, `angle_prompts.yaml` | The review-angle library |
| `widget/plugin.js` | Hermes Desktop plugin: live dashboard page (status, repos, live thread, checkpoints, plain-English "working on right now") |
| `scripts/status_server.py` | Local HTTP server that feeds the widget without touching the agent shell queue |
| `tests/` | ~270 tests pinning the safety properties (acceptance chain, containment wiring, refusal-on-dirty, wedge detection, ...) |
| `improve.yaml` | Your project manifest (example provided) |

## Quick start

```bash
git clone https://github.com/genetrader/Hermes-Karpathy-Loop.git
cd Hermes-Karpathy-Loop
pip install pyyaml requests

# 1. tell it about your projects (name, path, gate command)
cp improve.yaml my-improve.yaml   # then edit

# 2. start the loop
python loopctl.py start

# 3. watch it
python loopctl.py status
python monitor.py --serve          # or open the desktop widget
```

Model + GitHub + notification settings live in environment variables and
`loopctl.py config` — see `METHOD.md` for the full configuration contract.

## Status

Production-tested on four active repositories; 100+ accepted rounds, every
safety property pinned by tests. Built for (and with) the
[Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop app, but
the engine itself is just Python + git and runs anywhere.

## License

MIT
