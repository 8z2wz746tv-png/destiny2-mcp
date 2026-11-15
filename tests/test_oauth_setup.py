"""OAuth receipt is distinct from successful token exchange and persistence."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from destiny_mcp import oauth_setup


@pytest.fixture
def callback(monkeypatch):
    monkeypatch.setattr(oauth_setup.OAuthCallbackHandler, "captured_code", None)
    monkeypatch.setattr(oauth_setup.OAuthCallbackHandler, "captured_error", None)
    monkeypatch.setattr(oauth_setup.OAuthCallbackHandler, "expected_state", "test-state")
    handler = object.__new__(oauth_setup.OAuthCallbackHandler)
    handler.path = "/callback?code=test-code&state=test-state"
    handler.wfile = io.BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler._shutdown_soon = Mock()
    return handler


def test_callback_only_confirms_receipt(callback):
    callback.do_GET()

    html = callback.wfile.getvalue().decode()
    callback.send_response.assert_called_once_with(200)
    assert "已收到授权回调" in html
    assert "登录尚未完成" in html
    assert "登录成功" not in html
    assert "test-code" not in html


@pytest.mark.parametrize("path", [
    "/callback?code=test-code&state=wrong-state",
    "/callback?error=access_denied&state=test-state",
    "/callback?state=test-state",
])
def test_invalid_callback_does_not_capture_code(callback, path):
    callback.path = path
    callback.do_GET()

    assert oauth_setup.OAuthCallbackHandler.captured_code is None
    callback.send_response.assert_called_once_with(400)


@pytest.mark.parametrize("outcome", ["success", "exchange_failure", "save_failure"])
def test_login_success_requires_exchange_and_save(
    callback, outcome, monkeypatch, tmp_path, capsys,
):
    config = SimpleNamespace(DESTINY_TOKEN_PATH=tmp_path, BUNGIE_CLIENT_ID="1")
    token_data = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}
    monkeypatch.setattr(oauth_setup, "_load_config", lambda: config)

    def serve(*args):
        callback.do_GET()
        return oauth_setup.OAuthCallbackHandler.captured_code

    exchange = Mock(return_value=token_data)
    if outcome == "exchange_failure":
        exchange.side_effect = SystemExit("synthetic exchange failure")
    save = Mock(wraps=oauth_setup._save_tokens)
    if outcome == "save_failure":
        save.side_effect = OSError("synthetic disk failure")
    monkeypatch.setattr(oauth_setup, "_serve_for_code", serve)
    monkeypatch.setattr(oauth_setup, "_exchange_code", exchange)
    monkeypatch.setattr(oauth_setup, "_save_tokens", save)

    if outcome == "success":
        oauth_setup.main(["--no-open"])
        assert (tmp_path / "tokens.json").is_file()
        assert (tmp_path / "tokens.json").stat().st_mode & 0o777 == 0o600
    else:
        with pytest.raises((SystemExit, OSError)):
            oauth_setup.main(["--no-open"])
        assert not (tmp_path / "tokens.json").exists()

    output = capsys.readouterr().out
    assert ("Bungie 登录完成" in output) is (outcome == "success")
    assert "登录尚未完成" in callback.wfile.getvalue().decode()
    assert "synthetic-access" not in output
    assert "synthetic-refresh" not in output
    if outcome == "exchange_failure":
        save.assert_not_called()


def test_missing_openssl_explains_the_manual_route(tmp_path, monkeypatch):
    """缺 openssl 时给的是人话 + --manual，不是裸 traceback。

    Windows 默认没有 openssl；以前的实现直接抛 FileNotFoundError，
    用户看不出还有一条不需要本地回调的路。
    """
    def boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "openssl")

    monkeypatch.setattr(oauth_setup.subprocess, "run", boom)

    with pytest.raises(SystemExit) as excinfo:
        oauth_setup._generate_self_signed_cert(tmp_path)

    message = str(excinfo.value)
    assert "--manual" in message
    assert "openssl" in message


def test_openssl_failure_reports_its_output(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise oauth_setup.subprocess.CalledProcessError(
            1, "openssl", stderr=b"synthetic openssl failure"
        )

    monkeypatch.setattr(oauth_setup.subprocess, "run", boom)

    with pytest.raises(SystemExit) as excinfo:
        oauth_setup._generate_self_signed_cert(tmp_path)

    message = str(excinfo.value)
    assert "--manual" in message
    assert "synthetic openssl failure" in message


# ── 2026-09-23 真机反转：授权 URL 不许带 scope ──────────────────────────────


def test_auth_url_never_carries_scope() -> None:
    """Bungie 现在明确拒绝：`Scope is always configured value. Do not specify scope parameter.`

    真机回包（Windows / client_id=54207）：回调带 `error=invalid_scope`，登录 100% 失败。
    scope 改由 Developer Portal 的应用配置决定，URL 上一个字符都不能带。
    """
    from destiny_mcp.oauth_setup import _auth_url

    url = _auth_url("12345", "https://localhost:8765/callback", "STATE")
    assert "scope" not in url.lower(), f"授权 URL 里不许出现 scope：{url}"
    for part in ("client_id=12345", "response_type=code", "state=STATE", "redirect_uri="):
        assert part in url


def test_callback_surfaces_bungie_error_before_state_check() -> None:
    """Bungie 已明确回 error 时先透出它 —— 以前 state 在前，`invalid_scope` 被吞成 `invalid_state`。"""
    from destiny_mcp import oauth_setup

    handler = object.__new__(oauth_setup.OAuthCallbackHandler)
    handler.path = "/callback?error=invalid_scope&error_description=Do%20not%20specify&state=WRONG"
    handler.expected_state = "RIGHT"
    sent: list[tuple] = []
    handler._send_html = lambda status, title, message, **kw: sent.append((status, title, message))
    handler._shutdown_soon = lambda: None
    oauth_setup.OAuthCallbackHandler.captured_error = None
    oauth_setup.OAuthCallbackHandler.captured_code = None

    handler.do_GET()

    assert oauth_setup.OAuthCallbackHandler.captured_error == "invalid_scope"
    assert oauth_setup.OAuthCallbackHandler.captured_code is None
    text = " ".join(str(x) for row in sent for x in row)
    assert "invalid_scope" in text and "Do not specify" in text
    assert "state 校验失败" not in text, "真错误不许被 state 文案盖住"
