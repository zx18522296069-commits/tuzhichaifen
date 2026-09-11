#!/usr/bin/env python3
"""One-time helper that creates a Google OAuth refresh token locally."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python scripts/create_google_oauth_token.py /路径/client_secret.json")
        return 2

    client_file = Path(sys.argv[1]).expanduser()
    if not client_file.is_file():
        print(f"找不到 OAuth 客户端文件: {client_file}")
        return 2

    data = json.loads(client_file.read_text(encoding="utf-8"))
    desktop = data.get("installed") or {}
    if not desktop.get("client_id") or not desktop.get("client_secret"):
        print("该文件不是 Google“桌面应用”OAuth 客户端 JSON")
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        authorization_prompt_message="请在浏览器中登录拥有业务网盘的 Google 账号：{url}",
        success_message="授权成功，可以关闭此页面并返回终端。",
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    if not credentials.refresh_token:
        print("未获得 refresh token，请删除该应用授权后重试。")
        return 1

    print("\n请把下面三项分别保存到两个 GitHub 仓库的 Actions secrets：")
    print(f"GOOGLE_OAUTH_CLIENT_ID={desktop['client_id']}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={desktop['client_secret']}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={credentials.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
