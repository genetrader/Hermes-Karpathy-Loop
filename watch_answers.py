#!/usr/bin/env python
"""
project-improver / watch_answers.py

Two jobs, run every minute by the watcher task:
  1. send the next reminder ping for an unanswered question (nag loop)
  2. listen for Gene's reply in the improver channel and hand it to the worker

Reply detection: reads messages posted in the improver channel AFTER the
question was asked, from a non-bot author. The first such message is the answer.
His replies are NOT routed through the agent (the channel is chat-free), so a
plain message is unambiguous.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import discord_notify as dn

ROOT = Path(__file__).resolve().parent
QDIR = ROOT / "questions"
CURSOR = ROOT / "state" / "last_seen_message_id.txt"


def _seen() -> str | None:
    return CURSOR.read_text(encoding="utf-8").strip() if CURSOR.exists() else None


def _mark(mid: str) -> None:
    CURSOR.parent.mkdir(exist_ok=True)
    CURSOR.write_text(mid, encoding="utf-8")


def fetch_recent(limit: int = 20) -> list[dict]:
    ch = dn.channel_id()
    msgs = dn._req("GET", f"/channels/{ch}/messages?limit={limit}")
    return msgs if isinstance(msgs, list) else []


def check_reply() -> str:
    """
    If a question is open, look for a human reply newer than the question.
    Returns a status string.
    """
    qfile = QDIR / "pending.json"
    if not qfile.exists():
        return "idle"
    st = json.loads(qfile.read_text(encoding="utf-8"))
    if st.get("answered"):
        return "answered"

    asked_at = st["asked_at"]
    msgs = fetch_recent(30)
    # Discord returns newest-first; take human messages, oldest-first
    human = []
    for m in msgs:
        a = m.get("author", {})
        if a.get("bot"):
            continue
        if a.get("id") == dn.ping_user_id():
            human.append(m)
    human.sort(key=lambda m: m["id"])

    # a reply must be newer than the question message
    qm = str(st.get("message_id") or "")
    for m in human:
        if qm and int(m["id"]) <= int(qm):
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        who = (m.get("author") or {}).get("username", "gene")
        dn.answer(text, answered_by=who)
        # write the answer where the worker reads it
        (ROOT / "state" / "answer.json").write_text(
            json.dumps({"qid": st["qid"], "answer": text, "at": time.time()}, indent=2),
            encoding="utf-8")
        return f"answered: {text[:80]}"
    return "waiting"


def tick() -> str:
    """One watcher pass: first look for an answer, then nag if still open."""
    r = check_reply()
    if r.startswith("answered") or r == "idle":
        if r == "idle":
            pass
        return r
    nag = dn.remind()
    return f"{r} | {nag}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        print("watcher loop — Ctrl-C to stop")
        while True:
            try:
                print(time.strftime("%H:%M:%S"), tick(), flush=True)
            except Exception as e:
                print("ERR", e, flush=True)
            time.sleep(60)
    else:
        print(tick())