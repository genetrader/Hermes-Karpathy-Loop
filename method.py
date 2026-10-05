#!/usr/bin/env python
"""
project-improver / method.py

THE PROCESS. This is what makes the loop methodical instead of blind.

Every project goes through the SAME six stages, in order. A project cannot
skip a stage: the later stages need the earlier stages' output as input.
Each stage has a concrete artifact it must produce, and the artifact is what
the next stage reads.

  S1 SCOUT      read the codebase, produce an evidence report.
                artifact: state/plan/<project>/01-scout.md
  S2 BASELINE   establish the gate actually runs + record current pass/fail.
                artifact: state/plan/<project>/02-baseline.json
  S3 BACKLOG   rank candidate improvements by ROI, with evidence per item.
                artifact: state/plan/<project>/03-backlog.md
  S4 ITERATE   run N goal-mode loops, ONE backlog item per loop.
                artifact: git commits + checkpoint branches
  S5 REVIEW    adversarial review by a DIFFERENT model family.
                artifact: state/plan/<project>/05-review.md
  S6 VERIFY    re-run the gate, compare against baseline, close or reopen.
                artifact: state/plan/<project>/06-verify.json

The stage is stored in state/plan.json per project, and the control panel reads
it — that is the "what's planned / what's been done" you wanted visible.

Usage:
  python method.py stages                          list the process
  python method.py status                          per-project stage table
  python method.py prompt <project> <stage>        emit the worker prompt for a stage
  python method.py advance <project> <stage> [ok]  record a stage result
  python method.py build-prompts <project>         write all 6 prompts to disk
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLAN = ROOT / "state" / "plan.json"
PLANDIR = ROOT / "state" / "plan"

STAGES = [
    ("S1", "scout",
     "Read-only reconnaissance of the codebase. NO edits.",
     "state/plan/<project>/01-scout.md",
     "Inventory the real structure: entry points, build/run commands (verified "
     "by reading the actual files, not guessed), test presence, dependencies, "
     "dead code, TODOs, and the 5 most fragile spots. Every claim needs a "
     "file:line anchor. If you cannot anchor it, mark it HYPOTHESIS."),
    ("S2", "baseline",
     "Establish the gate runs. Record the true starting point.",
     "state/plan/<project>/02-baseline.json",
     "Run the project's test command by hand. Record the EXACT command, exit "
     "code, pass/fail counts, and wall time. If there is no test command, say "
     "so plainly and propose the cheapest one that could exist. Never report a "
     "gate result you did not actually execute."),
    ("S3", "backlog",
     "Rank candidate improvements by ROI.",
     "state/plan/<project>/03-backlog.md",
     "From the scout report, produce a ranked backlog. Each item: what, why it "
     "matters, evidence anchor, effort (S/M/L), risk, and how a gate would "
     "prove it. Order by ROI, not by ease. Explicitly separate FIXES (broken) "
     "from IMPROVEMENTS (works but weak) from REMOVALS (delete it)."),
    ("S4", "iterate",
     "Run the loops — ONE backlog item per loop.",
     "git checkpoint commit per passing item",
     "Work the backlog top-down. Each loop: (1) state which item you are doing, "
     "(2) make the change, (3) run the gate, (4) if it fails, repair and retry "
     "WITHIN the loop, (5) if it passes, commit a checkpoint and move to the "
     "next item. Do NOT batch multiple items into one unverified commit."),
    ("S5", "review",
     "Adversarial review by a different model family.",
     "state/plan/<project>/05-review.md",
     "You are the REVIEWER, not the author. Read the diff only — not the "
     "author's justification. For each change: does it actually address the "
     "backlog item, does it introduce a regression, is it tested, is it "
     "over-engineered? Return a verdict per item: ACCEPT / REJECT / NEEDS-WORK "
     "with a concrete reason. Rejecting good-sounding work is expected."),
    ("S6", "verify",
     "Re-run the gate, compare to baseline, close or reopen.",
     "state/plan/<project>/06-verify.json",
     "Re-run the EXACT baseline command. Compare pass/fail counts and wall time "
     "against 02-baseline.json. Any regression fails verification regardless of "
     "how good the diff looked. Record what was closed, what was reopened, and "
     "what is now the new baseline for next time."),
]

STAGE_BY_KEY = {s[1]: s for s in STAGES}
STAGE_INDEX = {s[1]: i for i, s in enumerate(STAGES)}


# ------------------------------------------------------------------ state

def load_plan() -> dict:
    try:
        return json.loads(PLAN.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_plan(d: dict) -> None:
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(d, indent=2), encoding="utf-8")


def project_dir(name: str) -> Path:
    d = PLANDIR / name
    d.mkdir(parents=True, exist_ok=True)
    plans = ROOT / "plans"
    plans.mkdir(exist_ok=True)
    return d


def manifest_project(name: str) -> dict | None:
    try:
        import yaml
        m = yaml.safe_load((ROOT / "improve.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    for p in (m.get("projects") or []):
        if p.get("name") == name:
            return p
    return None


# ------------------------------------------------------------------ commands

def cmd_stages():
    print(f"{'':4}{'STAGE':12}{'ARTIFACT':44}WHAT IT PROVES")
    print("-" * 116)
    for code, key, what, art, _ in STAGES:
        print(f"{code:4}{key:12}{art:44}{what}")
    print()
    print("A project cannot skip a stage: each stage consumes the previous")
    print("stage's artifact. The control panel shows which stage each project")
    print("is on.")


def cmd_status():
    plan = load_plan()
    try:
        import yaml
        m = yaml.safe_load((ROOT / "improve.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        m = {}
    projs = m.get("projects") or []
    if not projs:
        print("no projects in improve.yaml")
        return
    print(f"{'PROJECT':26}{'STAGE':12}{'DONE':6}{'STARTED':14}ARTIFACTS")
    print("-" * 96)
    for p in projs:
        st = plan.get(p["name"]) or {}
        cur = st.get("stage") or "not started"
        done = ", ".join(sorted(st.get("done", {}).keys())) or "—"
        started = time.strftime("%m-%d %H:%M", time.localtime(st["started_at"])) \
            if st.get("started_at") else "—"
        d = PLANDIR / p["name"]
        arts = len(list(d.glob("*"))) if d.exists() else 0
        flag = "" if p.get("enabled") else "  (parked)"
        print(f"{p['name']:26}{cur:12}{str(len(st.get('done', {}))):6}{started:14}"
              f"{arts} file(s){flag}")


def cmd_prompt(project: str, stage: str):
    p = manifest_project(project)
    if not p:
        raise SystemExit(f"unknown project '{project}' — not in improve.yaml")
    if stage not in STAGE_BY_KEY:
        raise SystemExit(f"unknown stage '{stage}'. options: {', '.join(STAGE_BY_KEY)}")
    code, key, what, art, instruction = STAGE_BY_KEY[stage]
    d = project_dir(project)
    path = str(d / f"{code}-{key}.md")
    # The PROMPT is not the ARTIFACT. Writing the prompt to the artifact path
    # meant the worker's first job (writing its findings) destroyed its own
    # instructions. Keep them in separate files: prompt-S1-scout.md carries
    # the brief; 01-scout.md is what the worker produces.
    prompt_path = str(d / f"prompt-{code}-{key}.md")
    loops = p.get("loops", 5)
    prompt = f"""==============================================================
PROJECT IMPROVER — {code} {key.upper()}
project : {project}
path    : {p['path']}
stage   : {code} ({STAGE_INDEX[stage] + 1} of {len(STAGES)}) — {what}
artifact: {path}
==============================================================

BOUNDARIES
  This repo only: {p['path']}
  {p.get('boundary', '').strip()}

OBJECTIVE
  {p.get('objective', '').strip()}

ACCEPTANCE CRITERIA
  {p.get('acceptance', '').strip()}

GATE
  {p.get('gate') or '(none — this project has no test command; S2 must propose one)'}

YOUR TASK THIS STAGE ({code} {key})
  {instruction}

RULES
  - Write the artifact to: {d / art.split('<project>/')[-1]}
  - Every factual claim needs a file:line anchor, or be marked HYPOTHESIS.
  - Never report a command result you did not actually execute.
  - Do not skip ahead to a later stage. The next stage needs this artifact.
  - If you are blocked on a decision only Gene can make, ask ONE question:
      python C:\\CODING\\project-improver\\discord_notify.py ask "{project}" "<question>"
    then stop and wait. Do not guess and continue.
  - Report progress to Discord:
      python C:\\CODING\\project-improver\\discord_notify.py progress "{project}" "<what happened>"

ITERATION BUDGET FOR THIS TURN: {loops} loops (used in S4)
"""
    d.mkdir(parents=True, exist_ok=True)
    Path(prompt_path).write_text(prompt, encoding="utf-8")
    print(prompt)
    return prompt_path


def cmd_build_prompts(project: str):
    outs = []
    for _, stage, *_ in STAGES:
        outs.append(cmd_prompt(project, stage))
    print(f"\nwrote {len(outs)} stage prompts under {PLANDIR / project}")


def cmd_advance(project: str, stage: str, ok: str = "ok"):
    if stage not in STAGE_BY_KEY:
        raise SystemExit(f"unknown stage '{stage}'")
    plan = load_plan()
    st = plan.setdefault(project, {"done": {}})
    st.setdefault("started_at", time.time())
    st["done"][stage] = {
        "at": time.time(),
        "ok": ok.lower() in {"ok", "true", "1", "yes", "pass"},
    }
    n = len(st["done"])
    st["percent"] = round(100 * n / len(STAGES))
    st["stage"] = f"{n}/{len(STAGES)}"
    save_plan(plan)
    print(f"{project}: {stage} recorded ({ok}). {n}/{len(STAGES)} stages complete "
          f"({st['percent']}%)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        cmd_stages()
    elif args[0] == "stages":
        cmd_stages()
    elif args[0] == "status":
        cmd_status()
    elif args[0] == "prompt" and len(args) >= 3:
        cmd_prompt(args[1], args[2])
    elif args[0] == "build-prompts" and len(args) >= 2:
        cmd_build_prompts(args[1])
    elif args[0] == "advance" and len(args) >= 3:
        cmd_advance(args[1], args[2], args[3] if len(args) > 3 else "ok")
    else:
        print(__doc__)