"""Personal OAuth helper for Destiny MCP.

The intended flow is:
1. A local agent installs this project.
2. The agent runs this helper and gives the printed login URL to the user.
3. The user opens the URL and logs in with Bungie.
4. This helper catches the localhost callback and writes tokens.json.
"""

from __future__ import annotations

import argparse
import html as html_utils
import json
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import httpx

from .exceptions import ConfigError

DEFAULT_PORT = 8765
DEFAULT_CALLBACK_PATH = "/callback"
DEFAULT_REDIRECT_URI = f"https://localhost:{DEFAULT_PORT}{DEFAULT_CALLBACK_PATH}"
TOKEN_ENDPOINT = "https://www.bungie.net/platform/app/oauth/token/"
AUTHORIZE_ENDPOINT = "https://www.bungie.net/zh-chs/OAuth/Authorize"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    captured_code: str | None = None
    captured_error: str | None = None
    expected_path: str = DEFAULT_CALLBACK_PATH
    expected_state: str | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != self.expected_path:
            self._send_html(
                404,
                "未找到回调地址",
                "这个页面不是 Destiny MCP 等待的 OAuth 回调地址。",
                success=False,
            )
            return

        params = parse_qs(parsed.query)
        error = params.get("error", [""])[0]
        code = params.get("code", [""])[0]
        state = params.get("state", [""])[0]
        if self.expected_state and state != self.expected_state:
            OAuthCallbackHandler.captured_error = "invalid_state"
            self._send_html(400, "授权失败", "OAuth state 校验失败，请重新运行登录命令。", success=False)
            self._shutdown_soon()
            return
        if error:
            OAuthCallbackHandler.captured_error = error
            self._send_html(400, "授权失败", f"Bungie 返回错误：{error}", success=False)
            self._shutdown_soon()
            return
        if not code:
            self._send_html(400, "授权失败", "回调地址里没有 code 参数。", success=False)
            return

        OAuthCallbackHandler.captured_code = code
        self._send_html(
            200,
            "已收到授权回调",
            "正在交换并保存 Token，登录尚未完成。请回到终端查看最终结果；"
            "只有显示“Bungie 登录完成”才表示 Token 已保存。",
        )
        self._shutdown_soon()

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _shutdown_soon(self) -> None:
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _send_html(self, status: int, title: str, message: str, *, success: bool = True) -> None:
        color = "#61d394" if success else "#ff6b6b"
        title_html = html_utils.escape(title)
        message_html = html_utils.escape(message)
        html_doc = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>Destiny MCP - {title_html}</title>
<body style="margin:0;background:#10131a;color:#e7edf7;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">
  <main style="max-width:560px;margin:96px auto;padding:0 24px;text-align:center;">
    <h1 style="color:{color};font-size:28px;margin-bottom:16px;">{title_html}</h1>
    <p style="line-height:1.8;font-size:16px;">{message_html}</p>
  </main>
</body>
</html>"""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_doc.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html_doc.encode("utf-8"))


def _load_config():
    try:
        from . import config
    except ConfigError as exc:
        print("❌ 配置不完整：", exc)
        print()
        print("请先在项目目录创建 .env：")
        print("  cp .env.example .env")
        print("然后填入 BUNGIE_API_KEY / BUNGIE_CLIENT_ID / BUNGIE_CLIENT_SECRET")
        raise SystemExit(2) from exc
    return config


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Destiny MCP 个人版 Bungie 登录助手")
    parser.add_argument(
        "--redirect-uri",
        help=(
            "OAuth 回调地址，必须和 Bungie Developer Portal 里配置的一致。"
            f"默认：{DEFAULT_REDIRECT_URI}"
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="本地回调端口，默认 8765")
    parser.add_argument("--no-open", action="store_true", help="只打印登录链接，不自动打开浏览器")
    parser.add_argument("--timeout", type=int, default=300, help="等待登录回调秒数，默认 300")
    parser.add_argument(
        "--code",
        help="兜底模式：直接传入 Bungie 回调 URL 或 code，跳过本地回调服务",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="兜底模式：手动粘贴 Bungie 回调 URL 或 code",
    )
    return parser.parse_args(argv)


def _extract_code(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        return parse_qs(parsed.query).get("code", [""])[0].strip()
    return raw


def _redirect_uri(args: argparse.Namespace, config) -> str:
    env_uri = getattr(config, "DESTINY_OAUTH_REDIRECT_URI", None)
    if args.redirect_uri:
        return args.redirect_uri
    if env_uri:
        return env_uri
    if args.port != DEFAULT_PORT:
        return f"https://localhost:{args.port}{DEFAULT_CALLBACK_PATH}"
    return DEFAULT_REDIRECT_URI


def _auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    return (
        f"{AUTHORIZE_ENDPOINT}"
        f"?client_id={quote(str(client_id), safe='')}"
        "&response_type=code"
        f"&state={quote(state, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
    )


def _generate_self_signed_cert(cert_dir: Path) -> tuple[Path, Path]:
    key_file = cert_dir / "key.pem"
    cert_file = cert_dir / "cert.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_file),
            "-out",
            str(cert_file),
            "-days",
            "7",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert_file, key_file


def _serve_for_code(redirect_uri: str, timeout: int, expected_state: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.hostname != "localhost":
        raise SystemExit("❌ 自动回调地址必须使用 localhost。请改用 --manual 或 --code。")
    if parsed.scheme != "https":
        raise SystemExit("❌ 自动回调地址必须使用 https://localhost。请改用 --manual 或 --code。")
    if not parsed.port:
        raise SystemExit("❌ redirect_uri 必须包含端口，例如 https://localhost:8765/callback")

    OAuthCallbackHandler.captured_code = None
    OAuthCallbackHandler.captured_error = None
    OAuthCallbackHandler.expected_path = parsed.path or DEFAULT_CALLBACK_PATH
    OAuthCallbackHandler.expected_state = expected_state

    cert_dir: Path | None = None
    try:
        server = HTTPServer(("localhost", parsed.port), OAuthCallbackHandler)
    except OSError as exc:
        raise SystemExit(
            f"❌ 无法监听 localhost:{parsed.port}。请换一个 --port，或关闭占用该端口的程序。"
        ) from exc
    try:
        if parsed.scheme == "https":
            cert_dir = Path(tempfile.mkdtemp(prefix="destiny_oauth_"))
            cert_file, key_file = _generate_self_signed_cert(cert_dir)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_file), str(key_file))
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        deadline = time.monotonic() + timeout
        while (
            not OAuthCallbackHandler.captured_code
            and not OAuthCallbackHandler.captured_error
            and time.monotonic() < deadline
        ):
            server.timeout = min(1, max(0.1, deadline - time.monotonic()))
            server.handle_request()
    finally:
        server.server_close()
        if cert_dir is not None:
            import shutil

            shutil.rmtree(cert_dir, ignore_errors=True)

    if OAuthCallbackHandler.captured_error:
        raise SystemExit(f"❌ Bungie 授权失败：{OAuthCallbackHandler.captured_error}")
    if not OAuthCallbackHandler.captured_code:
        raise SystemExit("❌ 等待登录超时。请重新运行脚本，或使用 --manual 粘贴回调 URL。")
    return OAuthCallbackHandler.captured_code


def _exchange_code(config, code: str, redirect_uri: str) -> dict:
    response = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config.BUNGIE_CLIENT_ID,
            "client_secret": config.BUNGIE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(f"❌ Token 交换失败 ({response.status_code}): {response.text}")
    return response.json()


def _save_tokens(config, token_data: dict) -> Path:
    token_file = config.DESTINY_TOKEN_PATH / "tokens.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at": time.time() + int(token_data.get("expires_in", 3600)),
        "membership_id": token_data.get("membership_id", ""),
    }
    token_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return token_file


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = _load_config()
    redirect_uri = _redirect_uri(args, config)

    print("=" * 64)
    print("Destiny MCP 个人版登录")
    print("=" * 64)
    print(f"Token 保存位置: {config.DESTINY_TOKEN_PATH / 'tokens.json'}")
    print(f"OAuth 回调地址: {redirect_uri}")
    print()

    if args.code or args.manual:
        code_source = args.code or input("请粘贴 Bungie 回调 URL 或 code: ")
        code = _extract_code(code_source)
        if not code:
            raise SystemExit("❌ 没有解析到 code。")
    else:
        state = secrets.token_urlsafe(18)
        login_url = _auth_url(config.BUNGIE_CLIENT_ID, redirect_uri, state)
        print("请把下面这个登录链接发给用户打开：")
        print(login_url)
        print()
        print("用户登录完成后，此命令会自动保存 token。")
        print("如果浏览器无法回跳本机，可以重新运行：destiny-mcp-oauth --manual")
        print()
        sys.stdout.flush()
        if not args.no_open:
            webbrowser.open(login_url)
        code = _serve_for_code(redirect_uri, args.timeout, state)

    print("正在交换并保存 token...")
    token_data = _exchange_code(config, code, redirect_uri)
    token_file = _save_tokens(config, token_data)
    expires_at = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(time.time() + int(token_data.get("expires_in", 3600))),
    )
    print("✅ Bungie 登录完成")
    print(f"Token: {token_file}")
    print(f"Membership ID: {token_data.get('membership_id', '')}")
    print(f"Refresh Token: {'有' if token_data.get('refresh_token') else '无'}")
    print(f"Access Token 过期时间: {expires_at}")


if __name__ == "__main__":
    main()
