#!/usr/bin/env python3
"""
settings.py -- the ONE place the Karpathy Loop decides anything machine-specific.

Before this module existed, the loop carried the author's machine in its code:
a Discord .env at one hard-coded path, the author's hermes home, gh CLI remotes,
fleet model names. Anything that is not a *decision* about the loop itself now
lives here, and every module reads it through this file.

Three layers, later wins (deep-merged per leaf):

    built-in defaults        safe: GitHub OFF, notifications OFF
    settings.yaml            tracked; team-shared, NO secrets allowed
    settings.local.yaml      gitignored; your machine, secrets allowed here
    environment variables    final override, highest priority

Secrets rule (hard, not style): a token VALUE may only come from the
environment or from settings.local.yaml. A token value found in the tracked
settings.yaml is rejected by validation and dropped from the merge. The
tracked file may only name an ENV VAR (github.token_env, e.g. "GITHUB_TOKEN").

Env overrides (all optional; "1/true/yes/on" enable, "0/false/no/off" disable):
    KL_SETTINGS_FILE      alternate path for the tracked settings file
    KL_SETTINGS_LOCAL     alternate path for settings.local.yaml (tests)
    KL_GITHUB_ENABLED     master GitHub switch
    KL_NOTIFY_ENABLED     master notifications switch
    KL_NOTIFY_BACKEND     discord | none (pluggable; none = silent no-op)
    KL_NOTIFY_SUMMARIES   include the plain-English round summary (default on)
    KL_LLM_URL / KL_LLM_MODEL (+ _2 fallbacks)  plain-summary LLM endpoints
    KL_BUILDER_MODEL_SEAT / KL_REVIEWER_MODEL_SEAT   model seats
    GITHUB_TOKEN          token value the github.token_env default points at
    GH_TOKEN              honoured by gh itself; we forward our token to it
    HERMES_HOME           where Hermes lives
    KL_HERMES_PYTHON      interpreter that launches hermes CLI children
    KL_BUILDER_MODEL      implementer-seat fallback
    KL_BRIEF_MODEL / KL_BRIEF_PROFILE      one-shot repo-brief model
    DISCORD_BOT_TOKEN / IMPROVER_CHANNEL_ID / IMPROVER_PING_USER_ID
                          (the historical Discord env names still work)

Validation is explicit and human-readable: loopctl.py settings validate prints
every problem with the exact dotted key and the exact fix. Safe defaults mean
a fresh clone runs (locally, silently) with zero configuration.
"""
from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SETTINGS_FILE = ROOT / "settings.yaml"
SETTINGS_LOCAL = ROOT / "settings.local.yaml"


def _main_path() -> Path:
    """Honours KL_SETTINGS_FILE everywhere the tracked file is touched."""
    return Path(os.environ.get("KL_SETTINGS_FILE") or SETTINGS_FILE)


def _local_path() -> Path:
    """Honours KL_SETTINGS_LOCAL everywhere the local file is touched."""
    return Path(os.environ.get("KL_SETTINGS_LOCAL") or SETTINGS_LOCAL)

# Keys whose VALUES are secrets. They are rejected in the tracked file and
# accepted only from settings.local.yaml or the environment.
SECRET_LEAVES = {"github.token", "notifications.bot_token"}

# Filesystem-like settings: the settings card offers a Browse picker for
# these (files vs directories noted per key).
PATH_KEYS = {
    "github.gh_cli": "file",
    "notifications.env_file": "file",
    "hermes.home": "dir",
    "hermes.python": "file",
    "hermes.config_file": "file",
    "projects.manifest": "file",
}

# Plain-language help for every setting, shown on the "?" hover in the card.
HELP = {
    "github.enabled": "Push accepted-round checkpoints to GitHub. OFF = local-only mode: tags and rollback still work, nothing ever leaves this machine.",
    "github.owner": "Your GitHub username. Checkpoint repos are expected under this owner (or already set per-project by git remotes).",
    "github.token": "A GitHub personal access token with repo scope. Pasted tokens are stored in settings.local.yaml (gitignored) — never displayed again, never committed.",
    "github.token_env": "Name of the environment variable that holds the token, if you prefer env over a local file.",
    "github.gh_cli": "Path to the gh CLI binary used for non-interactive pushes.",
    "github.require_repo": "Refuse to run for a project whose git repo has no GitHub remote (instead of skipping pushes silently).",
    "github.push_branches": "Also push the round branch; OFF pushes only the checkpoint tags.",
    "models.implementer": "The model that writes code each round. Format provider:model. Must differ from the reviewer — one brain cannot review itself.",
    "models.reviewer": "The model that reviews/falsifies the implementer's work each round. Format provider:model.",
    "models.builder_fallback": "Seat used when the implementer is unreachable.",
    "models.brief_model": "One-shot model used to write the repo briefs shown in the widget.",
    "models.brief_profile": "Hermes profile to run brief generation in.",
    "models.summary_url": "OpenAI-compatible /v1/chat/completions endpoint of the model that writes the plain-English round summaries.",
    "models.summary_model": "Model name at the summary endpoint.",
    "models.summary_url_2": "Fallback endpoint if the first is down.",
    "models.summary_model_2": "Model name at the fallback endpoint.",
    "notifications.enabled": "Master switch. OFF = the loop runs silently with zero network calls to any chat platform.",
    "notifications.backend": "discord posts round progress, STUCK alarms and questions. none = silent.",
    "notifications.channel_id": "Discord channel ID the loop posts to.",
    "notifications.ping_user_id": "User ID pinged on STUCK alarms.",
    "notifications.ping_on_stuck": "Ping you when a repo gets stuck/quarantined.",
    "notifications.include_summaries": "Attach the plain-English 'what it is doing' summary to round messages.",
    "notifications.bot_token": "Discord bot token. Stored in settings.local.yaml only.",
    "notifications.env_file": "Optional .env file with DISCORD_BOT_TOKEN etc.",
    "hermes.home": "Where Hermes lives (the folder containing hermes-agent).",
    "hermes.python": "Interpreter that launches the round-worker children.",
    "hermes.profile": "Hermes profile the round workers run under.",
    "hermes.config_file": "Path to Hermes' own config.yaml.",
    "projects.manifest": "improve.yaml path: which repos the loop works on, with their gate commands.",
    "projects.index_rows": "Rows kept in the discovery index.",
    "projects.sweep_roots": "Roots scanned by project discovery.",
    "runtime.round_timeout": "Seconds before a round child is killed (rc=124).",
    "runtime.worktree_abandon_secs": "Seconds before an unclaimed worktree is considered abandoned.",
}

# ---------------------------------------------------------------- schema
# name -> (type, env-var-override, default). type is bool/int/str.
# This doubles as the validation contract and the CLI's coercion table.
SCHEMA: dict[str, tuple[str, str, object]] = {
    "github.enabled":            ("bool", "KL_GITHUB_ENABLED", False),
    "github.owner":              ("str",  "KL_GITHUB_OWNER", ""),
    "github.token_env":          ("str",  "", "GITHUB_TOKEN"),
    "github.token":              ("str",  "GITHUB_TOKEN", ""),
    "github.require_repo":       ("bool", "", False),
    "github.gh_cli":             ("str",  "KL_GH_CLI", "gh"),
    "github.push_branches":      ("bool", "", True),

    "notifications.enabled":       ("bool", "KL_NOTIFY_ENABLED", False),
    "notifications.backend":       ("str",  "KL_NOTIFY_BACKEND", "none"),
    "notifications.bot_token_env": ("str",  "", "DISCORD_BOT_TOKEN"),
    "notifications.bot_token":     ("str",  "DISCORD_BOT_TOKEN", ""),
    "notifications.channel_id":    ("str",  "IMPROVER_CHANNEL_ID", ""),
    "notifications.ping_user_id":  ("str",  "IMPROVER_PING_USER_ID", ""),
    "notifications.ping_on_stuck": ("bool", "IMPROVER_PING_ON_STUCK", False),
    "notifications.include_summaries": ("bool", "KL_NOTIFY_SUMMARIES", True),
    "notifications.env_file":      ("str",  "KL_NOTIFY_ENV_FILE", ""),

    "hermes.home":               ("str",  "HERMES_HOME", ""),
    "hermes.python":             ("str",  "KL_HERMES_PYTHON", ""),
    "hermes.profile":            ("str",  "KL_HERMES_PROFILE", ""),
    "hermes.config_file":        ("str",  "KL_HERMES_CONFIG", ""),

    "models.implementer":        ("str",  "KL_BUILDER_MODEL_SEAT", ""),
    "models.reviewer":           ("str",  "KL_REVIEWER_MODEL_SEAT", ""),
    "models.builder_fallback":   ("str",  "KL_BUILDER_MODEL", ""),
    "models.brief_model":        ("str",  "KL_BRIEF_MODEL", ""),
    "models.brief_profile":      ("str",  "KL_BRIEF_PROFILE", ""),
    # The plain-English (fifth-grade) round-summary LLM: any OpenAI-compatible
    # /v1/chat/completions endpoint. url + model, with an optional fallback pair.
    # No hardcoded servers: nothing ships with anyone's topology in it.
    "models.summary_url":        ("str",  "KL_LLM_URL", ""),
    "models.summary_url_2":      ("str",  "KL_LLM_URL_2", ""),
    "models.summary_model":      ("str",  "KL_LLM_MODEL", ""),
    "models.summary_model_2":    ("str",  "KL_LLM_MODEL_2", ""),

    "projects.manifest":         ("str",  "KL_MANIFEST", ""),
    "projects.index_rows":       ("str",  "KL_INDEX_ROWS", ""),
    # Extra roots discovery sweeps for new codebases (comma-separated list).
    # Empty = the portable home-folder defaults in discover.py.
    "projects.sweep_roots":      ("str",  "KL_SWEEP_ROOTS", ""),

    "runtime.round_timeout":     ("int",  "KL_ROUND_TIMEOUT", 4200),
    "runtime.worktree_abandon_secs": ("int", "KL_WT_ABANDON_SECS", 21600),
    "runtime.gate_timeout":      ("int",  "KL_GATE_TIMEOUT", 900),
    # Rotation seeds for a FRESH deployment. loop.json (set from the widget's
    # rotation card / `loopctl config`) still wins at runtime; these are what a
    # new checkout seeds loop.json from -- no hardcoded numbers in loopctl.
    "runtime.angles_per_visit":  ("int",  "KL_ANGLES_PER_VISIT", 2),
    "runtime.sweep_minutes":     ("int",  "KL_SWEEP_MINUTES", 360),
    "runtime.max_rounds":        ("int",  "KL_MAX_ROUNDS", 0),   # 0 = forever
    "runtime.max_hours":         ("int",  "KL_MAX_HOURS", 0),    # 0 = forever

    # The loop's git identity for its own commits/tags (checkpoints, initial
    # commits). Generic defaults; put your own name in if you prefer attribution.
    "git.author_name":           ("str",  "KL_GIT_AUTHOR_NAME", "Karpathy Loop"),
    "git.author_email":          ("str",  "KL_GIT_AUTHOR_EMAIL", "loop@local"),

    # The status server the widget talks to (scripts/status_server.py). The
    # server binds this port; the widget's shell-fallback and settings card
    # discover it from the server's own /meta.json.
    "widget.status_port":        ("int",  "KL_STATUS_PORT", 8765),
}

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}
_SEAT_RE = re.compile(r"^[A-Za-z0-9._-]+(:[A-Za-z0-9._-]+){1,2}$")

_ISSUES: list = []           # issues from the most recent load (for `validate`)
_SOURCES: dict = {}          # dotted key -> "env"/"local"/"file"/"default"
_CACHE: dict | None = None
_CACHE_STAMP: tuple | None = None


class Issue:
    def __init__(self, level: str, key: str, message: str, fix: str = ""):
        self.level = level          # "error" | "warn"
        self.key = key
        self.message = message
        self.fix = fix

    def __repr__(self):
        fix = ("  fix: " + self.fix) if self.fix else ""
        return "%s [%s] %s%s" % (self.level.upper(), self.key, self.message, fix)


def _coerce(kind: str, raw, key: str):
    """String/env value -> schema type. Returns (value, issue|None)."""
    if isinstance(raw, bool) and kind == "bool":
        return raw, None
    if kind == "bool":
        s = str(raw).strip().lower()
        if s in _TRUTHY:
            return True, None
        if s in _FALSY:
            return False, None
        return None, Issue("error", key, "expected true/false, got %r" % (raw,),
                           "set %s to true or false" % key)
    if kind == "int":
        try:
            return int(str(raw).strip()), None
        except (TypeError, ValueError):
            return None, Issue("error", key, "expected a whole number, got %r" % (raw,),
                               "set %s to an integer (seconds)" % key)
    return str(raw), None


def _layer_from_yaml(path: Path, tracked: bool) -> tuple[dict, list]:
    """Read one settings file. tracked=True rejects secret VALUES outright."""
    issues: list = []
    if not path.exists():
        return {}, issues
    try:
        import yaml  # lazy: the loop runs fine without PyYAML when no files exist
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {}, [Issue("error", path.name, "cannot be parsed: %s" % e,
                          "fix the YAML syntax (a validator or `loopctl settings validate` shows the line)")]
    if not isinstance(doc, dict):
        return {}, [Issue("error", path.name, "top level must be a mapping of sections",
                          "write `section:` / `  key: value` pairs")]
    flat: dict = {}
    for section, kv in doc.items():
        if not isinstance(kv, dict):
            issues.append(Issue("error", str(section),
                                "section must contain key: value pairs",
                                "move `%s` under a section" % section))
            continue
        for k, v in kv.items():
            key = "%s.%s" % (section, k)
            if key in SECRET_LEAVES and tracked and v not in (None, ""):
                issues.append(Issue(
                    "error", key,
                    "%s holds a secret and cannot be set in the TRACKED %s "
                    "(it would be committed to git)" % (key, path.name),
                    "put the value in %s, or only name an env var: "
                    "%s: <ENV_VAR_NAME>" % (SETTINGS_LOCAL.name, key.replace(".token", ".token_env")
                                            if key == "github.token" else
                                            key.replace(".bot_token", ".bot_token_env"))))
                continue
            if key not in SCHEMA:
                issues.append(Issue("warn", key,
                                    "unknown setting in %s (ignored)" % path.name,
                                    "check the spelling; `loopctl settings show` lists every key"))
                continue
            flat[key] = v
    return flat, issues


def _layer_from_env() -> dict:
    flat: dict = {}
    for key, (_kind, env, _dflt) in SCHEMA.items():
        if not env:
            continue
        if key in SECRET_LEAVES and key not in ("github.token",):
            pass  # bot_token's env name itself lives in notifications.bot_token_env
        if env in os.environ:
            flat[key] = os.environ[env]
    # the historical env names win when they are present at all
    if os.environ.get("IMPROVER_PING_ON_STUCK"):
        flat["notifications.ping_on_stuck"] = os.environ["IMPROVER_PING_ON_STUCK"]
    return flat


def _hermes_default_home() -> Path:
    """Portable Hermes home when nothing is configured."""
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "hermes"
    return Path.home() / ".hermes"


def load_settings(refresh: bool = False) -> dict:
    """Merged settings as a nested dict. Reads cache unless files changed."""
    global _CACHE, _CACHE_STAMP, _ISSUES, _SOURCES
    main, local = _main_path(), _local_path()
    stamp = tuple(
        (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
        for p in (main, local))
    if _CACHE is not None and stamp == _CACHE_STAMP and not refresh:
        return copy.deepcopy(_CACHE)

    cfg: dict = {}
    sources: dict = {}
    issues: list = []

    layers: list[tuple[str, dict]] = [("default", {k: d for k, (_t, _e, d) in SCHEMA.items()})]
    file_flat, iss = _layer_from_yaml(main, tracked=True)
    issues += iss
    layers.append(("file", file_flat))
    local_flat, iss = _layer_from_yaml(local, tracked=False)
    issues += iss
    layers.append(("local", local_flat))
    layers.append(("env", _layer_from_env()))

    flat: dict = {}
    for name, layer in layers:
        for key, raw in layer.items():
            if raw is None or raw == "":
                if name == "default":
                    flat[key] = raw
                    sources[key] = name
                continue  # an empty override does not clobber a lower layer
            kind, _env, _d = SCHEMA[key]
            val, issue = _coerce(kind, raw, key)
            if issue:
                issues.append(issue)
                continue
            flat[key] = val
            sources[key] = name

    # environment fallback for secret env-var NAMES: github.token_env may
    # name an arbitrary variable -- if that variable is set, it is the token.
    te = (flat.get("github.token_env") or "").strip()
    if te and te in os.environ and sources.get("github.token") != "local":
        flat["github.token"] = os.environ[te]
        sources["github.token"] = "env(%s)" % te
    be = (flat.get("notifications.bot_token_env") or "").strip()
    if be and be in os.environ and sources.get("notifications.bot_token") != "local":
        flat["notifications.bot_token"] = os.environ[be]
        sources["notifications.bot_token"] = "env(%s)" % be

    # portable defaults filled only when unset anywhere
    if not (flat.get("hermes.home") or "").strip():
        flat["hermes.home"] = str(_hermes_default_home())
        sources.setdefault("hermes.home", "default")
    if not (flat.get("hermes.python") or "").strip():
        home = Path(flat["hermes.home"])
        rel = ("hermes-agent/venv/Scripts/python.exe" if sys.platform == "win32"
               else "hermes-agent/venv/bin/python")
        flat["hermes.python"] = str(home / rel)
        sources.setdefault("hermes.python", "default")
    if not (flat.get("hermes.config_file") or "").strip():
        flat["hermes.config_file"] = str(Path(flat["hermes.home"]) / "config.yaml")
        sources.setdefault("hermes.config_file", "default")
    if not (flat.get("hermes.profile") or "").strip():
        flat["hermes.profile"] = "default"
    if not (flat.get("projects.manifest") or "").strip():
        flat["projects.manifest"] = str(ROOT / "improve.yaml")

    issues += _validate_semantics(flat, sources)

    for key, val in flat.items():
        section, leaf = key.split(".", 1)
        cfg.setdefault(section, {})[leaf] = val

    _CACHE, _CACHE_STAMP, _ISSUES, _SOURCES = cfg, stamp, issues, sources
    return copy.deepcopy(cfg)


def _validate_semantics(flat: dict, sources: dict) -> list:
    issues: list = []
    for key in ("runtime.round_timeout", "runtime.worktree_abandon_secs",
                "runtime.gate_timeout"):
        v = flat.get(key)
        if isinstance(v, int) and v <= 0:
            issues.append(Issue("error", key, "must be > 0 (got %s)" % v,
                                "set %s to a positive number of seconds" % key))
    if flat.get("notifications.backend") not in ("discord", "none", ""):
        issues.append(Issue("error", "notifications.backend",
                            "unknown backend %r" % flat.get("notifications.backend"),
                            "use discord or none"))

    impl = (flat.get("models.implementer") or "").strip()
    rev = (flat.get("models.reviewer") or "").strip()
    for key, seat in (("models.implementer", impl), ("models.reviewer", rev)):
        if seat and not _SEAT_RE.match(seat):
            issues.append(Issue(
                "error", key,
                "model seat %r is not provider:model (or custom:slug:model) "
                "form" % seat,
                'e.g. "openrouter:anthropic/claude-sonnet-4" or '
                '"custom:mybox:mymodel" -- a bare model name mis-parses to a cloud provider'))
    if impl and rev and impl == rev:
        issues.append(Issue("error", "models.reviewer",
                            "implementer and reviewer are the same model -- "
                            "a model cannot review its own work",
                            "point models.reviewer at a different model"))

    if flat.get("github.enabled"):
        tok = (flat.get("github.token") or "").strip()
        owner = (flat.get("github.owner") or "").strip()
        gh_cli = (flat.get("github.gh_cli") or "gh").strip()
        if not tok and gh_cli in ("gh", Path(gh_cli).name):
            issues.append(Issue(
                "warn", "github.token",
                "GitHub is enabled but no token is available (no %s, no %s "
                "in %s, and gh may or may not be logged in)"
                % (flat.get("github.token_env") or "GITHUB_TOKEN",
                   "token", SETTINGS_LOCAL.name),
                "run `gh auth login`, or export GITHUB_TOKEN, or set token in "
                + SETTINGS_LOCAL.name))
        if flat.get("github.require_repo") and not owner:
            issues.append(Issue("warn", "github.owner",
                                "require_repo is on but no owner is set -- "
                                "auto-creating repos needs to know whose account",
                                "set github.owner in settings.yaml"))
    if flat.get("notifications.enabled") and flat.get("notifications.backend") == "discord":
        if not (flat.get("notifications.bot_token") or "").strip():
            issues.append(Issue("error", "notifications.bot_token",
                                "notifications are enabled but no Discord bot "
                                "token resolves from env or %s" % SETTINGS_LOCAL.name,
                                "export DISCORD_BOT_TOKEN, set bot_token in %s, "
                                "or turn notifications off" % SETTINGS_LOCAL.name))
        if not (flat.get("notifications.channel_id") or "").strip():
            issues.append(Issue("error", "notifications.channel_id",
                                "notifications are enabled but no channel is "
                                "configured",
                                "set notifications.channel_id or "
                                "IMPROVER_CHANNEL_ID (or write state/channel_id.txt)"))
    return issues


# ---------------------------------------------------------------- accessors

def issues() -> list:
    load_settings()
    return list(_ISSUES)


def source_of(key: str) -> str:
    load_settings()
    return _SOURCES.get(key, "default")


def setting(key: str, default=None):
    section, leaf = key.split(".", 1)
    return load_settings().get(section, {}).get(leaf, default)


# ---- GitHub ---------------------------------------------------------------

def github_enabled() -> bool:
    return bool(setting("github.enabled"))


def github_token() -> str | None:
    if not github_enabled():
        return None
    tok = (setting("github.token") or "").strip()
    return tok or None


def github_owner() -> str:
    return (setting("github.owner") or "").strip()


def gh_cli() -> str:
    return (setting("github.gh_cli") or "gh").strip() or "gh"


def require_repo() -> bool:
    return bool(setting("github.require_repo"))


def push_branches() -> bool:
    return bool(setting("github.push_branches"))


def push_enabled(loop_cfg: dict | None = None) -> bool:
    """Effective push decision: settings master switch AND the runtime
    loop.json toggle (loopctl config --push-github false)."""
    if not github_enabled():
        return False
    if loop_cfg is not None and not loop_cfg.get("pushed_to_github", True):
        return False
    return True


def push_env(base: dict | None = None, token: str | None = None) -> dict:
    """Env for a git push that must authenticate WITHOUT any interactive
    prompt. With a token, hands git (and gh) a non-interactive bearer
    header; without one, keeps the fail-fast empty-credential hardening."""
    env = dict(base if base is not None else os.environ)
    tok = token if token is not None else github_token()
    if tok:
        env.pop("GIT_ASKPASS", None)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = "Authorization: Bearer " + tok
        env["GH_TOKEN"] = tok
        env.pop("GITHUB_TOKEN", None)
        env["GITHUB_TOKEN"] = tok
    else:
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_ASKPASS": "true",
            "SSH_ASKPASS": "true",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "core.askpass",
            "GIT_CONFIG_VALUE_1": "",
        })
    return env


# ---- notifications ----------------------------------------------------------

def notify_enabled() -> bool:
    return bool(setting("notifications.enabled"))


def notify_backend() -> str:
    return (setting("notifications.backend") or "none").strip().lower()


def notify_include_summaries() -> bool:
    """Whether round messages carry the plain-English round summary."""
    return bool(setting("notifications.include_summaries"))


def notify_token() -> str | None:
    tok = (setting("notifications.bot_token") or "").strip()
    return tok or None


def notify_channel() -> str:
    return (setting("notifications.channel_id") or "").strip()


def notify_ping_user() -> str:
    return (setting("notifications.ping_user_id") or "").strip()


def notify_ping_on_stuck() -> bool:
    return bool(setting("notifications.ping_on_stuck"))


def notify_env_file() -> str:
    return (setting("notifications.env_file") or "").strip()


# ---- hermes / models / projects / runtime -----------------------------------

def hermes_home() -> Path:
    return Path(setting("hermes.home") or _hermes_default_home())


def hermes_python() -> Path:
    return Path(setting("hermes.python"))


def hermes_config_file() -> Path:
    return Path(setting("hermes.config_file"))


def hermes_profile() -> str:
    return (setting("hermes.profile") or "default")


def builder_fallback() -> str:
    for key in ("models.builder_fallback", "models.implementer"):
        v = (setting(key) or "").strip()
        if v:
            return v
    return ""


def brief_model() -> str:
    return (setting("models.brief_model") or "").strip()


def brief_profile() -> str:
    return (setting("models.brief_profile") or hermes_profile() or "default")


def manifest_path() -> Path:
    return Path(setting("projects.manifest") or (ROOT / "improve.yaml"))


def index_rows_path() -> Path | None:
    v = (setting("projects.index_rows") or "").strip()
    return Path(v) if v else None


def seat_defaults() -> tuple[str, str]:
    return ((setting("models.implementer") or "").strip(),
            (setting("models.reviewer") or "").strip())


def implementer_seat() -> str:
    """Settings-layer default for the implementer seat. loop.json (set via
    `loopctl config --implementer`) still wins at runtime; this is what a
    fresh deployment seeds it from -- no hardcoded fleet model names."""
    return (setting("models.implementer") or "").strip()


def reviewer_seat() -> str:
    return (setting("models.reviewer") or "").strip()


def summary_endpoints() -> list[tuple[str, str]]:
    """[(url, model), ...] for the plain-English round-summary LLM.

    OpenAI-compatible /v1/chat/completions endpoints, primary first, optional
    fallback second. Empty when unconfigured -- callers must then skip summary
    generation (never fall back to a hardcoded server or model name)."""
    out: list[tuple[str, str]] = []
    for ukey, mkey in (("models.summary_url", "models.summary_model"),
                       ("models.summary_url_2", "models.summary_model_2")):
        u = (setting(ukey) or "").strip()
        if u:
            out.append((u, (setting(mkey) or "").strip() or "gpt-4o-mini"))
    return out


def round_timeout() -> int:
    return int(setting("runtime.round_timeout") or 4200)


def worktree_abandon_secs() -> int:
    return int(setting("runtime.worktree_abandon_secs") or 21600)


def gate_timeout_default() -> int:
    return int(setting("runtime.gate_timeout") or 900)


def angles_per_visit_default() -> int:
    """Seed for a fresh loop.json (loop.json itself wins once it exists)."""
    return max(1, int(setting("runtime.angles_per_visit") or 2))


def sweep_minutes_default() -> int:
    return int(setting("runtime.sweep_minutes") or 360)


def max_rounds_default() -> int:
    return int(setting("runtime.max_rounds") or 0)


def max_hours_default() -> int:
    return int(setting("runtime.max_hours") or 0)


def git_author() -> tuple[str, str]:
    """(name, email) the loop uses for its OWN commits/tags."""
    return ((setting("git.author_name") or "Karpathy Loop").strip(),
            (setting("git.author_email") or "loop@local").strip())


def status_port() -> int:
    try:
        p = int(setting("widget.status_port") or 8765)
    except (TypeError, ValueError):
        p = 8765
    return p if 1024 <= p <= 65535 else 8765


def sweep_roots() -> list:
    """Discovery sweep roots: projects.sweep_roots (comma/os.pathsep list)
    when set, else the portable home-folder defaults. Nothing ships with
    anyone's drive letters."""
    v = (setting("projects.sweep_roots") or "").strip()
    if v:
        return [p.strip() for p in re.split(r"[,;]", v.replace(os.pathsep, ";"))
                if p.strip()]
    return []


def reset_cache() -> None:
    """Test hook: force the next load_settings() to re-read disk + env."""
    global _CACHE, _CACHE_STAMP, _ISSUES, _SOURCES
    _CACHE, _CACHE_STAMP, _ISSUES, _SOURCES = None, None, [], {}


# ---------------------------------------------------------------- UI-facing
# The widget's settings card talks to the status server, which calls ONLY the
# functions below. They live here so the validation contract stays in ONE
# place: the card and the CLI validate through the identical code path.

def schema_view() -> list:
    """Machine-readable schema for the settings card: the card renders its
    form FROM THIS, so the UI and SCHEMA can never drift apart."""
    out = []
    for key, (kind, env, dflt) in SCHEMA.items():
        section, leaf = key.split(".", 1)
        out.append({"key": key, "section": section, "leaf": leaf,
                    "type": kind, "env": env, "default": dflt,
                    "secret": key in SECRET_LEAVES,
                    "path": PATH_KEYS.get(key),
                    "help": HELP.get(key, ""),
                    "model_seat": leaf in ("implementer", "reviewer",
                                           "builder_fallback", "brief_model")})
    return out


def _effective_flat_with(overlay: dict) -> tuple[dict, dict]:
    """Effective flat settings with `overlay` (already-coerced values) applied
    on top of the last load -- what the world WOULD look like if we saved."""
    cfg = load_settings(refresh=True)
    flat: dict = {}
    for section, kv in cfg.items():
        for leaf, v in kv.items():
            flat["%s.%s" % (section, leaf)] = v
    sources = dict(_SOURCES)
    for key, val in overlay.items():
        flat[key] = val
        sources[key] = "local"
    return flat, sources


def _write_local(updates: dict, removals: list) -> None:
    """One atomic write of several dotted keys into settings.local.yaml.
    updates may hold real values (already coerced); removals drops keys.
    This is the ONLY file-path the UI write endpoints touch."""
    import yaml
    path = _local_path()
    doc: dict = {}
    if path.exists():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            raise ValueError("%s top level must be a mapping" % path.name)
    for key in removals:
        section, leaf = key.split(".", 1)
        if isinstance(doc.get(section), dict) and leaf in doc[section]:
            del doc[section][leaf]
            if not doc[section]:
                del doc[section]
    for key, val in updates.items():
        section, leaf = key.split(".", 1)
        if not isinstance(doc.get(section), dict):
            doc[section] = {}
        doc[section][leaf] = val
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, width=100), encoding="utf-8")
    os.replace(tmp, path)


def apply_ui_updates(values: dict) -> tuple[list, dict]:
    """Bulk write from the settings card (non-secret keys).

    values: {dotted_key: raw}. NOTHING is written unless every key coerces
    and the resulting config is free of ERRORS (warnings pass through).
    Returns (issues, redacted_view). Secret keys are refused here -- they
    have their own put/clear endpoints so their values never ride a bulk
    payload where they could be logged."""
    issues: list = []
    coerced: dict = {}
    for key, raw in (values or {}).items():
        if key in SECRET_LEAVES:
            issues.append(Issue("error", key,
                                "secrets are not set through the bulk save "
                                "(use the secret field's own Save button)",
                                "use POST /settings/secret for this key"))
            continue
        if key not in SCHEMA:
            issues.append(Issue("warn", key, "unknown setting (ignored)",
                                "check the spelling; the card lists every key"))
            continue
        kind = SCHEMA[key][0]
        if raw == "" or raw is None:
            # Clearing = REMOVE the key from settings.local.yaml (writing ""
            # would not clear: an empty override never clobbers a lower layer).
            coerced[key] = None
            continue
        val, issue = _coerce(kind, raw, key)
        if issue:
            issues.append(issue)
            continue
        coerced[key] = val
    errs = [i for i in issues if i.level == "error"]
    if errs:
        return issues, redacted_view()

    overlay = {k: v for k, v in coerced.items() if v is not None}
    flat, sources = _effective_flat_with(overlay)
    sem = _validate_semantics(flat, sources)
    if [i for i in sem if i.level == "error"]:
        return sem + issues, redacted_view()

    updates = {k: v for k, v in coerced.items() if v is not None}
    removals = [k for k, v in coerced.items() if v is None]
    if updates or removals:
        _write_local(updates, removals)
    load_settings(refresh=True)
    return list(_ISSUES), redacted_view()


def put_secret(key: str, value: str) -> None:
    """Store one secret into settings.local.yaml ONLY. Never writes the
    tracked file, never logs or returns the value."""
    if key not in SECRET_LEAVES:
        raise ValueError("%s is not a secret setting (known: %s)"
                         % (key, ", ".join(sorted(SECRET_LEAVES))))
    v = str(value or "").strip()
    if not v:
        raise ValueError("empty value: use the clear action to unset")
    _write_local({key: v}, [])


def clear_secret(key: str) -> None:
    if key not in SECRET_LEAVES:
        raise ValueError("%s is not a secret setting" % key)
    _write_local({}, [key])


# ---------------------------------------------------------------- CLI plumbing
# loopctl.py settings <verb> delegates here; kept in settings.py so the
# settings layer owns its own file format.

EXAMPLE_KEYS = list(SCHEMA.keys())


def write_setting(key: str, raw_value: str, tracked: bool = False) -> None:
    """Set one dotted key in settings.local.yaml (default) or settings.yaml.
    Validates against the schema BEFORE writing; refuses secrets in tracked."""
    if key not in SCHEMA:
        raise SystemExit("unknown setting %r -- `loopctl settings show` lists every key" % key)
    if key in SECRET_LEAVES and tracked:
        raise SystemExit("refusing to write %s into the TRACKED settings.yaml "
                         "(it would be committed). Drop --tracked to write "
                         "settings.local.yaml instead (gitignored), or name "
                         "an env var with the *_env key instead." % key)
    kind = SCHEMA[key][0]
    val, issue = _coerce(kind, raw_value, key)
    if issue:
        raise SystemExit(str(issue))
    path = _main_path() if tracked else _local_path()
    doc: dict = {}
    if path.exists():
        try:
            import yaml
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            raise SystemExit("cannot parse %s: %s" % (path.name, e))
    section, leaf = key.split(".", 1)
    doc.setdefault(section, {})[leaf] = val
    try:
        import yaml
    except ImportError:
        raise SystemExit("PyYAML is required for `settings set`")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, width=100), encoding="utf-8")
    os.replace(tmp, path)


def unset_setting(key: str, tracked: bool = False) -> None:
    if key not in SCHEMA:
        raise SystemExit("unknown setting %r" % key)
    path = _main_path() if tracked else _local_path()
    if not path.exists():
        return
    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section, leaf = key.split(".", 1)
    if isinstance(doc.get(section), dict) and leaf in doc[section]:
        del doc[section][leaf]
        if not doc[section]:
            del doc[section]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(doc, sort_keys=False, width=100), encoding="utf-8")
        os.replace(tmp, path)


def redacted_view() -> dict:
    """Effective settings with secrets masked -- safe to print."""
    cfg = load_settings()
    out = {}
    for section, kv in cfg.items():
        out[section] = {}
        for leaf, v in kv.items():
            key = "%s.%s" % (section, leaf)
            if key in SECRET_LEAVES:
                src = _SOURCES.get(key, "default")
                out[section][leaf] = ("<set via %s>" % src) if v else "<unset>"
            else:
                out[section][leaf] = v
    return out


def _example_yaml_text() -> str:
    lines = [
        "# settings.yaml -- Karpathy Loop configuration for THIS deployment.",
        "# Everything here is optional; these are the safe built-in defaults.",
        "# Machine-specific values and SECRETS belong in settings.local.yaml",
        "# (gitignored). A token in this tracked file is rejected by validation.",
        "",
        "github:",
        "  enabled: false            # master switch: false = tags stay local, no pushes",
        "  owner: \"\"                 # your GitHub username (for repo creation hints)",
        "  token_env: GITHUB_TOKEN   # NAME of the env var holding your token",
        "  require_repo: false       # true = refuse rounds on repos with no remote",
        "  gh_cli: gh                # path to the gh binary if it is not on PATH",
        "  push_branches: true       # push the working branch each round (not just tags)",
        "",
        "notifications:",
        "  enabled: false            # master switch for progress/stuck/ask pings",
        "  backend: none             # discord | none  (add more senders in",
        "                            #   notify_senders.py: slack/telegram fit here)",
        "  include_summaries: true   # round messages carry the plain-English summary",
        "  bot_token_env: DISCORD_BOT_TOKEN",
        "  channel_id: \"\"            # the channel the loop posts into",
        "  ping_user_id: \"\"          # Discord user id that gets @mentioned on questions",
        "  ping_on_stuck: false      # also @mention when the loop reports STUCK",
        "  env_file: \"\"              # optional KEY=VALUE file (e.g. Hermes' .env) for the above",
        "",
        "hermes:",
        "  home: \"\"                  # Hermes home dir (default: ~/.hermes or %LOCALAPPDATA%\\hermes)",
        "  python: \"\"                # interpreter that launches hermes CLI children",
        "  profile: default          # Hermes profile the loop threads run under",
        "  config_file: \"\"           # hermes config.yaml (validated against model seats)",
        "",
        "models:",
        "  implementer: \"\"           # provider:model seat writing the change",
        "  reviewer: \"\"              # provider:model seat reading the diff (MUST differ)",
        "  builder_fallback: \"\"      # seat used when loop.json has no implementer yet",
        "  brief_model: \"\"           # model for the one-shot repo briefs",
        "  brief_profile: \"\"",
        "  summary_url: \"\"           # OpenAI-compatible /v1/chat/completions endpoint",
        "                            #   for the fifth-grade round summaries",
        "  summary_model: \"\"         # model name on that endpoint",
        "  summary_url_2: \"\"         # optional fallback endpoint",
        "  summary_model_2: \"\"       # its model name",
        "",
        "projects:",
        "  manifest: \"\"              # improve.yaml path (default: alongside the loop)",
        "  index_rows: \"\"            # optional rows.json seed for project discovery",
        "  sweep_roots: \"\"           # discovery scan roots (comma-separated);",
        "                            #   empty = your home + standard code folders",
        "",
        "runtime:",
        "  round_timeout: 4200       # hard ceiling per round (seconds)",
        "  worktree_abandon_secs: 21600",
        "  gate_timeout: 900         # default gate command timeout (seconds)",
        "  angles_per_visit: 2       # seed for a fresh rotation (loop.json wins)",
        "  sweep_minutes: 360        # rotation sweep cadence (seed)",
        "  max_rounds: 0             # autopause after N rounds (0 = forever)",
        "  max_hours: 0              # autopause after N hours (0 = forever)",
        "",
        "git:",
        "  author_name: Karpathy Loop    # identity on the loop's own commits/tags",
        "  author_email: loop@local",
        "",
        "widget:",
        "  status_port: 8765         # port scripts/status_server.py binds;",
        "                            #   the widget card talks to this server",
        "",
    ]
    return "\n".join(lines)


def write_example(dest: Path | None = None) -> Path:
    dest = dest or SETTINGS_FILE
    dest.write_text(_example_yaml_text(), encoding="utf-8")
    return dest


if __name__ == "__main__":
    import json as _json
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "show":
        print(_json.dumps(redacted_view(), indent=2))
    elif cmd == "validate":
        load_settings(refresh=True)
        bad = 0
        for i in issues():
            print(i)
            bad += (i.level == "error")
        raise SystemExit(1 if bad else 0)
    elif cmd == "example":
        print(_example_yaml_text())
    else:
        raise SystemExit("usage: settings.py show|validate|example")
