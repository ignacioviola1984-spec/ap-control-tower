"""Intercambia un grant code Self Client y guarda el refresh token.

Uso desde WSL:
  printf '%s' "$GRANT_CODE" | python3 scripts/zoho/exchange_grant.py

No imprime client secret, grant code, access token ni refresh token.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT = "singular-backup-501617-r6"
CLIENT_ID_SECRET = "zoho-us-oauth-client-id"
CLIENT_SECRET_SECRET = "zoho-us-oauth-client-secret"
REFRESH_TOKEN_SECRET = "zoho-us-oauth-refresh-token"
TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"


def secret(name: str) -> str:
    completed = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={name}",
            f"--project={PROJECT}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    grant_code = sys.stdin.read().strip()
    if not re.fullmatch(r"1000\.[!-~]+", grant_code):
        print("oauth_exchange=error reason=invalid_grant_code_format", file=sys.stderr)
        return 2

    form = urlencode(
        {
            "client_id": secret(CLIENT_ID_SECRET),
            "client_secret": secret(CLIENT_SECRET_SECRET),
            "grant_type": "authorization_code",
            "code": grant_code,
        }
    ).encode("ascii")
    request = Request(
        TOKEN_URL,
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
            reason = payload.get("error_description") or payload.get("error") or f"http_{exc.code}"
        except Exception:
            reason = f"http_{exc.code}"
        print(f"oauth_exchange=error reason={reason}", file=sys.stderr)
        return 3
    except (URLError, OSError) as exc:
        print(f"oauth_exchange=error reason=network type={type(exc).__name__}", file=sys.stderr)
        return 4

    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        reason = payload.get("error_description") or payload.get("error") or "missing_refresh_token"
        print(f"oauth_exchange=error reason={reason}", file=sys.stderr)
        return 5
    if not isinstance(access_token, str) or not access_token:
        print("oauth_exchange=error reason=missing_access_token", file=sys.stderr)
        return 6

    subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "add",
            REFRESH_TOKEN_SECRET,
            f"--project={PROJECT}",
            "--data-file=-",
        ],
        input=refresh_token.encode("ascii"),
        check=True,
    )
    api_domain = payload.get("api_domain") or "unknown"
    expires_in = payload.get("expires_in") or "unknown"
    print(
        f"oauth_exchange=ok refresh_token_stored=true "
        f"api_domain={api_domain} access_expires_in={expires_in}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
