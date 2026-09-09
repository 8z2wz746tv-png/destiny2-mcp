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
