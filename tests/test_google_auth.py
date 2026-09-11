import os
import unittest
from unittest.mock import patch

import google_auth


class GoogleAuthTests(unittest.TestCase):
    def test_oauth_is_preferred_when_complete(self):
        env = {
            "GOOGLE_OAUTH_CLIENT_ID": "client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
            "GOOGLE_OAUTH_REFRESH_TOKEN": "refresh-token",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "not-used",
        }
        with patch.dict(os.environ, env, clear=True):
            credentials = google_auth.build_credentials()
        self.assertEqual(credentials.client_id, "client-id")
        self.assertEqual(credentials.refresh_token, "refresh-token")

    def test_partial_oauth_is_rejected(self):
        with patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_ID": "client-id"}, clear=True):
            with self.assertRaisesRegex(google_auth.GoogleAuthError, "配置不完整"):
                google_auth.build_credentials()

    def test_missing_credentials_are_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(google_auth.auth_configured())
            with self.assertRaisesRegex(google_auth.GoogleAuthError, "缺少 Google 凭据"):
                google_auth.build_credentials()


if __name__ == "__main__":
    unittest.main()
