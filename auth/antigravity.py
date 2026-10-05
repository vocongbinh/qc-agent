"""Google Antigravity OAuth2 & Credential Management.

Cơ chế xác thực theo chuẩn của Oh My Pi:
- OAuth2 PKCE qua Google Cloud Code Client ID
- Tự động lấy và refresh token qua https://oauth2.googleapis.com/token
- Khám phá cloudaicompanionProject qua daily-cloudcode-pa.googleapis.com
- Lưu trữ credentials cục bộ tại ~/.qc-agent/credentials.json
"""

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import webbrowser

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# RFC 8252 (OAuth 2.0 for Native Apps): Public client identifier for Cloud Code Assist desktop client.
# Configured via environment variables ANTIGRAVITY_CLIENT_ID / GOOGLE_CLIENT_ID.
CLIENT_ID = os.getenv("ANTIGRAVITY_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", ""))
CLIENT_SECRET = os.getenv("ANTIGRAVITY_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET", ""))
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

CALLBACK_PORT = 51121
CALLBACK_PATH = "/oauth-callback"
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}"

CLOUD_CODE_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
DEFAULT_ANTIGRAVITY_VERSION = "2.8.0"


def get_antigravity_user_agent() -> str:
    """Tạo User-Agent chuẩn của Antigravity tương tự Oh My Pi."""
    os_name = "darwin" if sys.platform == "darwin" else ("win32" if sys.platform == "win32" else "linux")
    arch = "arm64" if "arm" in sys.platform or "aarch64" in sys.platform or sys.platform == "darwin" else "x86_64"
    return f"antigravity/hub/{DEFAULT_ANTIGRAVITY_VERSION} (aidev_client; os_type={os_name}; arch={arch}; cl=963137146)"


def get_credentials_path() -> Path:
    """Đường dẫn lưu file credentials."""
    override = os.environ.get("QC_AGENT_CREDENTIALS_PATH")
    if override:
        return Path(override)
    base_dir = Path.home() / ".qc-agent"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "credentials.json"


def load_all_credentials() -> dict[str, Any]:
    """Đọc toàn bộ file credentials."""
    path = get_credentials_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Không thể đọc file credentials tại {path}: {e}")
        return {}


def save_credentials(provider: str, creds: dict[str, Any]) -> None:
    """Lưu credentials cho một provider cụ thể."""
    path = get_credentials_path()
    all_creds = load_all_credentials()
    all_creds[provider] = creds
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_creds, f, indent=2, ensure_ascii=False)


def generate_pkce() -> tuple[str, str]:
    """Sinh cặp PKCE verifier và challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler nhận authorization code từ callback của Google."""
    code: str | None = None
    state: str | None = None
    error: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Tắt log stdout của BaseHTTPRequestHandler

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == CALLBACK_PATH:
            params = urllib.parse.parse_qs(parsed_url.query)
            if "code" in params:
                OAuthCallbackHandler.code = params["code"][0]
                OAuthCallbackHandler.state = params.get("state", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """
                <!DOCTYPE html>
                <html>
                <head><title>Xác thực thành công</title></head>
                <body style="font-family: system-ui, sans-serif; text-align: center; padding: 50px;">
                    <h2 style="color: #10b981;">Đăng nhập Google Antigravity thành công!</h2>
                    <p>Bạn có thể đóng tab này và quay trở lại cửa sổ terminal của <b>qc-agent</b>.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
            elif "error" in params:
                OAuthCallbackHandler.error = params["error"][0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"Đăng nhập thất bại: {OAuthCallbackHandler.error}".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def exchange_code_for_token(code: str, verifier: str) -> dict[str, Any]:
    """Đổi authorization code lấy access token và refresh token."""
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Lỗi đổi token: {resp.status_code} - {resp.text}")
        return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Refresh access token khi hết hạn."""
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Lỗi làm mới token: {resp.status_code} - {resp.text}")
        return resp.json()


def fetch_user_email(access_token: str) -> str | None:
    """Lấy email tài khoản Google."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(USERINFO_URL, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("email")
    except Exception:
        pass
    return None


def discover_project(access_token: str) -> str:
    """Khám phá Cloud Code Companion Project của tài khoản (tương tự OMP)."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": get_antigravity_user_agent(),
    }
    load_url = f"{CLOUD_CODE_ENDPOINT}/v1internal:loadCodeAssist"
    onboard_url = f"{CLOUD_CODE_ENDPOINT}/v1internal:onboardUser"

    with httpx.Client(timeout=30) as client:
        resp = client.post(load_url, headers=headers, json={"metadata": {"ideType": "ANTIGRAVITY"}})
        if resp.status_code != 200:
            logger.warning(f"loadCodeAssist không thành công ({resp.status_code}): {resp.text}")
            return ""

        data = resp.json()
        project_id = data.get("cloudaicompanionProject")
        if project_id:
            return project_id

        # Nếu chưa onboard, kích hoạt onboard
        if not data.get("currentTier"):
            onboard_resp = client.post(
                onboard_url,
                headers=headers,
                json={"tierId": "free-tier", "metadata": {"ideType": "ANTIGRAVITY"}},
            )
            if onboard_resp.status_code == 200:
                # Refresh lại loadCodeAssist
                refresh_resp = client.post(load_url, headers=headers, json={"metadata": {"ideType": "ANTIGRAVITY"}})
                if refresh_resp.status_code == 200:
                    return refresh_resp.json().get("cloudaicompanionProject", "")

        return ""


def get_valid_antigravity_credentials() -> dict[str, Any] | None:
    """Lấy credentials hợp lệ, tự động refresh nếu sắp hết hạn (300s)."""
    all_creds = load_all_credentials()
    creds = all_creds.get("antigravity")
    if not creds:
        return None

    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")
    expires_at = creds.get("expires_at", 0)

    # Nếu token vẫn còn hạn > 300s
    if access_token and time.time() < (expires_at - 300):
        return creds

    # Nếu sắp hết hạn và có refresh token
    if refresh_token:
        try:
            new_data = refresh_access_token(refresh_token)
            creds["access_token"] = new_data["access_token"]
            creds["expires_at"] = time.time() + new_data.get("expires_in", 3600)
            if "refresh_token" in new_data:
                creds["refresh_token"] = new_data["refresh_token"]
            save_credentials("antigravity", creds)
            return creds
        except Exception as e:
            logger.error(f"Lỗi tự động làm mới access token: {e}")
            return None

    return None


def run_antigravity_login(timeout: int = 120, open_browser: bool = True) -> dict[str, Any]:
    """Thực hiện luồng đăng nhập OAuth Google Antigravity."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError(
            "Chưa cấu hình ANTIGRAVITY_CLIENT_ID hoặc ANTIGRAVITY_CLIENT_SECRET trong .env. "
            "Vui lòng thêm vào file .env."
        )
    verifier, challenge = generate_pkce()
    state = secrets.token_hex(16)

    OAuthCallbackHandler.code = None
    OAuthCallbackHandler.state = None
    OAuthCallbackHandler.error = None

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(auth_params)}"

    if open_browser:
        webbrowser.open(auth_url)

    print(f"\n[Antigravity Auth] Vui lòng mở trình duyệt và đăng nhập Google:")
    print(f"👉 {auth_url}\n")
    print(f"Đang chờ đăng nhập tại {REDIRECT_URI} (hết hạn sau {timeout}s)...")

    start_time = time.time()
    try:
        while time.time() - start_time < timeout:
            if OAuthCallbackHandler.code is not None:
                break
            if OAuthCallbackHandler.error is not None:
                raise RuntimeError(f"Lỗi xác thực từ Google: {OAuthCallbackHandler.error}")
            time.sleep(0.5)

        if OAuthCallbackHandler.code is None:
            raise TimeoutError("Quá thời gian chờ đăng nhập (timeout).")

        code = OAuthCallbackHandler.code
        token_data = exchange_code_for_token(code, verifier)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)

        email = fetch_user_email(access_token)
        project_id = discover_project(access_token)

        creds = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in,
            "email": email or "",
            "project_id": project_id or "",
        }
        save_credentials("antigravity", creds)
        return creds

    finally:
        server.shutdown()
        server.server_close()
