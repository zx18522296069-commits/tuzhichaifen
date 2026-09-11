"""Build Google Drive credentials from GitHub Actions secrets.

OAuth user credentials are preferred because a service account has no personal
Drive storage quota.  The service-account path remains as a compatibility
fallback for Shared Drive deployments.
"""

from __future__ import annotations

import json
import os

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
OAUTH_ENV_NAMES = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REFRESH_TOKEN",
)


class GoogleAuthError(RuntimeError):
    """Raised when Google credentials are absent or only partly configured."""


def auth_configured() -> bool:
    oauth_values = [os.getenv(name, "").strip() for name in OAUTH_ENV_NAMES]
    return all(oauth_values) or bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip())


def build_credentials():
    oauth = {name: os.getenv(name, "").strip() for name in OAUTH_ENV_NAMES}
    if any(oauth.values()):
        missing = [name for name, value in oauth.items() if not value]
        if missing:
            raise GoogleAuthError(f"Google OAuth 配置不完整，缺少: {', '.join(missing)}")
        return Credentials(
            token=None,
            refresh_token=oauth["GOOGLE_OAUTH_REFRESH_TOKEN"],
            token_uri=TOKEN_URI,
            client_id=oauth["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=oauth["GOOGLE_OAUTH_CLIENT_SECRET"],
            scopes=SCOPES,
        )

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise GoogleAuthError(
            "缺少 Google 凭据：请配置 GOOGLE_OAUTH_CLIENT_ID、"
            "GOOGLE_OAUTH_CLIENT_SECRET、GOOGLE_OAUTH_REFRESH_TOKEN"
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GoogleAuthError("GOOGLE_SERVICE_ACCOUNT_JSON 不是有效 JSON") from exc
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
