"""Tests for settings.py -- the machine-config layer, and the pluggable
notifier it feeds (notify_senders / discord_notify).

Every test steers the loader with KL_SETTINGS_FILE / KL_SETTINGS_LOCAL (paths
under tmp_path) + env vars, and calls reset_cache() so nothing leaks between
cases or touches the repo's real settings files.

Run: python -m pytest tests/test_settings.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings as S   # noqa: E402


@pytest.fixture()
def clean_env(tmp_path, monkeypatch):
    """Isolate the settings layer: nonexistent settings files + a clean env."""
    for k in list(__import__("os").environ):
        if k.startswith(("KL_", "GITHUB_", "GH_", "DISCORD_", "IMPROVER_",
                         "HERMES_HOME")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KL_SETTINGS_FILE", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KL_SETTINGS_LOCAL", str(tmp_path / "settings.local.yaml"))
    S.reset_cache()
    yield tmp_path
    S.reset_cache()


def _write(path: pathlib.Path, text: str):
    path.write_text(text, encoding="utf-8")
    S.reset_cache()


# ---------------------------------------------------------------- defaults

def test_shipped_defaults_are_safe(clean_env):
    assert S.github_enabled() is False
    assert S.notify_enabled() is False
    assert S.notify_backend() == "none"
    assert S.notify_include_summaries() is True
    assert S.summary_endpoints() == []
    assert S.seat_defaults() == ("", "")
    assert S.round_timeout() == 4200


def test_unknown_setting_is_a_warn_not_an_error(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", "github:\n  enabledn: true\n")
    warns = [i for i in S.issues() if i.level == "warn"]
    assert any("github.enabledn" in i.key for i in warns)
    assert not [i for i in S.issues() if i.level == "error"]
    # and it did not change the value
    assert S.github_enabled() is False


# ---------------------------------------------------------------- layering

def test_env_beats_file_beats_default(clean_env, tmp_path, monkeypatch):
    _write(tmp_path / "settings.yaml", "github:\n  enabled: true\n")
    assert S.github_enabled() is True
    monkeypatch.setenv("KL_GITHUB_ENABLED", "off")
    S.reset_cache()
    assert S.github_enabled() is False
    assert S.source_of("github.enabled") == "env"


def test_local_file_beats_tracked_file(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", "runtime:\n  round_timeout: 100\n")
    _write(tmp_path / "settings.local.yaml", "runtime:\n  round_timeout: 999\n")
    assert S.round_timeout() == 999


def test_empty_override_does_not_clobber_lower_layer(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", 'notifications:\n  channel_id: "12345"\n')
    _write(tmp_path / "settings.local.yaml", 'notifications:\n  channel_id: ""\n')
    assert S.notify_channel() == "12345"


# ---------------------------------------------------------------- secrets

def test_secret_in_tracked_file_is_rejected_and_dropped(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", "github:\n  token: ghp_REALTOKENxx\n")
    errs = [i for i in S.issues() if i.level == "error"]
    assert any("github.token" in i.key for i in errs), \
        "a committed token must be a hard validation error"
    assert not S.github_token()


def test_secret_from_local_file_is_accepted(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("KL_GITHUB_ENABLED", "1")
    _write(tmp_path / "settings.local.yaml", "github:\n  token: ghp_LOCALONLY\n")
    assert S.github_token() == "ghp_LOCALONLY"


def test_token_env_names_an_arbitrary_variable(clean_env, monkeypatch):
    monkeypatch.setenv("KL_GITHUB_ENABLED", "1")
    _write(tmp_path_of := (clean_env / "settings.yaml"),
           "github:\n  token_env: MY_WEIRD_TOKEN\n")
    monkeypatch.setenv("MY_WEIRD_TOKEN", "ghp_FROM_ENV")
    assert S.github_token() == "ghp_FROM_ENV"


def test_redacted_view_never_shows_secret_values(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("KL_GITHUB_ENABLED", "1")
    _write(tmp_path / "settings.local.yaml", "github:\n  token: ghp_SECRET\n")
    v = S.redacted_view()
    assert "ghp_SECRET" not in repr(v)
    assert "<set via" in v["github"]["token"]


# ---------------------------------------------------------------- models

def test_seats_must_be_provider_colon_model(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml",
           'models:\n  implementer: "baremodel"\n')
    errs = [i for i in S.issues() if i.level == "error"]
    assert any("models.implementer" in i.key for i in errs)


def test_identical_seats_are_refused(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml",
           'models:\n  implementer: "p:m"\n  reviewer: "p:m"\n')
    errs = [i for i in S.issues() if i.level == "error"]
    assert any("cannot review its own work" in i.message for i in errs)


def test_summary_endpoints_from_settings_and_fallback(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml",
           'models:\n  summary_url: "http://a/v1/chat/completions"\n'
           '  summary_model: "m1"\n'
           '  summary_url_2: "http://b/v1/chat/completions"\n'
           '  summary_model_2: "m2"\n')
    assert S.summary_endpoints() == [("http://a/v1/chat/completions", "m1"),
                                     ("http://b/v1/chat/completions", "m2")]
    _write(tmp_path / "settings.yaml",
           'models:\n  summary_url: "http://only/v1"\n')
    assert S.summary_endpoints() == [("http://only/v1", "gpt-4o-mini")]


def test_builder_fallback_prefers_explicit_then_implementer(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", 'models:\n  implementer: "p:i"\n')
    assert S.builder_fallback() == "p:i"
    _write(tmp_path / "settings.yaml",
           'models:\n  implementer: "p:i"\n  builder_fallback: "p:f"\n')
    assert S.builder_fallback() == "p:f"


# ---------------------------------------------------------------- validation

def test_bad_bool_is_human_readable(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", "github:\n  enabled: maybe\n")
    errs = [i for i in S.issues() if i.level == "error"]
    assert any("github.enabled" in i.key and "true/false" in i.message
               for i in errs)


def test_negative_timeout_is_error(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", "runtime:\n  round_timeout: -5\n")
    errs = [i for i in S.issues() if i.level == "error"]
    assert any("runtime.round_timeout" in i.key for i in errs)


def test_enabled_discord_without_token_or_channel_errors(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml",
           "notifications:\n  enabled: true\n  backend: discord\n")
    errs = [i for i in S.issues() if i.level == "error"]
    keys = [i.key for i in errs]
    assert "notifications.bot_token" in keys
    assert "notifications.channel_id" in keys


def test_backend_none_needs_no_token(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml",
           "notifications:\n  enabled: true\n  backend: none\n")
    assert not [i for i in S.issues() if i.level == "error"]


def test_broken_yaml_is_loud_not_silent(clean_env, tmp_path):
    _write(tmp_path / "settings.yaml", "github: [unclosed\n")
    errs = [i for i in S.issues() if i.level == "error"]
    assert any("cannot be parsed" in i.message for i in errs)


# ---------------------------------------------------------------- CLI write

def test_write_setting_roundtrip_and_secret_refusal(clean_env, tmp_path):
    S.write_setting("runtime.gate_timeout", "123")
    assert (tmp_path / "settings.local.yaml").exists()
    assert S.gate_timeout_default() == 123
    with pytest.raises(SystemExit):
        S.write_setting("github.token", "ghp_x", tracked=True)
    S.unset_setting("runtime.gate_timeout")
    assert S.gate_timeout_default() == 900


def test_write_setting_unknown_key_refused(clean_env):
    with pytest.raises(SystemExit):
        S.write_setting("github.nope", "1")


# ---------------------------------------------------------------- senders

def test_sender_none_is_the_disabled_default(clean_env):
    import notify_senders
    s = notify_senders.current_sender()
    assert s.name == "none"
    assert s.send("hi")["id"] == "noop-disabled"


def test_sender_disabled_ignores_backend_discord(clean_env, monkeypatch):
    monkeypatch.setenv("KL_NOTIFY_BACKEND", "discord")   # enabled stays off
    import notify_senders
    assert notify_senders.current_sender().name == "none"


def test_registry_rejects_unknown_backend(clean_env, monkeypatch):
    monkeypatch.setenv("KL_NOTIFY_ENABLED", "1")
    import notify_senders
    with pytest.raises(KeyError) as e:
        notify_senders.get("smoke")
    assert "discord" in str(e.value)   # the error names what IS known


def test_discord_registered_and_current(clean_env, monkeypatch):
    monkeypatch.setenv("KL_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("KL_NOTIFY_BACKEND", "discord")
    import notify_senders
    assert notify_senders.current_sender().name == "discord"


def test_include_summaries_toggle_reads_settings(clean_env, tmp_path, monkeypatch):
    _write(tmp_path / "settings.yaml",
           "notifications:\n  include_summaries: false\n")
    import notify_senders
    assert notify_senders.include_summaries() is False
    monkeypatch.setenv("KL_NOTIFY_SUMMARIES", "1")
    S.reset_cache()
    assert notify_senders.include_summaries() is True


def test_progress_message_carries_plain_summary(clean_env, monkeypatch, tmp_path):
    """The human requirement: round messages include the fifth-grade summary,
    produced by the checkpoint.py plain engine, honouring the toggle."""
    import json
    import discord_notify as dn
    monkeypatch.setenv("KL_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("KL_NOTIFY_BACKEND", "discord")
    S.reset_cache()

    sent = []

    class FakeSender:
        def send(self, content, *, mention=False, embed=None):
            sent.append(content)
            return {"id": "1"}

    import notify_senders
    monkeypatch.setattr(notify_senders, "current_sender", lambda: FakeSender())
    # HERMETIC: redirect checkpoint's HERE so the fixture never touches the
    # live state/ dir (a live-overwrite bug here once wiped the real registry).
    import checkpoint as _C
    monkeypatch.setattr(_C, "HERE", tmp_path, raising=False)
    monkeypatch.setattr(_C, "PLAIN_CACHE", tmp_path / "state" / "plain_summaries.json", raising=False)
    (tmp_path_state := tmp_path / "state").mkdir(exist_ok=True)
    threads = tmp_path_state / "threads.json"
    cache = tmp_path_state / "plain_summaries.json"
    t_bak = threads.read_text(encoding="utf-8") if threads.exists() else None
    c_bak = cache.read_text(encoding="utf-8") if cache.exists() else None
    try:
        threads.write_text(json.dumps(
            {"projx": {"current_angle": {"id": "a1", "round": 2,
                                         "lens": "l", "prompt": "p"}}}),
            encoding="utf-8")
        cache.write_text(json.dumps(
            {"current/projx/a1/r2": "Fixed the thing that made saving fail."}),
            encoding="utf-8")
        dn.progress("projx", "round 2: angle a1 -- starting", round_no=2)
        assert any("Fixed the thing that made saving fail." in m for m in sent), \
            "round message lost the plain-English summary"

        # toggle off -> technical line only
        sent.clear()
        monkeypatch.setenv("KL_NOTIFY_SUMMARIES", "0")
        S.reset_cache()
        dn.progress("projx", "round 2 done", round_no=2)
        assert all("Fixed the thing" not in m for m in sent), \
            "include_summaries=false was ignored"
    finally:
        for p, b in ((threads, t_bak), (cache, c_bak)):
            if b is not None:
                p.write_text(b, encoding="utf-8")
            elif p.exists():
                p.unlink()


def test_notify_noop_when_disabled_no_network(clean_env, monkeypatch):
    """F0-4 spirit at the sender layer: disabled = no HTTP, no token lookup."""
    import discord_notify as dn
    monkeypatch.setattr(dn, "token",
                        lambda: (_ for _ in ()).throw(AssertionError(
                            "token looked up while notifications disabled")))
    assert dn.progress("p", "x").get("id") == "noop-disabled"
    assert dn.stuck("p", "y").get("id") == "noop-disabled"
