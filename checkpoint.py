#!/usr/bin/env python3
"""Checkpoint engine for the Karpathy Loop.

The safety net. Every round is bracketed by a real git checkpoint so any
round can be rolled back in one command -- the thing the loop promised in
prose and never actually did (no `git tag` was ever executed; `git tag -l`
was empty on a repo that had been "checkpointed" twice).

Design (locked by the operator 2026-09-22):
  * PUBLIC repo   -> fork to the user's account, work on the fork, never
                     touch upstream; a PR upstream only on explicit approval.
  * no remote / not a repo -> create a PRIVATE repo automatically and push.
  * Private repo  -> branch + tag + push to the user's own repo.
  * Backup        -> push branches AND tags every round (safest).

Checkpoint tags are annotated and namespaced so they are trivially listable
and never collide with a project's own tags. NOTE: git forbids a tag that is a
path-prefix of another (`kp/p/1` cannot coexist with `kp/p/1/start`), so the
round tag is a LEAF name, not a prefix — `r01` / `r01-start`:

    kp/<project>/r<NN>          a round's END state (the rollback point)
    kp/<project>/r<NN>-start    the round's START state (the diff base)

Rollback:
    git checkout kp/<project>/r<NN>

Verbs:
    prepare   ensure remote exists (fork/private), record the START checkpoint
    finish    commit + tag the END checkpoint, push branch and tags
    list      show every kp/ checkpoint for a project
    rollback  print/execute the checkout for a given checkpoint
    status    one-line health for a project
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
import re
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
CKPT_LOG = STATE / "checkpoints.json"

TAG_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _git_author() -> tuple[str, str]:
    """(name, email) for the loop's own commits/tags, from settings
    (git.author_name / git.author_email). Falls back to the generic
    defaults if the settings layer itself is unusable."""
    try:
        import settings as _S
        return _S.git_author()
    except Exception:
        return ("Karpathy Loop", "loop@local")


# --------------------------------------------------------------------------
# shell helpers
# --------------------------------------------------------------------------
def run(args, cwd, check=False, timeout=300):
    """Run a command, return (rc, stdout+stderr). Never raises on rc!=0."""
    try:
        p = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        out = (p.stdout or "") + (p.stderr or "")
        if check and p.returncode != 0:
            raise RuntimeError(f"{' '.join(args)} -> rc={p.returncode}: {out.strip()[:400]}")
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(args)}"
    except FileNotFoundError as e:
        return 127, str(e)


def git(repo: Path, *args, check=False, timeout=300):
    return run(["git", "-C", str(repo), *args], cwd=repo, check=check, timeout=timeout)


def gh(*args, check=False, timeout=300):
    import settings as _S
    return run([_S.gh_cli(), *args], cwd=HERE, check=check, timeout=timeout)


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------
def load_log() -> dict:
    if CKPT_LOG.exists():
        try:
            return json.loads(CKPT_LOG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_log(d: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    CKPT_LOG.write_text(json.dumps(d, indent=2), encoding="utf-8")


def record(project: str, entry: dict) -> None:
    d = load_log()
    d.setdefault(project, {"checkpoints": []})
    d[project]["checkpoints"].append(entry)
    d[project]["checkpoints"] = d[project]["checkpoints"][-200:]
    save_log(d)


# --------------------------------------------------------------------------
# repo facts
# --------------------------------------------------------------------------
def is_repo(repo: Path) -> bool:
    rc, _ = git(repo, "rev-parse", "--git-dir")
    return rc == 0


def current_branch(repo: Path) -> str:
    rc, out = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if rc == 0 else ""


def remotes(repo: Path) -> dict:
    rc, out = git(repo, "remote", "-v")
    if rc != 0:
        return {}
    seen = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return seen


def parse_github(url: str):
    """https://github.com/O/R(.git) | git@github.com:O/R.git -> (owner, repo)"""
    if not url:
        return None
    m = re.search(r"github\.com[/:]([^/]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
    return (m.group(1), m.group(2)) if m else None


def gh_repo_meta(owner: str, name: str):
    rc, out = gh("repo", "view", f"{owner}/{name}", "--json",
                 "name,isPrivate,isFork,parent,visibility,defaultBranchRef")
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except Exception:
        # gh can emit trailing noise; take the first JSON document
        try:
            return json.JSONDecoder().raw_decode(out)[0]
        except Exception:
            return None


def short_sha(repo: Path, ref: str = "HEAD") -> str:
    rc, out = git(repo, "rev-parse", "--short", ref)
    return out.strip() if rc == 0 else ""


# --------------------------------------------------------------------------
# remote assurance -- fork (public) / create private (no remote)
# --------------------------------------------------------------------------
def ensure_remote(repo: Path, project: str, owner_hint: str | None = None,
                  dry: bool = False) -> dict:
    """Guarantee this repo has a SAFE push target before the loop touches it.

    public upstream -> fork to the user's account and point `origin` at the fork
                      (upstream preserved as a separate remote, never pushed to)
    no remote      -> create a PRIVATE repo and push
    private own    -> nothing to do
    """
    res = {"action": "none", "safe": True, "detail": ""}

    # GitHub master switch OFF (the shipped default): checkpoints are LOCAL
    # tags only. No gh calls, no fork/repo creation, no network -- and this
    # is a SUPPORTED mode, so it reports safe, not a refusal.
    try:
        import settings as _S
        if not _S.github_enabled():
            res.update(action="local-only", safe=True,
                       detail="GitHub disabled by settings -- tags stay local")
            return res
    except Exception:
        pass

    if not is_repo(repo):
        res.update(safe=False, action="not-a-repo",
                   detail="not a git repo; checkpointing impossible")
        return res

    rem = remotes(repo)
    origin = rem.get("origin")
    url = origin or rem.get("upstream") or next(iter(rem.values()), None)
    gh_pair = parse_github(url or "")

    if gh_pair:
        meta = gh_repo_meta(*gh_pair)
        if meta is None:
            res.update(safe=False, action="remote-unreadable",
                       detail=f"cannot read {gh_pair[0]}/{gh_pair[1]} via gh")
            return res

        if meta.get("isFork") and meta.get("parent"):
            res.update(action="already-fork", detail="origin is already a fork")
            return res

        if meta.get("isPrivate"):
            res.update(action="private-ok",
                       detail=f"{gh_pair[0]}/{gh_pair[1]} is private")
            return res

        # PUBLIC, not a fork -> fork it.
        if dry:
            res.update(action="would-fork",
                       detail=f"public repo {gh_pair[0]}/{gh_pair[1]} would be forked")
            return res

        rc, out = gh("repo", "fork", f"{gh_pair[0]}/{gh_pair[1]}", "--clone=false")
        if rc != 0 and "already exists" not in out.lower():
            res.update(safe=False, action="fork-failed", detail=out[:300])
            return res

        rc, me = gh("api", "user", "--jq", ".login")
        my = me.strip() if rc == 0 else (owner_hint or "")
        if not my:
            res.update(safe=False, action="fork-unverified",
                       detail="forked but could not resolve your login")
            return res

        # preserve upstream, move origin to the fork
        if rem.get("upstream") is None:
            git(repo, "remote", "add", "upstream", url)
        fork_url = f"https://github.com/{my}/{gh_pair[1]}.git"
        if rem.get("origin"):
            git(repo, "remote", "set-url", "origin", fork_url)
        else:
            git(repo, "remote", "add", "origin", fork_url)

        rc, out = git(repo, "push", "-u", "origin", "HEAD", timeout=600)
        res.update(action="forked", detail=f"forked to {my}/{gh_pair[1]}; "
                                           f"upstream preserved; origin->fork")
        if rc != 0:
            res.update(safe=False, action="fork-push-failed", detail=out[:300])
        return res

    # No GitHub remote at all -> create a PRIVATE repo.
    if dry:
        res.update(action="would-create-private",
                   detail=f"no GitHub remote; would create private repo for '{project}'")
        return res

    name = re.sub(r"[^A-Za-z0-9._-]+", "-", project).strip("-") or "project"
    _aname, _aemail = _git_author()
    # is this local repo already git-initialised with commits?
    rc, out = git(repo, "rev-parse", "HEAD")
    if rc != 0:
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=%s" % _aemail, "-c", "user.name=%s" % _aname,
            "commit", "-m", "chore: initial commit before Karpathy Loop")
        rc, out = git(repo, "rev-parse", "HEAD")

    rc, me = gh("api", "user", "--jq", ".login")
    my = me.strip() if rc == 0 else (owner_hint or "")
    if not my:
        res.update(safe=False, action="no-login",
                   detail="gh not authenticated; cannot create a remote")
        return res

    rc, out = gh("repo", "create", f"{my}/{name}", "--private",
                 "--source", str(repo), "--remote", "origin", "--push")
    if rc != 0 and "already exists" not in out.lower():
        res.update(safe=False, action="create-failed", detail=out[:300])
        return res

    if "already exists" in out.lower():
        if not remotes(repo).get("origin"):
            git(repo, "remote", "add", "origin",
                f"https://github.com/{my}/{name}.git")
        rc, out = git(repo, "push", "-u", "origin", "HEAD", timeout=600)

    res.update(action="created-private",
               detail=f"created PRIVATE {my}/{name} and pushed")
    return res


# --------------------------------------------------------------------------
# checkpoints
# --------------------------------------------------------------------------
def make_checkpoint(repo: Path, tag: str, message: str, push: bool = True,
                    dry: bool = False) -> dict:
    """Annotated tag at HEAD (+ push). Idempotent: refuses to move an existing tag."""
    if not TAG_RE.match(tag):
        return {"ok": False, "detail": f"invalid tag name: {tag}"}

    rc, _ = git(repo, "rev-parse", "--verify", f"refs/tags/{tag}")
    if rc == 0:
        return {"ok": True, "tag": tag, "detail": "already exists (not moved)"}

    if dry:
        return {"ok": True, "tag": tag, "detail": "dry-run"}

    _aname, _aemail = _git_author()
    rc, out = git(repo, "-c", "user.email=%s" % _aemail,
                  "-c", "user.name=%s" % _aname,
                  "tag", "-a", tag, "-m", message)
    if rc != 0:
        return {"ok": False, "tag": tag, "detail": out[:300]}

    res = {"ok": True, "tag": tag, "sha": short_sha(repo), "pushed": False}

    try:
        import settings as _S
        push = push and _S.push_enabled()
    except Exception:
        pass

    if push:
        rem = remotes(repo)
        if "origin" in rem:
            rc, out = git(repo, "push", "origin", f"refs/tags/{tag}", timeout=600)
            res["pushed"] = rc == 0
            if rc != 0:
                res["detail"] = f"tag created locally; push failed: {out[:200]}"
        else:
            res["detail"] = "no origin remote; tag is local only"
    return res


def commit_all(repo: Path, message: str) -> dict:
    rc, out = git(repo, "status", "--porcelain")
    if rc != 0:
        return {"ok": False, "detail": out[:200]}
    if not out.strip():
        return {"ok": True, "committed": False, "detail": "working tree clean"}

    git(repo, "add", "-A")
    _aname, _aemail = _git_author()
    rc, out = git(repo, "-c", "user.email=%s" % _aemail,
                  "-c", "user.name=%s" % _aname, "commit", "-m", message)
    if rc != 0:
        return {"ok": False, "committed": False, "detail": out[:300]}
    return {"ok": True, "committed": True, "sha": short_sha(repo)}


def push_branch(repo: Path, project: str) -> dict:
    try:
        import settings as _S
        if not _S.push_enabled() or not _S.push_branches():
            return {"ok": True, "skipped": True,
                    "detail": "branch push disabled by settings (github off / "
                              "push_branches false)"}
    except Exception:
        pass
    rem = remotes(repo)
    if "origin" not in rem:
        return {"ok": False, "detail": "no origin to push to"}
    br = current_branch(repo)
    if not br or br == "HEAD":
        return {"ok": False, "detail": "detached HEAD; not pushing"}

    ok_tag = "--set-upstream" if f"refs/heads/{br}" not in _remote_branches(repo) else ""
    args = ["push", "origin", f"refs/heads/{br}:refs/heads/{br}"]
    if ok_tag:
        args.insert(1, "-u")
    rc, out = git(repo, *args, timeout=900)
    if rc != 0:
        return {"ok": False, "detail": out[:300]}
    return {"ok": True, "branch": br}


_cached_remote_branches: dict = {}


def _remote_branches(repo: Path):
    key = str(repo)
    if key in _cached_remote_branches:
        return _cached_remote_branches[key]
    rc, out = git(repo, "ls-remote", "--heads", "origin", timeout=300)
    refs = {ln.split("\t")[-1] for ln in out.splitlines() if "\t" in ln} if rc == 0 else set()
    _cached_remote_branches[key] = refs
    return refs


# --------------------------------------------------------------------------
# verbs
# --------------------------------------------------------------------------
def cmd_prepare_local(repo: Path, project: str, round_no: int, angle: str = "",
                      push: bool = True, owner: str | None = None) -> dict:
    """Programmatic prepare (no argparse) — used by improver.py's rotator.

    Same behaviour as the `prepare` verb, returning the result dict instead of
    printing JSON. Kept next to cmd_prepare so the two cannot drift.
    """
    repo = Path(repo).resolve()
    if not is_repo(repo):
        return {"ok": False, "detail": f"{repo} is not a git repo"}

    out: dict = {"project": project, "round": round_no, "repo": str(repo),
                 "branch": current_branch(repo)}

    rem = ensure_remote(repo, project, owner)
    out["remote"] = rem
    if not rem.get("safe"):
        out.update(ok=False,
                   detail=f"remote unsafe: {rem.get('action')} {rem.get('detail')}")
        return out

    out["baseline_commit"] = commit_all(
        repo, f"chore(kp): baseline before round {round_no} ({angle or 'session'})")

    start_tag = f"kp/{project}/r{round_no:02d}-start"
    t = make_checkpoint(repo, start_tag,
                        f"Karpathy Loop: round {round_no} start ({angle or 'session'})",
                        push=push)
    out["start_tag"] = t
    out["ok"] = bool(t.get("ok"))
    out["branch_push"] = push_branch(repo, project)

    record(project, {"kind": "start", "round": round_no, "angle": angle,
                     "tag": start_tag, "sha": short_sha(repo), "at": time.time()})
    return out


def cmd_finish_local(repo: Path, project: str, round_no: int, angle: str = "",
                     summary: str = "", gate: str = "", push: bool = True) -> dict:
    """Programmatic finish (no argparse). Closes a round with an END checkpoint."""
    repo = Path(repo).resolve()
    if not is_repo(repo):
        return {"ok": False, "detail": f"{repo} is not a git repo"}

    out: dict = {"project": project, "round": round_no, "repo": str(repo)}
    out["commit"] = commit_all(
        repo, f"kp({angle or 'session'}): round {round_no} — {summary or 'accepted round'}")

    end_tag = f"kp/{project}/r{round_no:02d}"
    t = make_checkpoint(repo, end_tag,
                        f"Karpathy Loop: round {round_no} end ({angle or 'session'})",
                        push=push)
    out["end_tag"] = t
    out["ok"] = bool(t.get("ok"))
    out["branch_push"] = push_branch(repo, project)

    record(project, {"kind": "end", "round": round_no, "angle": angle,
                     "tag": end_tag, "sha": short_sha(repo),
                     "gate": gate, "at": time.time()})
    return out


def cmd_prepare(a):
    repo = Path(a.repo).resolve()
    project = a.project
    round_no = a.round
    angle = a.angle or "session"

    if not is_repo(repo):
        print(json.dumps({"ok": False, "detail": f"{repo} is not a git repo"}))
        return 2

    out = {"project": project, "round": round_no, "repo": str(repo),
           "branch": current_branch(repo)}

    rem = ensure_remote(repo, project, a.owner, dry=a.dry_run)
    out["remote"] = rem
    if not rem.get("safe"):
        out["ok"] = False
        out["detail"] = f"remote unsafe: {rem.get('action')} {rem.get('detail')}"
        print(json.dumps(out, indent=2))
        return 3

    if a.dry_run:
        out.update(ok=True, dry_run=True)
        print(json.dumps(out, indent=2))
        return 0

    # commit any pre-existing dirt so the START tag is a true baseline
    c = commit_all(repo, f"chore(kp): baseline before round {round_no} ({angle})")
    out["baseline_commit"] = c

    start_tag = f"kp/{project}/r{round_no:02d}-start"
    t = make_checkpoint(repo, start_tag,
                        f"Karpathy Loop: round {round_no} start ({angle})",
                        push=a.push)
    out["start_tag"] = t
    out["ok"] = bool(t.get("ok"))

    b = push_branch(repo, project)
    out["branch_push"] = b

    record(project, {"kind": "start", "round": round_no, "angle": angle,
                     "tag": start_tag, "sha": short_sha(repo),
                     "at": time.time()})
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 4


def cmd_finish(a):
    repo = Path(a.repo).resolve()
    project = a.project
    round_no = a.round
    angle = a.angle or "session"

    if not is_repo(repo):
        print(json.dumps({"ok": False, "detail": f"{repo} is not a git repo"}))
        return 2

    out = {"project": project, "round": round_no, "repo": str(repo)}

    c = commit_all(repo, f"kp({angle}): round {round_no} — {a.summary or 'accepted round'}")
    out["commit"] = c

    end_tag = f"kp/{project}/r{round_no:02d}"
    t = make_checkpoint(repo, end_tag,
                        f"Karpathy Loop: round {round_no} end ({angle})",
                        push=a.push)
    out["end_tag"] = t
    out["ok"] = bool(t.get("ok"))

    b = push_branch(repo, project)
    out["branch_push"] = b

    record(project, {"kind": "end", "round": round_no, "angle": angle,
                     "tag": end_tag, "sha": short_sha(repo),
                     "gate": a.gate, "at": time.time()})
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 4


def cmd_list(a):
    repo = Path(a.repo).resolve() if a.repo else None
    if a.repo and is_repo(repo):
        rc, out = git(repo, "tag", "-l", f"kp/{a.project}/*", "--sort=creatordate")
        rows = []
        for tag in [t for t in out.splitlines() if t.strip()]:
            rc2, meta = git(repo, "for-each-ref", "--format=%(creatordate:iso-strict)|%(objectname:short)|%(subject)",
                            f"refs/tags/{tag}")
            parts = meta.split("|", 2)
            rows.append({"tag": tag.strip(),
                         "created": parts[0] if parts else "",
                         "sha": parts[1] if len(parts) > 1 else "",
                         "subject": parts[2] if len(parts) > 2 else ""})
        print(json.dumps({"project": a.project, "count": len(rows), "checkpoints": rows},
                         indent=2))
        return 0
    # fall back to the ledger
    d = load_log().get(a.project, {})
    print(json.dumps({"project": a.project,
                      "count": len(d.get("checkpoints", [])),
                      "checkpoints": d.get("checkpoints", [])[-40:]}, indent=2))
    return 0


def cmd_rollback(a):
    repo = Path(a.repo).resolve()
    if not is_repo(repo):
        print(json.dumps({"ok": False, "detail": "not a git repo"}))
        return 2
    tag = a.tag
    if not tag.startswith("kp/"):
        # accept "1", "r1", "r01", "01-start", "r01-start"
        t = tag.strip()
        m = re.match(r"^r?(\d+)(-start)?$", t, re.I)
        if m:
            t = f"r{int(m.group(1)):02d}" + ("-start" if m.group(2) else "")
        tag = f"kp/{a.project}/{t}"
    rc, out = git(repo, "rev-parse", "--verify", f"refs/tags/{tag}")
    if rc != 0:
        print(json.dumps({"ok": False, "detail": f"no such checkpoint: {tag}"}))
        return 3
    if not a.yes:
        rc, cur = git(repo, "status", "--porcelain")
        print(json.dumps({
            "ok": True, "dry_run": True, "tag": tag,
            "command": f'git -C "{repo}" checkout {tag}',
            "note": "re-run with --yes to execute. "
                    + ("working tree is dirty; commit or stash first." if cur.strip() else "tree clean.")
        }, indent=2))
        return 0
    rc, out = git(repo, "checkout", tag)
    print(json.dumps({"ok": rc == 0, "tag": tag, "detail": out[:300]}, indent=2))
    return 0 if rc == 0 else 4


def cmd_status(a):
    repo = Path(a.repo).resolve()
    s = {"project": a.project, "repo": str(repo), "exists": repo.exists()}
    if not repo.exists():
        print(json.dumps(s, indent=2))
        return 2
    s["is_git"] = is_repo(repo)
    if s["is_git"]:
        s["branch"] = current_branch(repo)
        s["head"] = short_sha(repo)
        rem = remotes(repo)
        s["remotes"] = rem
        pair = parse_github(rem.get("origin", ""))
        if pair:
            meta = gh_repo_meta(*pair)
            s["github"] = {
                "repo": f"{pair[0]}/{pair[1]}",
                "private": bool(meta.get("isPrivate")) if meta else None,
                "is_fork": bool(meta.get("isFork")) if meta else None,
                "visibility": (meta or {}).get("visibility"),
            }
        rc, out = git(repo, "status", "--porcelain")
        s["dirty_files"] = len([l for l in out.splitlines() if l.strip()]) if rc == 0 else None
        rc, out = git(repo, "tag", "-l", f"kp/{a.project}/*")
        s["checkpoint_tags"] = len([t for t in out.splitlines() if t.strip()]) if rc == 0 else 0
        d = load_log().get(a.project, {})
        s["ledger_entries"] = len(d.get("checkpoints", []))
    print(json.dumps(s, indent=2))
    return 0


# ---------------------------------------------------------------- plain talk
# F-S (2026-10-05, the operator: "explain what was done as if I'm in fifth grade"):
# every checkpoint's git subject is engineer-speak. This translates each tag's
# subject into 1-2 plain sentences, ONCE per tag, cached on disk -- the panel
# polls every 30s and must never pay for (or wait on) an LLM call per render.
PLAIN_CACHE = HERE / "state" / "plain_summaries.json"


def _summary_endpoints() -> list:
    """[(url, model), ...] for the plain-English summary LLM, read FRESH from
    settings -- models.summary_url / summary_model (+ _2 fallback), env names
    KL_LLM_URL / KL_LLM_MODEL. An OpenAI-compatible /v1/chat/completions
    endpoint. EMPTY when unconfigured: summary generation is then simply
    skipped -- there are no hardcoded servers or model names anywhere."""
    try:
        import settings as _S
        eps = _S.summary_endpoints()
        if eps:
            return eps
    except Exception:
        pass
    # historical env names (settings folds these too; belt and braces so the
    # summaries keep working if the settings layer itself is broken)
    out = []
    for uk, mk in (("KL_LLM_URL", "KL_LLM_MODEL"), ("KL_LLM_URL_2", "KL_LLM_MODEL_2")):
        u = os.environ.get(uk)
        if u:
            out.append((u, os.environ.get(mk) or "gpt-4o-mini"))
    return out


def _load_plain_cache() -> dict:
    try:
        return json.loads(PLAIN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _plain_for(tag: str, subject: str, project: str) -> str:
    """Layman summary for one checkpoint, cached forever. Returns "" on any
    failure -- the panel then falls back to the technical summary."""
    cache = _load_plain_cache()
    hit = cache.get(tag)
    if isinstance(hit, str):
        return hit
    if not subject.strip():
        return ""
    sysmsg = ("You explain software changes to a curious fifth grader. "
              "Plain words, no jargon, no code identifiers, no file names. "
              "1-2 sentences, under 40 words. Start with a verb like Fixed, "
              "Added, Made, or Improved.")
    usermsg = ("Change made to the %s project:\n%s\n\n"
               "Explain in 1-2 simple sentences what this did for the person "
               "using the app." % (project, subject[:600]))
    for url, model in _summary_endpoints():
        try:
            # Thinking models burn small max_tokens budgets on hidden
            # reasoning and return content:null (measured: 160 tokens -> None
            # every call). Disable thinking where supported (vLLM
            # chat_template_kwargs) and budget generously.
            body = json.dumps({
                "model": model, "max_tokens": 400, "temperature": 0.2,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": sysmsg},
                             {"role": "user", "content": usermsg}],
            }).encode("utf-8")
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            # worker models may emit <think> blocks -- strip them
            text = re.sub(r"<think>.*?</think>", "", str(text), flags=re.S)
            text = re.sub(r"\s+", " ", text).strip()[:320]
            if text:
                break
        except Exception:
            continue
    else:
        return ""   # every endpoint failed: not cached, retry on a later poll
    if not text:
        return ""
    cache[tag] = text
    try:
        PLAIN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PLAIN_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except Exception:
        pass
    return text


def _inflight_round(project: str, round_no: int | None = None) -> tuple:
    """(entry, current_angle) for a project from the threads registry, or
    (None, {}) when it has none. When round_no is given the angle must belong
    to that round (or not name one), so a stale current_angle from a previous
    round never captions a fresh message."""
    try:
        reg = json.loads((HERE / "state" / "threads.json").read_text(encoding="utf-8"))
    except Exception:
        reg = {}
    e = reg.get(project) or {}
    ca = e.get("current_angle") or {}
    if ca and round_no is not None and ca.get("round") not in (None, round_no):
        return None, {}
    return (e, ca) if ca else (None, {})


def _current_subject(brief: str, ca: dict, project: str) -> str:
    """The engineer-speak description handed to the fifth-grade translator for
    the in-flight round. Shared by cmd_current (panel) and plain_round_summary
    (Discord) so the two can never drift."""
    return ("The project: %s\n"
            "This round (number %s) is applying the review angle '%s' "
            "-- %s\n"
            "The exact instruction the worker was given: %s"
            % ((brief or project)[:300], ca.get("round"), ca.get("id"),
               (ca.get("lens") or ""), (ca.get("prompt") or "")[:900]))


def plain_round_summary(project: str, round_no: int | None = None) -> str:
    """Public: the fifth-grade line for THIS project's round, used by
    discord_notify.progress on round messages. Cache-first (the notify path
    must never block the round on the LLM); asks the summary model only for
    the in-flight round and caches per <project>/<angle>/<round>. Returns ""
    when notifications-summaries are off, no endpoint is configured, or
    nothing is in flight -- callers then just post the technical line."""
    e, ca = _inflight_round(project, round_no)
    if ca:
        key = "current/%s/%s/r%s" % (project, ca.get("id"), ca.get("round"))
        hit = _load_plain_cache().get(key)
        if isinstance(hit, str) and hit:
            return hit
        # cache miss: generating a NEW summary needs an endpoint; without one
        # we post the technical line only.
        try:
            if not _summary_endpoints():
                return ""
        except Exception:
            return ""
        brief = ""
        try:
            brief = json.loads((HERE / "state" / "repos" / ("%s.json" % project)).read_text(
                encoding="utf-8")).get("what_it_is") or ""
        except Exception:
            pass
        return _plain_current(key, _current_subject(brief, ca, project), project) or ""
    # not in flight: a cached checkpoint summary for the finished round, if any
    if round_no:
        hit = _load_plain_cache().get("kp/%s/r%02d" % (project, round_no))
        if isinstance(hit, str) and hit:
            return hit
    return ""


def cmd_current(a):
    """F-V (2026-10-05, the operator: fifth-grade summary of what the loop is working
    on RIGHT NOW). Emits the in-flight round as plain English: repo, angle,
    round, and what it's trying to build/prove/fix. Cached per
    <project>/<angle>/<round> so the 2.5s widget poll never pays an LLM call
    twice for the same round."""
    import time as _t
    reg = {}
    try:
        reg = json.loads((HERE / "state" / "threads.json").read_text(
            encoding="utf-8"))
    except Exception:
        pass
    hb = {}
    try:
        hb = json.loads((HERE / "state" / "runner_heartbeat.json").read_text(
            encoding="utf-8"))
    except Exception:
        pass
    # The in-flight project: fresh round-started heartbeat, else the freshest
    # current_angle.started.
    name, ca = None, {}
    if hb.get("state") == "round-started" and (_t.time() - float(hb.get("ts") or 0)) < 4200:
        name = hb.get("project")
    if not name:
        best = 0.0
        for n, e in reg.items():
            s = ((e.get("current_angle") or {}).get("started") or 0)
            if s > best:
                best, name = s, n
    e = reg.get(name) or {}
    ca = e.get("current_angle") or {}
    if not ca:
        print()
        print(json.dumps({"running": False}))
        return 0
    brief = ""
    try:
        brief = json.loads((HERE / "state" / "repos" / ("%s.json" % name)).read_text(
            encoding="utf-8")).get("what_it_is") or ""
    except Exception:
        pass
    key = "current/%s/%s/r%s" % (name, ca.get("id"), ca.get("round"))
    plain = _plain_for(key, "", name)  # cache lookup only
    if not plain:
        plain = _plain_current(key, _current_subject(brief, ca, name), name)
    print()
    print(json.dumps({
        "running": True, "project": name, "round": ca.get("round"),
        "angle": ca.get("id"), "family": ca.get("family"),
        "plain": plain,
    }, separators=(",", ":")))
    return 0


def _plain_current(key: str, subject: str, project: str) -> str:
    """Like _plain_for but with a 'what is it trying to do' system prompt."""
    cache = _load_plain_cache()
    hit = cache.get(key)
    if isinstance(hit, str) and hit:
        return hit
    sysmsg = ("You explain what a software worker is doing RIGHT NOW to a "
              "curious fifth grader. Plain words, no jargon, no code or file "
              "names. 1-2 sentences, under 45 words. Explain what it is "
              "trying to build, create, prove, or fix and why that matters "
              "to someone using the app.")
    usermsg = ("%s\n\nExplain simply what this worker is trying to do right "
               "now." % subject)
    text = ""
    for url, model in _summary_endpoints():
        try:
            body = json.dumps({
                "model": model, "max_tokens": 400, "temperature": 0.2,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": sysmsg},
                             {"role": "user", "content": usermsg}],
            }).encode("utf-8")
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
            t = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            t = re.sub(r"<think>.*?</think>", "", str(t), flags=re.S)
            t = re.sub(r"\s+", " ", t).strip()[:320]
            if t:
                text = t
                break
        except Exception:
            continue
    if not text:
        return ""
    cache[key] = text
    try:
        PLAIN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PLAIN_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except Exception:
        pass
    return text


def cmd_all(a):
    """List checkpoints across EVERY project in the manifest (for the app UI).

    Joins the rotation manifest (project -> path) to the git tags actually on
    disk, and falls back to the ledger for projects whose repo is gone.
    """
    rows = []
    manifest = HERE / "improve.yaml"
    projects = []
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        for p in (doc.get("projects") or []):
            projects.append({"name": p.get("name"), "path": p.get("path")})
    except Exception as e:
        print(json.dumps({"error": f"cannot read manifest: {e}"}, indent=2))
        return 1

    for p in projects:
        name = p.get("name")
        repo = Path(str(p.get("path") or ""))
        if not name:
            continue
        if repo.exists() and is_repo(repo):
            # ONE git call per project. The tag-per-tag form cost 1+N invocations
            # (~3.2 s across 3 repos / 56 tags) and the widget polls this; a single
            # for-each-ref over the tag glob returns creatordate+sha for all of them
            # in ~40 ms.
            rc, out = git(repo, "for-each-ref",
                          "--format=%(refname:short)|%(creatordate:iso-strict)|%(objectname:short)|%(contents:subject)",
                          "--sort=creatordate", "refs/tags/kp/")
            prefix = f"kp/{name}/"
            # Map tag -> commit subject in ONE more call: decorated log over the kp
            # range, so each checkpoint can carry WHAT WAS BUILT. The commit subject
            # is the round's own summary line ("kl: fix <angle> -- <finding> (r09)").
            # %d = decorations (tag: ...). `--format` DROPS the auto-decoration, so
            # ask for it explicitly; without it the tag->commit map comes back empty
            # and every checkpoint loses its summary.
            commits = {}
            rc2, lg = git(repo, "log", "--no-merges",
                          "--format=%H%x1f%d%x1f%s%x1e", "-n", "400")
            for rec in (lg or "").split("\x1e"):
                f = rec.split("\x1f")
                if len(f) < 3:
                    continue
                deco, subj = f[1], f[2].strip()
                for t in re.findall(r"tag:\s*([^,\s)]+)", deco):
                    commits[t.strip()] = subj
            for line in [l.strip() for l in out.splitlines() if l.strip()]:
                parts = line.split("|")
                tag = parts[0]
                # for-each-ref globs do NOT match nested refs (kp/<p>/<r>/<angle>),
                # so list the parent and filter here -- still one invocation.
                if not tag.startswith(prefix):
                    continue
                subj = parts[3] if len(parts) > 3 else ""
                # angle sits in the tag subject as "(<angle>)"
                m = re.search(r"\(([^)]+)\)", subj)
                rows.append({
                    "project": name, "tag": tag, "repo": str(repo),
                    "created": parts[1] if len(parts) > 1 else "",
                    "sha": parts[2] if len(parts) > 2 else "",
                    "angle": (m.group(1) if m else ""),
                    "summary": commits.get(tag, ""),
                })
        else:
            for c in (load_log().get(name, {}).get("checkpoints") or []):
                if c.get("kind") == "end":
                    continue
                rows.append({"project": name, "tag": c.get("tag"), "repo": str(repo),
                             "created": "", "sha": c.get("sha", ""), "ledger_only": True})

    rows.sort(key=lambda r: (r["project"], r["tag"]), reverse=True)

    # EMIT A COMPACT PAYLOAD. The widget reads this through the desktop bridge,
    # which clips large stdout mid-stream -- the pretty-printed form was 5,034
    # bytes of 21 checkpoints with full Windows repo paths on every row, and the
    # clipped JSON then failed to parse as
    #   "Unexpected non-whitespace character after JSON at position 6".
    # The widget shows only the most recent handful, so send that: recent tail,
    # no repeated `repo` on every row, no indent. Measured 5,034 -> ~1.4 KB.
    # F-Q (2026-10-04): the desktop bridge clipped this stdout MID-STREAM and
    # the widget's JSON.parse failed with "Unexpected token 'o'" starting at
    # byte ~16 -- the panel showed a parse-error box instead of checkpoints.
    # Defense in depth: (a) hard cap the payload (72-char summaries: measured
    # 4,016 B -> ~1.6 KB; the widget list shows two lines, anything past ~72
    # chars is invisible), and (b) print a LEADING newline so a clipped head
    # is still detectable (JSON.parse tolerates leading whitespace but a
    # clipped head fails loudly instead of parsing a HALF row silently).
    # F-S: LIMIT drops 12 -> 7: each row now also carries a plain-language
    # line (up to 320 chars; typical ~170). Worst case 7 x 320 + base
    # ~1.3 KB = ~3.5 KB, safely under the ~4 KB bridge clip. Most recent
    # first, as always.
    LIMIT = 7
    tail = rows[:LIMIT]
    ck = []
    for r in tail:
        subj = (r.get("summary") or "")[:72]
        plain = _plain_for(r.get("tag") or "", r.get("summary") or "",
                           r.get("project") or "the app")
        ck.append({"project": r.get("project"), "tag": r.get("tag"),
                   "created": r.get("created"), "sha": r.get("sha"),
                   "angle": r.get("angle") or "", "summary": subj,
                   "plain": plain})
    print()
    print(json.dumps({
        "count": len(rows),
        "shown": len(tail),
        "truncated": len(rows) > LIMIT,
        "checkpoints": ck,
    }, separators=(",", ":")))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Karpathy Loop checkpoint engine")
    sub = ap.add_subparsers(dest="verb", required=True)

    def common(p, need_repo=True):
        if need_repo:
            p.add_argument("--repo", required=True)
        p.add_argument("--project", required=True)

    p = sub.add_parser("prepare", help="ensure remote + START checkpoint")
    common(p)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--angle", default="")
    p.add_argument("--owner", default=None)
    p.add_argument("--push", action="store_true", default=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("finish", help="commit + END checkpoint + push")
    common(p)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--angle", default="")
    p.add_argument("--summary", default="")
    p.add_argument("--gate", default="")
    p.add_argument("--push", action="store_true", default=True)
    p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("list", help="list checkpoints")
    common(p, need_repo=False)
    p.add_argument("--repo", default="")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("rollback", help="checkout a checkpoint")
    common(p)
    p.add_argument("tag", help="full tag or just the round number")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_rollback)

    p = sub.add_parser("status", help="health for one project")
    common(p)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("all", help="checkpoints across every project in the manifest")
    p.set_defaults(fn=cmd_all)

    p = sub.add_parser("current", help="plain summary of the in-flight round")
    p.set_defaults(fn=cmd_current)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()