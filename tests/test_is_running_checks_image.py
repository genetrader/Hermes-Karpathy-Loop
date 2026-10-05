"""F0-5 test: is_running() must verify IMAGE NAME + PID, not just PID presence.

The old logic returned True whenever tasklist printed ANY row for the PID --
`"python" in out.lower()` matched the FILTER ECHO line tasklist prints
(`"INFO: No tasks are running..."` contains no python, but a matched row's
image column, or even the word 'python' appearing anywhere in output) and
crucially matched ANY image (a recycled PID on cmd.exe counted as runner-alive).

This test pins the contract with tasklist stubs -- no live process needed.

Run: python -m pytest tests/test_is_running_checks_image.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karpathy_runner as K  # noqa: E402


def _set_pid(monkeypatch, tmp_path, pid):
    p = tmp_path / "runner.pid"
    p.write_text(str(pid), encoding="utf-8")
    monkeypatch.setattr(K, "PID_FILE", p)


def _stub_tasklist(monkeypatch, csv_output):
    class R:
        stdout = csv_output
        returncode = 0

    def fake_run(*a, **kw):
        return R()

    monkeypatch.setattr(K.subprocess, "run", fake_run)


def test_python_image_with_matching_pid_is_running(monkeypatch, tmp_path):
    _set_pid(monkeypatch, tmp_path, 4242)
    _stub_tasklist(monkeypatch,
                   '"Image Name","PID Session Name","Session#","Mem Usage"\n'
                   '"python.exe","4242 Console","1","25,604 K"\n')
    assert K.is_running() is True


def test_nonpython_image_with_same_pid_is_NOT_running(monkeypatch, tmp_path):
    """THE old bug: a recycled PID on cmd.exe read as 'runner alive'."""
    _set_pid(monkeypatch, tmp_path, 4242)
    _stub_tasklist(monkeypatch,
                   '"Image Name","PID Session Name","Session#","Mem Usage"\n'
                   '"cmd.exe","4242 Console","1","5,604 K"\n')
    assert K.is_running() is False


def test_python_image_with_DIFFERENT_pid_is_NOT_running(monkeypatch, tmp_path):
    _set_pid(monkeypatch, tmp_path, 4242)
    _stub_tasklist(monkeypatch,
                   '"Image Name","PID Session Name","Session#","Mem Usage"\n'
                   '"python.exe","99 Console","1","25,604 K"\n')
    assert K.is_running() is False


def test_no_tasks_row_is_not_running(monkeypatch, tmp_path):
    _set_pid(monkeypatch, tmp_path, 4242)
    _stub_tasklist(monkeypatch,
                   'INFO: No tasks are running which match the specified criteria.\n')
    assert K.is_running() is False


def test_pid_file_missing_is_not_running(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "PID_FILE", tmp_path / "absent.pid")
    assert K.is_running() is False