import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import gateway_auth

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "print_token.py"


@pytest.fixture
def print_token():
    spec = importlib.util.spec_from_file_location("print_token", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(print_token, monkeypatch, fake_token_fn):
    monkeypatch.setattr(print_token, "get_iam_token", fake_token_fn)


def test_prints_the_bare_token(print_token, monkeypatch, capsys):
    load(print_token, monkeypatch, lambda force=False: "tok-bare")
    assert print_token.main([]) == 0
    assert capsys.readouterr().out == "tok-bare\n"


def test_header_file_is_atomic_private_and_replaced(print_token, monkeypatch, tmp_path, capsys):
    tokens = iter(["tok-1", "tok-2"])
    load(print_token, monkeypatch, lambda force=False: next(tokens))
    target = tmp_path / "sub" / "headers.txt"
    assert print_token.main(["--header-file", str(target)]) == 0
    assert target.read_text() == "Authorization: Bearer tok-1\n"
    assert capsys.readouterr().out == ""  # the token is not printed
    assert print_token.main(["--header-file", str(target)]) == 0
    assert target.read_text() == "Authorization: Bearer tok-2\n"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["headers.txt"]  # no temp file left behind


def test_failed_write_keeps_the_old_file_and_leaves_no_temp_file(print_token, monkeypatch, tmp_path, capsys):
    load(print_token, monkeypatch, lambda force=False: "tok-new")
    target = tmp_path / "headers.txt"
    target.write_text("old")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(print_token.os, "replace", broken_replace)
    assert print_token.main(["--header-file", str(target)]) == 1
    assert "could not write" in capsys.readouterr().err
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["headers.txt"]


def test_auth_errors_have_no_traceback(print_token, monkeypatch, capsys):
    def fail(force=False):
        raise gateway_auth.AuthConfigError("IAM token request failed: HTTP 401 from IAM.")

    load(print_token, monkeypatch, fail)
    assert print_token.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "IAM token request failed" in captured.err


def test_script_runs_without_credentials():
    """End to end through the real code path: no credentials -> clean message, exit 1."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GREENNODE_", "GATEWAY_"))}
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert "GREENNODE_CLIENT_ID" in done.stderr and "Traceback" not in done.stderr
