"""Smoke live acotado del conector Zoho CRM + WorkDrive."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ap_control_tower.zoho import ZohoConfig, ZohoConnector, ZohoConnectorError


def main() -> int:
    config = ZohoConfig.from_env()
    if config is None:
        print("zoho_smoke=error reason=missing_configuration")
        return 2

    connector = ZohoConnector(config)
    try:
        modules = connector.crm_list_modules()
        module_names = {
            str(module.get("api_name", "")).lower()
            for module in modules
            if isinstance(module, dict)
        }
        crm_modules_ok = "vendors" in module_names and "tasks" in module_names

        vendors = connector.crm_list_vendors(limit=1)
        crm_vendors_ok = isinstance(vendors, list)

        connector.workdrive_list_folder(limit=1)
        workdrive_read_ok = True

        uploaded = connector.workdrive_upload(
            filename="AP_Control_Tower_connector_smoke.txt",
            content=(
                b"AP Control Tower - Zoho connector smoke\n"
                b"No invoice data. No payment data.\n"
            ),
            override=True,
        )
        workdrive_upload_ok = bool(uploaded.get("data"))
    except (ZohoConnectorError, ValueError) as exc:
        print(f"zoho_smoke=error reason={exc}")
        return 3

    print(
        "zoho_smoke=ok "
        f"crm_modules={str(crm_modules_ok).lower()} "
        f"crm_vendors_read={str(crm_vendors_ok).lower()} "
        f"workdrive_read={str(workdrive_read_ok).lower()} "
        f"workdrive_upload={str(workdrive_upload_ok).lower()}"
    )
    return 0 if all(
        (crm_modules_ok, crm_vendors_ok, workdrive_read_ok, workdrive_upload_ok)
    ) else 4


if __name__ == "__main__":
    raise SystemExit(main())
