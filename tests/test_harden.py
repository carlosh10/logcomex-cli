"""Smoke tests for H-01 hardening. No live Logcomex login or network."""
from __future__ import annotations

import py_compile
import subprocess
import sys
import time
from http.cookiejar import Cookie, LWPCookieJar
from pathlib import Path

import pytest

import lx

ROOT = Path(__file__).resolve().parents[1]
CLI_MODULES = ("lx.py", "panel.py", "panel_build.py", "catalog.py")


def _cookie(name: str, value: str, *, expires: int | None, discard: bool) -> Cookie:
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        "example.com",
        True,
        False,
        "/",
        True,
        False,
        expires,
        discard,
        None,
        None,
        {},
    )


def test_cli_modules_compile() -> None:
    for name in CLI_MODULES:
        py_compile.compile(str(ROOT / name), doraise=True)


def test_login_rejects_code_on_argv() -> None:
    parser = lx.build_parser()
    with pytest.raises(SystemExit) as ei:
        parser.parse_args(["login", "--email", "a@b.com", "--code", "123456"])
    assert ei.value.code != 0


def test_login_code_argv_rejected_by_cli() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "lx.py"), "login", "--email", "a@b.com", "--code", "123456"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    combined = (r.stdout or "") + (r.stderr or "")
    assert "not taken on argv" in combined or "unrecognized arguments" in combined
    assert "123456" not in (r.stdout or "")


def test_login_accepts_code_file_not_code_flag() -> None:
    parser = lx.build_parser()
    args = parser.parse_args(["login", "--email", "a@b.com", "--code-file", "/tmp/otp"])
    assert args.code_file == "/tmp/otp"
    login = None
    for action in parser._subparsers._group_actions:  # type: ignore[attr-defined]
        chooser = action.choices
        if chooser and "login" in chooser:
            login = chooser["login"]
            break
    assert login is not None
    flags = {opt for action in login._actions for opt in action.option_strings}
    assert "--code-file" in flags
    assert "--password-file" in flags
    help_text = login.format_help()
    assert "--code-file" in help_text
    assert "--password-file" in help_text
    assert "6-digit" not in help_text


def test_login_code_file_does_not_print_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(lx, "CFG", tmp_path)
    monkeypatch.setattr(lx, "COOKIE_PATH", tmp_path / "cookies.txt")
    monkeypatch.setattr(lx, "SESSION_PATH", tmp_path / "session.json")
    secret = "654321"
    code_path = tmp_path / "otp.txt"
    code_path.write_text(secret + "\n")

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        assert method == "POST"
        assert path.endswith("/email-otp/verify")
        body = kwargs.get("body") or {}
        assert body.get("code") == secret
        return {"ok": True, "token": "t"}

    monkeypatch.setattr(lx, "request", fake_request)
    monkeypatch.setattr(lx, "_whoami_quiet", lambda: {"email": "a@b.com"})
    args = lx.build_parser().parse_args(
        ["login", "--email", "a@b.com", "--code-file", str(code_path)]
    )
    lx.cmd_login(args)
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_cookie_save_does_not_keep_discarded_or_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "cookies.txt"
    monkeypatch.setattr(lx, "CFG", tmp_path)
    monkeypatch.setattr(lx, "COOKIE_PATH", path)
    now = int(time.time())
    jar = LWPCookieJar(str(path))
    jar.set_cookie(_cookie("valid", "keep", expires=now + 3600, discard=False))
    jar.set_cookie(_cookie("expired", "drop", expires=now - 60, discard=False))
    jar.set_cookie(_cookie("discarded", "drop", expires=None, discard=True))
    lx.save_jar(jar)

    text = path.read_text()
    assert "valid" in text
    assert "expired" not in text
    assert "discarded" not in text

    loaded = lx.cookie_jar()
    assert {c.name for c in loaded} == {"valid"}


def test_cookie_load_does_not_reuse_discarded_or_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "cookies.txt"
    monkeypatch.setattr(lx, "CFG", tmp_path)
    monkeypatch.setattr(lx, "COOKIE_PATH", path)
    now = int(time.time())
    jar = LWPCookieJar(str(path))
    jar.set_cookie(_cookie("valid", "keep", expires=now + 3600, discard=False))
    jar.set_cookie(_cookie("expired", "drop", expires=now - 60, discard=False))
    jar.set_cookie(_cookie("discarded", "drop", expires=None, discard=True))
    # Simulate an old cookies.txt written with ignore_discard/ignore_expires.
    jar.save(ignore_discard=True, ignore_expires=True)

    loaded = lx.cookie_jar()
    assert {c.name for c in loaded} == {"valid"}


def test_panel_out_default_is_cwd_not_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = lx.build_parser().parse_args(["panel", "breaks"])
    assert args.out != "/workspace"
    out = lx.resolve_panel_out(args.out, "universe-selection-breaks")
    assert out.parent.resolve() == tmp_path.resolve()
    assert out.name == "intel-panel-breaks.png"
    assert Path("/workspace") not in out.parents
    assert out != Path("/workspace")


def test_dashboard_out_default_is_cwd_not_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = lx.build_parser().parse_args(["dashboard", "show", "airfryer"])
    assert args.out != "/workspace"
    out = lx.resolve_dashboard_out(args.out)
    assert out.resolve() == tmp_path.resolve()
    assert str(out.resolve()) != "/workspace"


def test_explicit_out_is_kept(tmp_path: Path) -> None:
    png = tmp_path / "custom.png"
    assert lx.resolve_panel_out(str(png), "universe-selection-lines") == png
    assert lx.resolve_dashboard_out(str(tmp_path)) == tmp_path
