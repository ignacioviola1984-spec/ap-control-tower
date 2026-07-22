"""Tests hermeticos del conector Zoho: no usan red ni credenciales reales."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower.zoho import ZohoConfig, ZohoConnector, ZohoConnectorError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._raw


class FakeZoho:
    def __init__(self) -> None:
        self.requests = []
        self.token_calls = 0

    def __call__(self, request, timeout=30):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/oauth/v2/token"):
            self.token_calls += 1
            form = parse_qs(request.data.decode("ascii"))
            assert form["client_id"] == ["1000.client"]
            assert form["client_secret"] == ["secret"]
            assert form["refresh_token"] == ["1000.refresh"]
            assert form["grant_type"] == ["refresh_token"]
            return FakeResponse(
                {
                    "access_token": "1000.access",
                    "api_domain": "https://www.zohoapis.com",
                    "expires_in": 3600,
                }
            )
        assert request.headers["Authorization"] == "Zoho-oauthtoken 1000.access"
        if "/settings/modules" in url:
            return FakeResponse({"modules": [{"api_name": "Vendors"}]})
        if "/crm/v8/Vendors" in url:
            return FakeResponse({"data": [{"id": "1", "Vendor_Name": "Demo"}]})
        if "/crm/v8/Tasks" in url:
            body = json.loads(request.data.decode("utf-8"))
            assert body["data"][0]["Subject"] == "Revisar excepcion"
            return FakeResponse({"data": [{"details": {"id": "task-1"}, "status": "success"}]})
        if "/workdrive/api/v1/files/" in url:
            assert request.headers["Accept"] == "application/vnd.api+json"
            return FakeResponse({"data": [{"id": "file-1", "type": "files"}]})
        if url.endswith("/workdrive/api/v1/upload"):
            assert b'name="parent_id"' in request.data
            assert b"folder-1" in request.data
            assert b"connector-ready.txt" in request.data
            assert b"AP Control Tower" in request.data
            return FakeResponse({"data": [{"id": "upload-1", "type": "files"}]})
        raise AssertionError(f"URL inesperada: {url}")


def config() -> ZohoConfig:
    return ZohoConfig(
        client_id="1000.client",
        client_secret="secret",
        refresh_token="1000.refresh",
        workdrive_folder_id="folder-1",
    )


def test_config_is_disabled_without_complete_credentials(monkeypatch) -> None:
    for key in ("AP_ZOHO_CLIENT_ID", "AP_ZOHO_CLIENT_SECRET", "AP_ZOHO_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert ZohoConfig.from_env() is None


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AP_ZOHO_CLIENT_ID", "cid")
    monkeypatch.setenv("AP_ZOHO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AP_ZOHO_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("AP_ZOHO_WORKDRIVE_FOLDER_ID", "folder")
    loaded = ZohoConfig.from_env()
    assert loaded is not None
    assert loaded.workdrive_folder_id == "folder"
    assert loaded.accounts_url == "https://accounts.zoho.com"


def test_crm_reads_and_token_is_reused() -> None:
    fake = FakeZoho()
    connector = ZohoConnector(config(), opener=fake, clock=lambda: 10)
    assert connector.crm_list_modules()[0]["api_name"] == "Vendors"
    assert connector.crm_list_vendors()[0]["Vendor_Name"] == "Demo"
    assert fake.token_calls == 1


def test_crm_task_is_available_but_explicit() -> None:
    fake = FakeZoho()
    connector = ZohoConnector(config(), opener=fake)
    result = connector.crm_create_task(
        subject="Revisar excepcion",
        description="Documento demo",
    )
    assert result["data"][0]["status"] == "success"


def test_workdrive_list_and_upload() -> None:
    fake = FakeZoho()
    connector = ZohoConnector(config(), opener=fake)
    assert connector.workdrive_list_folder()[0]["id"] == "file-1"
    uploaded = connector.workdrive_upload(
        filename="connector-ready.txt",
        content=b"AP Control Tower",
    )
    assert uploaded["data"][0]["id"] == "upload-1"


def test_workdrive_requires_folder() -> None:
    connector = ZohoConnector(
        ZohoConfig("cid", "secret", "refresh"),
        opener=FakeZoho(),
    )
    try:
        connector.workdrive_list_folder()
    except ZohoConnectorError as exc:
        assert "AP_ZOHO_WORKDRIVE_FOLDER_ID" in str(exc)
    else:
        raise AssertionError("debio exigir folder id")
