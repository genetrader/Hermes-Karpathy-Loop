#!/usr/bin/env python
"""
discord_notify.py -- the loop's notification facade + the Discord backend.

TWO roles in one file, deliberately:

  1. THE FACADE the rest of the loop calls: progress() / stuck() / ask() /
     remind() / answer(). Every one dispatches through
     notify_senders.current_sender(): with notifications disabled (the safe
     default) or backend "none", sends are silent no-ops -- no token lookup,
     no network, no spam. Callers never read notification settings themselves.

  2. THE DISCORD IMPLEMENTATION (this file), registered as the "discord"
     backend. Other platforms (slack, telegram, ...) live in their own module
     next to a @notify_senders.register("<name>") class -- same facade, zero
     call-site changes.

Three message classes, deliberately different so the operator can tell them apart by sound:
  progress -> plain post, no mention, NO ping        (quiet)
             round messages also carry the plain-English (fifth-grade)
             summary of what the round is doing/did, when
             notifications.include_summaries is on and a summary LLM is set.
  stuck    -> plain post prefixed [STUCK]; pings only when
              notifications.ping_on_stuck is on.
  question -> @mention ping, REPEATED until answered (loud)

The repeat logic edits ONE message's content, then re-pings with a fresh
short message every `repeat_min`. Discord has no "re-ping" primitive, so a
repeated ping requires a new message containing the mention.

Config lives in settings.py (notifications.*): bot token, channel, ping user,
env_file. The historical DISCORD_BOT_TOKEN / IMPROVER_* env names and the
Hermes .env still work -- settings.py folds them in.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)
LOG = ROOT / "logs" / "notify.log"
LOG.parent.mkdir(exist_ok=True)

API = "https://discord.com/api/v10"


# ---------------------------------------------------------------- env file

def _env_file() -> Path | None:
    """The KEY=VALUE file to fall back to: notifications.env_file, else the
    Hermes home .env. Nothing here is hardcoded to one machine."""
    try:
        import settings as _S
        p = _S.notify_env_file()
        if p:
            return Path(p)
        return _S.hermes_home() / ".env"
    except Exception:
        return None


def _env(key: str) -> str | None:
    """Read a key from the configured env file, handling quoted/exported forms."""
    p = _env_file()
    if p is None:
        return None
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.+?)\s*$", raw, re.M)
    if not m:
        return None
    val = m.group(1).strip().strip('"').strip("'")
    return val or None


def token() -> str:
    """The Discord bot token: settings (env / settings.local.yaml) first, the
    env file last. Raises SystemExit when nothing resolves -- the runner
    catches that and skips the notification rather than failing a round."""
    try:
        import settings as _S
        t = _S.notify_token()
    except Exception:
        t = None
    if not t:
        t = os.environ.get("DISCORD_BOT_TOKEN") or _env("DISCORD_BOT_TOKEN")
    if not t:
        raise SystemExit("DISCORD_BOT_TOKEN not found (settings, env, or env_file)")
    return t


def channel_id() -> str:
    """Improver channel: state file > settings (channel_id / env) > the env
    file's DISCORD_HOME_CHANNEL."""
    p = STATE / "channel_id.txt"
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    try:
        import settings as _S
        v = _S.notify_channel()
    except Exception:
        v = ""
    if not v:
        v = (os.environ.get("IMPROVER_CHANNEL_ID")
             or _env("IMPROVER_CHANNEL_ID") or "").strip()
    if v:
        return v
    hc = _env("DISCORD_HOME_CHANNEL")
    if not hc:
        raise SystemExit("no channel id: set notifications.channel_id "
                         "(or state/channel_id.txt / DISCORD_HOME_CHANNEL)")
    return hc.strip()


def ping_user_id() -> str | None:
    try:
        import settings as _S
        v = _S.notify_ping_user()
    except Exception:
        v = ""
    if not v:
        v = (os.environ.get("IMPROVER_PING_USER_ID")
             or _env("IMPROVER_PING_USER_ID") or "").strip()
    if v:
        return v
    p = STATE / "ping_user_id.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    return None


# ---------------------------------------------------------------- http

def _req(method: str, path: str, payload: dict | None = None):
    import urllib.error
    import urllib.request

    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bot " + token())
    r.add_header("User-Agent", "hermes-karpathy-loop/1.0")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        _log(f"HTTP {e.code} on {method} {path}: {detail}")
        raise


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


# ---------------------------------------------------------------- send (discord)

def send(channel: str, content: str, *, mention: bool = False, embed: dict | None = None):
    """Post to Discord. If mention=True, prepend the user ping (what makes sound)."""
    text = content
    uid = ping_user_id()
    if mention and uid:
        text = f"<@{uid}> {content}"
    elif mention and not uid:
        text = f"@here {content}"
    payload: dict = {"content": text[:1900]}
    if embed:
        payload["embeds"] = [embed]
    return _req("POST", f"/channels/{channel}/messages", payload)


def edit(channel: str, message_id: str, content: str):
    return _req("PATCH", f"/channels/{channel}/messages/{message_id}", {"content": content[:1900]})


# ---------------------------------------------------------------- backend

def _sender():
    import notify_senders
    return notify_senders.current_sender()


class DiscordSender:
    """The "discord" backend for notify_senders. Token and channel come from
    settings; everything else is the plain v10 REST API."""

    def send(self, content: str, *, mention: bool = False,
             embed: dict | None = None) -> dict:
        return send(channel_id(), content, mention=mention, embed=embed)

    def edit(self, message_id: str, content: str) -> dict:
        return edit(channel_id(), message_id, content)

    def fetch_recent(self, limit: int = 20) -> list[dict]:
        msgs = _req("GET", f"/channels/{channel_id()}/messages?limit={limit}")
        return msgs if isinstance(msgs, list) else []

    def ping_user_id(self) -> str | None:
        return ping_user_id()

    def describe(self) -> str:
        return "discord sender (channel %s)" % channel_id()


try:
    import notify_senders as _ns
    _ns.register("discord")(DiscordSender)
except Exception:      # pragma: no cover -- a broken facade must not kill this CLI
    pass


# ---------------------------------------------------------------- API

def _round_summary(project: str, round_no: int | None) -> str:
    """The plain-English (fifth-grade) line for a round message, or "" when
    off/unconfigured. The summary engine itself lives in checkpoint.py
    (_plain_for/_plain_current, cached per round on disk); this decides only
    WHETHER to ask: notifications.include_summaries + a configured endpoint."""
    if round_no is None:
        return ""
    try:
        import notify_senders as ns
        if not ns.include_summaries():
            return ""
        import checkpoint as C
        return C.plain_round_summary(project) or ""
    except Exception:
        return ""


def progress(project: str, text: str, *, round_no: int | None = None,
             total: int | None = None) -> dict:
    """Quiet progress line. Never pings. Messages that name a round carry the
    plain-English summary of what the round is doing/did when
    notifications.include_summaries is on and a summary model is configured."""
    tag = f"[{project}]"
    if round_no is not None and total is not None:
        tag += f" round {round_no}/{total}"
    body = f"`{tag}` {text}"
    plain = _round_summary(project, round_no)
    if plain:
        body += "\n💬 " + plain
    return _sender().send(body, mention=False)


def stuck(project: str, text: str) -> dict:
    """Blocker. No ping unless notifications.ping_on_stuck is on."""
    do_ping = False
    try:
        import settings as _S
        do_ping = bool(_S.notify_ping_on_stuck())
    except Exception:
        do_ping = (_env("IMPROVER_PING_ON_STUCK") or "0").strip() in {"1", "true", "yes"}
    return _sender().send(f"**[STUCK]** `[{project}]` {text}", mention=do_ping)


def ask(project: str, question: str, *, qid: str, repeat_min: int = 5,
        max_repeat: int = 25) -> str:
    """
    Ask ONE question and keep pinging until answered.

    Writes questions/pending.json so the answer-watcher knows what it's waiting for.
    Returns the question id.
    """
    qfile = ROOT / "questions" / "pending.json"
    qfile.parent.mkdir(exist_ok=True)
    state = {
        "qid": qid,
        "project": project,
        "question": question,
        "asked_at": time.time(),
        "repeat_min": repeat_min,
        "max_repeat": max_repeat,
        "reminders": 0,
        "message_id": None,
        "answered": False,
    }
    # ping #1
    r = _sender().send(
        f"**[QUESTION]** `[{project}]`\n{question}\n\n"
        f"_Reply in this channel to answer._ (reminder every {repeat_min}m, "
        f"up to {max_repeat}x)",
        mention=True,
    )
    state["message_id"] = r.get("id")
    qfile.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _log(f"ASK {qid} project={project} msg={state['message_id']}")
    print(f"asked {qid} -> message {state['message_id']}")
    return qid


def remind() -> str:
    """
    Send the next reminder for the open question if due. Called by the watcher
    every minute. Gives up (parks the question) after max_repeat.
    """
    qfile = ROOT / "questions" / "pending.json"
    if not qfile.exists():
        return "no pending question"
    st = json.loads(qfile.read_text(encoding="utf-8"))
    if st.get("answered"):
        return "already answered"
    now = time.time()
    due = st["asked_at"] + (st["reminders"] + 1) * st["repeat_min"] * 60
    if now < due:
        return f"not due for {int(due - now)}s"
    if st["reminders"] >= st["max_repeat"]:
        # Budget spent: record the loop's BEST-REASONED ASSUMPTION and keep
        # working. An unanswered question must never deadlock the loop
        # (the operator, 2026-09-23: "then you can make the best assumption and use
        # those assumptions"). The assumption file is where the next round
        # prompt reads it from.
        assumption = st.get("assumption") or (
            "No answer after %d pings; proceeding with the safest default: "
            "do not take the irreversible/unapproved path; pick the option that "
            "preserves the current behavior and note the alternative." % st["max_repeat"])
        st["assumed"] = True
        st["assumption"] = assumption
        st["assumed_at"] = now
        (ROOT / "questions" / "assumed" / ("%s.json" % st["qid"])).parent.mkdir(exist_ok=True)
        (ROOT / "questions" / "assumed" / ("%s.json" % st["qid"])).write_text(
            json.dumps(st, indent=2), encoding="utf-8")
        (ROOT / "state" / "answer.json").write_text(
            json.dumps({"qid": st["qid"], "answer": assumption,
                        "assumed": True, "at": now}, indent=2), encoding="utf-8")
        _sender().send(
            f"**[ASSUMED]** `[{st['project']}]` no answer after {st['max_repeat']} pings "
            f"({st['max_repeat'] * st['repeat_min']}m) — proceeding on this assumption, "
            f"say the word to override:\n> {assumption[:400]}",
            mention=True)
        st["parked"] = True
        (ROOT / "questions" / "parked.json").write_text(json.dumps(st, indent=2), encoding="utf-8")
        qfile.unlink()
        _log(f"ASSUMED {st['qid']} after {st['max_repeat']} pings")
        return "assumed"

    st["reminders"] += 1
    _sender().send(
        f"⏰ **[reminder {st['reminders']}/{st['max_repeat']}]** `[{st['project']}]`\n"
        f"{st['question']}\n\n_Reply in this channel to answer._",
        mention=True)
    st["asked_at"] = now
    qfile.write_text(json.dumps(st, indent=2), encoding="utf-8")
    _log(f"REMIND {st['qid']} #{st['reminders']}")
    return f"reminded #{st['reminders']}"


def answer(text: str, *, answered_by: str = "operator") -> str:
    """Record an answer, clear the pending question, mark it answered."""
    qfile = ROOT / "questions" / "pending.json"
    if not qfile.exists():
        return "no pending question"
    st = json.loads(qfile.read_text(encoding="utf-8"))
    st["answered"] = True
    st["answer"] = text
    st["answered_at"] = time.time()
    st["answered_by"] = answered_by
    (ROOT / "questions" / "answered" / f"{st['qid']}.json").parent.mkdir(exist_ok=True)
    (ROOT / "questions" / "answered" / f"{st['qid']}.json").write_text(
        json.dumps(st, indent=2), encoding="utf-8")
    qfile.unlink()
    _sender().send(f"✅ `[{st['project']}]` got it — resuming.\n> {text[:400]}",
                   mention=False)
    _log(f"ANSWER {st['qid']}: {text[:120]}")
    return "ok"


# ---------------------------------------------------------------- selftest

def _selftest():
    try:
        import notify_senders as ns
        s = ns.current_sender()
        print("sender:", s.describe())
        if s.name == "none":
            print("notifications are OFF (notifications.enabled=false or "
                  "backend=none). Enable them in settings.yaml / "
                  "settings.local.yaml to get round messages.")
            return None
        r = progress("selftest", "bridge online — this is a quiet progress line")
        print("progress msg id:", r.get("id"))
        return r.get("id")
    except Exception as e:
        print("selftest failed:", e)
        return None


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        _selftest()
    elif cmd == "progress":
        progress(sys.argv[2], " ".join(sys.argv[3:]))
    elif cmd == "stuck":
        stuck(sys.argv[2], " ".join(sys.argv[3:]))
    elif cmd == "ask":
        # Two accepted shapes:
        #   ask <project> <qid> <question>      (explicit id)
        #   ask <project> <question>            (id derived from the text)
        # The card prompts use the short form, so
        # make it the default rather than silently shifting the arguments.
        rest = sys.argv[3:]
        if rest and re.fullmatch(r"[A-Za-z0-9_-]{3,40}", rest[0]) and len(rest) > 1:
            qid, words = rest[0], rest[1:]
        else:
            words = rest
            qid = "q" + hashlib.sha1(" ".join(words).encode("utf-8")).hexdigest()[:10]
        ask(sys.argv[2], " ".join(words), qid=qid)
    elif cmd == "remind":
        print(remind())
    elif cmd == "answer":
        print(answer(" ".join(sys.argv[2:])))
    else:
        raise SystemExit(f"unknown cmd {cmd}")