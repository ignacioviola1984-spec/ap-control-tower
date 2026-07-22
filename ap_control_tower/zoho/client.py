"""Cliente OAuth para Zoho CRM y WorkDrive usando solo la libreria estandar."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

WORKDRIVE_SCOPES = (
    "WorkDrive.files.READ",
    "WorkDrive.files.CREATE",
)
CRM_SCOPES = (
    "ZohoCRM.modules.vendors.READ",
    "ZohoCRM.modules.tasks.CREATE",
    "ZohoCRM.settings.modules.READ",
)
OAUTH_SCOPES = WORKDRIVE_SCOPES + CRM_SCOPES

ENV_CLIENT_ID = "AP_ZOHO_CLIENT_ID"
ENV_CLIENT_SECRET = "AP_ZOHO_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "AP_ZOHO_REFRESH_TOKEN"
ENV_ACCOUNTS_URL = "AP_ZOHO_ACCOUNTS_URL"
ENV_API_DOMAIN = "AP_ZOHO_API_DOMAIN"
ENV_WORKDRIVE_FOLDER_ID = "AP_ZOHO_WORKDRIVE_FOLDER_ID"

DEFAULT_ACCOUNTS_URL = "https://accounts.zoho.com"
DEFAULT_API_DOMAIN = "https://www.zohoapis.com"


class ZohoConnectorError(RuntimeError):
    """Error seguro del conector: nunca incluye tokens ni credenciales."""


@dataclass(frozen=True)
class ZohoConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    accounts_url: str = DEFAULT_ACCOUNTS_URL
    api_domain: str = DEFAULT_API_DOMAIN
    workdrive_folder_id: str | None = None

    @classmethod
    def from_env(cls) -> "ZohoConfig | None":
        client_id = os.environ.get(ENV_CLIENT_ID)
        client_secret = os.environ.get(ENV_CLIENT_SECRET)
        refresh_token = os.environ.get(ENV_REFRESH_TOKEN)
        if not (client_id and client_secret and refresh_token):
            return None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            accounts_url=os.environ.get(ENV_ACCOUNTS_URL, DEFAULT_ACCOUNTS_URL).rstrip("/"),
            api_domain=os.environ.get(ENV_API_DOMAIN, DEFAULT_API_DOMAIN).rstrip("/"),
            workdrive_folder_id=os.environ.get(ENV_WORKDRIVE_FOLDER_ID) or None,
        )


def zoho_configured() -> bool:
    return ZohoConfig.from_env() is not None


def build_connector() -> "ZohoConnector | None":
    config = ZohoConfig.from_env()
    return ZohoConnector(config) if config is not None else None


class ZohoConnector:
    """Cliente pequeno con cache del access token y operaciones acotadas."""

    def __init__(
        self,
        config: ZohoConfig,
        *,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 30.0,
    ) -> None:
        self._config = config
        self._opener = opener
        self._clock = clock
        self._timeout = timeout
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._api_domain = config.api_domain

    @property
    def api_domain(self) -> str:
        return self._api_domain

    def _read_json(self, request: Request) -> dict[str, Any]:
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                raw = response.read()
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                detail = parsed.get("message") or parsed.get("error_description") or parsed.get("error")
            except Exception:
                detail = None
            suffix = f": {detail}" if detail else ""
            raise ZohoConnectorError(f"Zoho respondio HTTP {exc.code}{suffix}") from None
        except URLError as exc:
            reason = getattr(exc, "reason", "error de red")
            raise ZohoConnectorError(f"No se pudo conectar con Zoho: {reason}") from None
        except OSError as exc:
            raise ZohoConnectorError(f"No se pudo conectar con Zoho: {exc}") from None

        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ZohoConnectorError("Zoho devolvio una respuesta no JSON") from None
        if not isinstance(payload, dict):
            raise ZohoConnectorError("Zoho devolvio una respuesta JSON inesperada")
        return payload

    def _refresh_access_token(self) -> str:
        form = urlencode(
            {
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "refresh_token": self._config.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("ascii")
        request = Request(
            f"{self._config.accounts_url}/oauth/v2/token",
            data=form,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        payload = self._read_json(request)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            error = payload.get("error_description") or payload.get("error") or "sin access_token"
            raise ZohoConnectorError(f"Zoho no pudo renovar OAuth: {error}")
        api_domain = payload.get("api_domain")
        if isinstance(api_domain, str) and api_domain.startswith("https://"):
            self._api_domain = api_domain.rstrip("/")
        expires_in = payload.get("expires_in", 3600)
        try:
            ttl = max(60, int(expires_in))
        except (TypeError, ValueError):
            ttl = 3600
        self._access_token = token
        self._access_token_expires_at = self._clock() + ttl - 30
        return token

    def _token(self) -> str:
        if self._access_token and self._clock() < self._access_token_expires_at:
            return self._access_token
        return self._refresh_access_token()

    def _api_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> dict[str, Any]:
        url = f"{self._api_domain}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Zoho-oauthtoken {self._token()}",
            "Accept": accept,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self._read_json(Request(url, data=body, method=method, headers=headers))

    def crm_list_modules(self) -> list[dict[str, Any]]:
        payload = self._api_json(
            "GET",
            "/crm/v8/settings/modules",
            query={"status": "visible"},
        )
        modules = payload.get("modules", [])
        return modules if isinstance(modules, list) else []

    def crm_list_vendors(self, *, limit: int = 1) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 200)
        payload = self._api_json(
            "GET",
            "/crm/v8/Vendors",
            query={"fields": "Vendor_Name", "page": 1, "per_page": safe_limit},
        )
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    def crm_create_task(
        self,
        *,
        subject: str,
        description: str,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "Subject": subject,
            "Description": description,
            "Status": "Not Started",
        }
        if due_date:
            record["Due_Date"] = due_date
        return self._api_json("POST", "/crm/v8/Tasks", payload={"data": [record]})

    def workdrive_list_folder(
        self,
        folder_id: str | None = None,
        *,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        parent_id = folder_id or self._config.workdrive_folder_id
        if not parent_id:
            raise ZohoConnectorError("Falta AP_ZOHO_WORKDRIVE_FOLDER_ID")
        safe_limit = min(max(int(limit), 1), 50)
        payload = self._api_json(
            "GET",
            f"/workdrive/api/v1/files/{quote(parent_id, safe='')}/files",
            query={"page[limit]": safe_limit, "page[offset]": 0},
            accept="application/vnd.api+json",
        )
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    @staticmethod
    def _multipart_upload(
        *,
        parent_id: str,
        filename: str,
        content: bytes,
        override: bool,
    ) -> tuple[bytes, str]:
        boundary = f"----APControlTower{uuid.uuid4().hex}"
        parts: list[bytes] = []

        def add_text(name: str, value: str) -> None:
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )

        add_text("filename", filename)
        add_text("parent_id", parent_id)
        add_text("override-name-exist", "true" if override else "false")
        parts.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    'Content-Disposition: form-data; name="content"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                b"Content-Type: application/octet-stream\r\n\r\n",
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        return b"".join(parts), boundary

    def workdrive_upload(
        self,
        *,
        filename: str,
        content: bytes,
        folder_id: str | None = None,
        override: bool = False,
    ) -> dict[str, Any]:
        parent_id = folder_id or self._config.workdrive_folder_id
        if not parent_id:
            raise ZohoConnectorError("Falta AP_ZOHO_WORKDRIVE_FOLDER_ID")
        if not filename or not content:
            raise ValueError("filename y content son obligatorios")
        body, boundary = self._multipart_upload(
            parent_id=parent_id,
            filename=filename,
            content=content,
            override=override,
        )
        request = Request(
            f"{self._api_domain}/workdrive/api/v1/upload",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Zoho-oauthtoken {self._token()}",
                "Accept": "application/vnd.api+json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        return self._read_json(request)
