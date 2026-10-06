#!/usr/bin/env python
"""
notify_senders.py -- the pluggable message-platform layer for the loop.

discord_notify.py used to BE the notification path: every call site was
hardwired to Discord's HTTP API. This module turns that into a small sender
interface so the platform is a setting, not a dependency:

    notifications:
      backend: discord   # or: none

Two senders ship with the loop:

    "none"      -> silent. Every send returns a stub, nothing hits the
                   network, nothing pings. This is the DEFAULT (safe for a
                   fresh clone on any machine).
    "discord"   -> the original Discord bot path (lives in discord_notify.py).

Adding slack/telegram is additive, three steps, no call-site changes:

    1. write the class (implement send/edit/fetch_recent for that platform),
    2. register it:  @notify_senders.register("slack")  (see discord_notify.py
       for the pattern),
    3. add its settings keys to settings.py SCHEMA + validation and tell the
       user `notifications.backend: slack`.

Dispatch rule (keeps F0-4: a notification failure must never abort a round):
`current_sender()` returns the NoneSender whenever notifications are disabled
or the backend is "none" -- callers never branch on config themselves, and a
misconfigured/disabled notifier is a no-op, not an exception.
"""
from __future__ import annotations

REGISTRY: dict[str, type] = {}

# Sender modules that must be imported before the registry is consulted;
# each one registers its senders by decorator side effect.
_SENDER_MODULES = ("discord_notify",)


def register(name: str):
    """Class decorator: @register("discord") binds a sender to a backend name."""
    def deco(cls):
        cls.name = name
        REGISTRY[name] = cls
        return cls
    return deco


def _ensure_loaded() -> None:
    import importlib
    for mod in _SENDER_MODULES:
        try:
            importlib.import_module(mod)
        except Exception:
            # A broken sender module must not take the whole notify layer
            # down; get() will report the missing backend loudly.
            pass


def get(name: str):
    """Instantiate the sender registered under `name`. Raises KeyError with
    the list of known backends -- callers surface that, never swallow it."""
    _ensure_loaded()
    if name not in REGISTRY:
        raise KeyError(
            "unknown notifications.backend %r -- known: %s"
            % (name, ", ".join(sorted(REGISTRY)) or "(none)"))
    return REGISTRY[name]()


class BaseSender:
    """The sender contract. Implement these and the loop will message you.

    Contract notes (learned the hard way in the Discord implementation):
      * send() returns a dict with at least an "id" key (the message id used
        for edits/re-pings) -- a no-op sender returns {"id": "<noop>"}.
      * every method must be safe to call headless and offline: the loop runs
        rounds unattended and a notification path that blocks is a bug.
      * content arrives fully formatted; the sender may clip it to platform
        limits but must never raise on long input.
    """
    name = "base"

    def send(self, content: str, *, mention: bool = False,
             embed: dict | None = None) -> dict:
        raise NotImplementedError

    def edit(self, message_id: str, content: str) -> dict:
        raise NotImplementedError

    def fetch_recent(self, limit: int = 20) -> list[dict]:
        """Recent channel messages, for the answer watcher. Optional."""
        return []

    def ping_user_id(self) -> str | None:
        """The user id a question should @mention (None = no mentions)."""
        return None

    def describe(self) -> str:
        return "%s sender" % self.name


class _NoopSender(BaseSender):
    """Silent sender: notifications.enabled=false or backend=none. Returns
    plausible stubs so callers' bookkeeping (message ids etc.) still works."""

    name = "none"

    def send(self, content: str, *, mention: bool = False,
             embed: dict | None = None) -> dict:
        return {"id": "noop-disabled", "content": content[:120]}

    def edit(self, message_id: str, content: str) -> dict:
        return {"id": message_id, "content": content[:120]}

    def fetch_recent(self, limit: int = 20) -> list[dict]:
        return []

    def ping_user_id(self) -> str | None:
        return None


_NOOP = _NoopSender()


def current_sender():
    """The sender the loop should use RIGHT NOW, from settings.

    Never raises for a disabled/misconfigured notifier: disabled and unknown
    backends both degrade to the silent sender (the unknown case is logged to
    logs/notify.log so the misconfiguration stays visible)."""
    import settings as _S
    if not _S.notify_enabled():
        return _NOOP
    backend = _S.notify_backend()
    if backend in ("", "none", "off", "false", "0"):
        return _NOOP
    try:
        return get(backend)
    except KeyError as e:
        try:
            import discord_notify as _dn
            _dn._log("notify backend error: %s" % e)
        except Exception:
            pass
        return _NOOP


def noop() -> BaseSender:
    """Explicit silent sender (tests, callers that want determinism)."""
    return _NOOP


def include_summaries() -> bool:
    """Whether round messages carry the fifth-grade plain-English summary."""
    import settings as _S
    return _S.notify_include_summaries()
