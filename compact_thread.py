#!/usr/bin/env python
"""
compact_thread.py -- compact one Karpathy Loop thread.

Compaction is what makes a persistent thread viable: it keeps the durable
summary (what was tried, what passed) and drops the tool-call bulk. Without it,
a long-lived thread eventually blows its own context window and the loop dies
with a context error instead of a round result.

Mechanism: drive the same session headlessly with the /compact command. The
in-session command is the supported surface; if Hermes' CLI later grows a
dedicated `hermes compact` verb, switch to it here (single place to change).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

HERMES_PY = Path(r"C:\Users\gene\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe")
HERMES_CWD = Path(r"C:\Users\gene\AppData\Local\hermes\hermes-agent")


def compact(session_id: str, profile: str = "default") -> int:
    cmd = [str(HERMES_PY), "-m", "hermes_cli.main", "-p", profile,
           "-z", "/compact", "--resume", session_id]
    try:
        r = subprocess.run(cmd, cwd=str(HERMES_CWD), capture_output=True,
                           text=True, timeout=1800, errors="replace", creationflags=0x08000000)
        return r.returncode
    except Exception:
        return -1


if __name__ == "__main__":
    import sys
    raise SystemExit(compact(sys.argv[1]))
