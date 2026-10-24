#!/usr/bin/env python
"""
project-improver / setup_channel.py

Run this AFTER Gene creates the #project-improver channel in Discord.

It discovers the channel, pins it for the bridge, adds it to
DISCORD_ALLOWED_CHANNELS (so the gateway lets replies through), and verifies
the whole path end to end.

Usage:
  python setup_channel.py              # auto-find a channel named project-improver
  python setup_channel.py <channel_id> # use an explicit id
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = Path(r"C:\Users\gene\AppData\Local\hermes\.env")
GID = "1478613632626462753"  # COMMAND CENTRAL
TARGET_NAME = "project-reviewer"


def _load_env_text() -> str:
    return ENV.read_text(encoding="utf-8", errors="replace")


def find_channel(cid: str | None) -> dict:
    import discord_notify as dn
    if cid:
        return dn._req("GET", f"/channels/{cid}")
    chans = dn._req("GET", f"/guilds/{GID}/channels")
    for c in chans:
        if c.get("name") == TARGET_NAME:
            return c
    names = ", ".join(sorted(c.get("name", "?") for c in chans if c.get("type") == 0))
    raise SystemExit(
        f"No channel named '{TARGET_NAME}' found.\n"
        f"Create it in Discord, then re-run.\n"
        f"Current text channels: {names}"
    )


def pin(cid: str) -> None:
    (ROOT / "state").mkdir(exist_ok=True)
    (ROOT / "state" / "channel_id.txt").write_text(cid, encoding="utf-8")


def allowlist(cid: str) -> str:
    """
    Add the channel id to DISCORD_ALLOWED_CHANNELS in .env.
    We do NOT touch config.yaml (settings live there; this is env plumbing)
    and we never write secrets to stdout.
    """
    txt = _load_env_text()
    m = re.search(r"^DISCORD_ALLOWED_CHANNELS=(.*)$", txt, re.M)
    if m:
        cur = [x.strip() for x in m.group(1).split(",") if x.strip()]
        if cid in cur:
            return "already allowlisted"
        cur.append(cid)
        new = "DISCORD_ALLOWED_CHANNELS=" + ",".join(cur)
        txt = txt[:m.start()] + new + txt[m.end():]
    else:
        txt = txt.rstrip("\n") + f"\nDISCORD_ALLOWED_CHANNELS={cid}\n"
        cur = [cid]
    # also mirror into the free-response + no-thread lists so the agent can reply
    for key in ("DISCORD_FREE_RESPONSE_CHANNELS", "DISCORD_NO_THREAD_CHANNELS"):
        m2 = re.search(rf"^{key}=(.*)$", txt, re.M)
        if m2:
            vals = [x.strip() for x in m2.group(1).split(",") if x.strip()]
            if cid not in vals:
                vals.append(cid)
                txt = txt[:m2.start()] + f"{key}=" + ",".join(vals) + txt[m2.end():]
        else:
            txt = txt.rstrip("\n") + f"\n{key}={cid}\n"
    ENV.write_text(txt, encoding="utf-8")
    return f"allowlisted ({len(cur)} channel(s))"


def verify(cid: str) -> bool:
    import discord_notify as dn
    ch = dn._req("GET", f"/channels/{cid}")
    # can the bot read + write here?
    try:
        dn.send(cid, "`[project-improver]` bridge wired to this channel. "
                     "Quiet progress lands here; questions will @mention you and "
                     "repeat every 5 minutes until answered.")
        return True
    except Exception as e:
        print("  SEND FAILED:", e)
        return False


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    ch = find_channel(cid)
    cid = ch["id"]
    print(f"channel : #{ch.get('name')}  id={cid}  type={ch.get('type')}")
    pin(cid)
    print(f"pinned  : state/channel_id.txt -> {cid}")
    print(f"env     : {allowlist(cid)}")
    ok = verify(cid)
    print(f"send    : {'OK' if ok else 'FAILED'}")
    if ok:
        print()
        print("Next: hermes gateway restart   (so DISCORD_ALLOWED_CHANNELS reloads)")
        print("Then: python improver.py plan")


if __name__ == "__main__":
    main()