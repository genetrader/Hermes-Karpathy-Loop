"""
thread_feed.py -- the LIVE transcript of a Karpathy Loop thread.

The widget's CoT panel must look like the chat you have with the agent: user
prompts, assistant narration, every tool call with its arguments, and the tool
result -- oldest to newest, in one continuous feed.

Source of truth is the session DB (`messages`), which is the SAME data the
desktop chat renders. Polling this is what makes the feed live; there is no
websocket push, so the widget re-reads the tail on its tick.

Sizing: the desktop bridge returns only the LAST 4000 chars of stdout, so this
module must stay small no matter how long the thread is. It ships a bounded tail
of the newest entries with short previews -- the full-fidelity view is the opened
session (the widget's "Open live thread" button).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HERMES_HOME = Path(r"C:\Users\gene\AppData\Local\hermes")
SESSIONS_DB = HERMES_HOME / "state.db"

# Keep previews short: the panel shows a chat log, not full tool payloads.
MAX_TEXT = 120
MAX_ARG = 110
MAX_RESULT = 90


def _clip(s, n) -> str:
    s = str(s or "").replace("\r", "")
    s = s.strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _tool_args(tc: dict) -> tuple[str, str]:
    fn = (tc or {}).get("function") or {}
    name = fn.get("name") or "tool"
    raw = fn.get("arguments") or ""
    try:
        d = json.loads(raw)
    except Exception:
        return name, _clip(raw, MAX_ARG)
    for k in ("command", "path", "file_path", "query", "pattern", "url", "text"):
        if k in d:
            return name, _clip(d[k], MAX_ARG)
    if d:
        k = next(iter(d))
        return name, _clip("%s=%s" % (k, d[k]), MAX_ARG)
    return name, ""


def feed(session_id: str, limit: int = 40, session_ids: list | None = None) -> dict:
    """
    The newest `limit` transcript entries, oldest -> newest, plus totals.

    Returns {"items": [...], "total": n, "session_id": sid}.
    Each item: {kind, role, label, text, ts}
      kind   -- prompt | think | tool | result
      label  -- tool name for kind=tool/result, else ""
    """
    out: list[dict] = []
    ids = [i for i in (session_ids or []) if i] or ([session_id] if session_id else [])
    if not ids or not SESSIONS_DB.exists():
        return {"items": out, "total": 0, "session_id": session_id}
    try:
        c = sqlite3.connect("file:%s?mode=ro" % SESSIONS_DB.as_posix(), uri=True)
        c.row_factory = sqlite3.Row
    except Exception:
        return {"items": out, "total": 0, "session_id": session_id}
    try:
        ph = ",".join("?" for _ in ids)
        total = c.execute(
            "select count(*) from messages where session_id in (%s)" % ph,
            tuple(ids)).fetchone()[0]
        # Scan a WIDER window than we ship: a round is tool-heavy, so a narrow
        # window fills with tool results and crowds out the model's narration --
        # the one thing the operator actually wants to read. A wider scan lets the
        # final trim decide what to keep.
        # reasoning_content carries the model's actual thinking. deepseek-v4.1-flash
        # emits it rarely (most turns are bare tool calls), but when it does it is the
        # ONLY narration available -- reading just `content` hides it entirely, which
        # is why the panel looked like "tool calls and no reasoning".
        _cols = {r[1] for r in c.execute("pragma table_info(messages)")}
        _reason = ("reasoning_content" if "reasoning_content" in _cols
                   else ("reasoning" if "reasoning" in _cols else "''"))
        rows = c.execute(
            "select role, content, tool_calls, tool_name, timestamp, "
            "coalesce(%s,'') as reasoning_content "
            "from messages where session_id in (%s) order by rowid desc limit ?" % (_reason, ph),
            tuple(ids) + (max(limit * 12, 400),)).fetchall()
    except Exception:
        c.close()
        return {"items": out, "total": 0, "session_id": session_id}
    c.close()

    for r in reversed(rows):
        role = r["role"]
        ts = r["timestamp"]
        if role == "user":
            txt = _clip(r["content"], MAX_TEXT)
            if txt:
                out.append({"kind": "prompt", "role": "user", "label": "",
                            "text": txt, "ts": ts})
        elif role == "assistant":
            calls = None
            if r["tool_calls"]:
                try:
                    calls = json.loads(r["tool_calls"])
                except Exception:
                    calls = None
            txt = _clip(r["content"], MAX_TEXT)
            if txt:
                out.append({"kind": "think", "role": "assistant", "label": "",
                            "text": txt, "ts": ts})
            # Prefer real reasoning text when the model produced it and there was no
            # prose narration on the same turn.
            rsn = _clip((r["reasoning_content"] if "reasoning_content" in r.keys() else ""), MAX_TEXT)
            if rsn and not txt:
                out.append({"kind": "think", "role": "assistant", "label": "reasoning",
                            "text": rsn, "ts": ts})
            if calls:
                for tc in calls:
                    name, args = _tool_args(tc)
                    out.append({"kind": "tool", "role": "assistant", "label": name,
                                "text": args, "ts": ts})
        elif role == "tool":
            txt = _clip(r["content"], MAX_RESULT)
            if txt:
                out.append({"kind": "result", "role": "tool",
                            "label": r["tool_name"] or "tool",
                            "text": txt, "ts": ts})
    # Narration is the scarce, high-value signal; tool noise is abundant. When the
    # tail must be trimmed, keep every prompt/think line and drop results first.
    def rank(it):
        return {"prompt": 0, "think": 1, "tool": 2, "result": 3}.get(it["kind"], 4)
    if len(out) > limit:
        # Pass 1: from the newest end, allow tool/result rows only up to a third of
        # the budget so narration is never crowded out.
        keep: list[dict] = []
        budget_tools = max(2, limit // 3)
        tools = 0
        for it in reversed(out):
            if it["kind"] in ("tool", "result"):
                if tools >= budget_tools:
                    continue
                tools += 1
            keep.append(it)
            if len(keep) >= limit:
                break
        keep = list(reversed(keep))
        # Pass 2: top up with narration from further back so the panel always reads
        # like a conversation (prompt/think), even mid-round when tools dominate.
        have = {id(x) for x in keep}
        if sum(1 for x in keep if x["kind"] in ("prompt", "think")) < 3:
            extra = [x for x in out if x["kind"] in ("prompt", "think") and id(x) not in have]
            keep = sorted(keep + extra[-(limit // 2):], key=lambda x: (x.get("ts") or 0))
            keep = keep[-limit:]
        out = keep
    return {"items": out[-limit:], "total": total, "session_id": session_id}
