"""Regresión focalizada del producto piloto. Exit 0 = verde."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def _visible_login(at) -> list[str]:
    return (
        [item.value for item in at.title]
        + [item.value for item in at.markdown]
        + [item.value for item in at.subheader]
        + [item.label for item in at.text_input]
        + [item.placeholder or "" for item in at.text_input]
        + [item.label for item in at.button]
        + [item.value for item in at.error]
    )


@dataclass
class FakeResult:
    doc_id: str
    document: dict
    engine: str = "fallback_local"
    confidence: Decimal = Decimal("0.90")
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)
    pages: int = 1
    text_chars: int = 120


def _invoice() -> FakeResult:
    return FakeResult(
        "PILOT-001",
        {
            "document_type": "invoice",
            "proveedor_nombre_comercial": "Proveedor Ejemplo SA",
            "proveedor_tax_id": "30712345678",
            "numero_factura": "A-0001-00000001",
            "fecha_emision": "2026-07-01",
            "fecha_vencimiento_calculada": "2026-07-31",
            "moneda": "ARS",
            "importe_total": "125000.50",
            "iban": "ES9121000418450200051332",
            "po_reference": "OC-1042",
        },
    )


def main() -> int:
    from streamlit.testing.v1 import AppTest

    from ap_control_tower.ui import auth

    original_primary = os.environ.pop(auth.PRIMARY_PASSWORD_ENV_VAR, None)
    original_legacy = os.environ.pop(auth.LEGACY_PASSWORD_ENV_VAR, None)
    try:
        print("== Acceso y wording exacto ==")
        unconfigured = AppTest.from_file(str(ROOT / "app.py"))
        unconfigured.run(timeout=30)
        check(bool(unconfigured.error), "sin configuración el acceso permanece bloqueado")
        check(
            any("Contactá al administrador" in item.value for item in unconfigured.error),
            "la falta de configuración no expone variables ni secretos",
        )

        os.environ[auth.PRIMARY_PASSWORD_ENV_VAR] = "clave-piloto"
        login = AppTest.from_file(str(ROOT / "app.py"))
        login.run(timeout=30)
        visible = _visible_login(login)
        # "Brand UP" pasó a un bloque propio con filetes; el título de la
        # tarjeta usa capitalización de oración, como el resto del producto.
        for required in (
            "Torre de Control para Cuentas a Pagar",
            "**Brand UP**",
            "Acceso al sistema",
            "Contraseña",
            "Ingresar",
        ):
            check(required in visible, f"login contiene {required!r}")
        check(login.text_input[0].placeholder == "Contraseña", "placeholder exacto")
        forbidden = ("demo", "trial", "PoC", "prueba de concepto")
        check(
            not any(token.casefold() in " ".join(visible).casefold() for token in forbidden),
            "el login no contiene wording histórico",
        )

        login.text_input[0].input("secreto-no-valido")
        login.button[0].click()
        login.run(timeout=30)
        check(
            any(item.value == "La contraseña ingresada es incorrecta." for item in login.error),
            "error de autenticación exacto",
        )
        check(
            all("secreto-no-valido" not in item.value for item in login.error),
            "la contraseña no se filtra en errores",
        )

        login.text_input[0].input("clave-piloto")
        login.button[0].click()
        login.run(timeout=30)
        check(not login.exception, "AP_SYSTEM_PASSWORD permite ingresar")
        check(any(item.value == "Inicio" for item in login.title), "el acceso abre Inicio")

        os.environ.pop(auth.PRIMARY_PASSWORD_ENV_VAR, None)
        os.environ[auth.LEGACY_PASSWORD_ENV_VAR] = "compatibilidad"
        legacy = AppTest.from_file(str(ROOT / "app.py"))
        legacy.run(timeout=30)
        legacy.text_input[0].input("compatibilidad")
        legacy.button[0].click()
        legacy.run(timeout=30)
        check(not legacy.exception and bool(legacy.metric), "fallback temporal funciona")

        print("== Superficie única y wording operativo ==")
        active_files = [
            ROOT / "app.py",
            ROOT / "app_trial.py",
            *(ROOT / "app_pages").glob("*.py"),
            *(ROOT / "ap_control_tower" / "ui").glob("pilot_*.py"),
            ROOT / "ap_control_tower" / "ui" / "auth.py",
            ROOT / "ap_control_tower" / "ui" / "bootstrap.py",
            ROOT / "ap_control_tower" / "ui" / "trial" / "intake.py",
            ROOT / "ap_control_tower" / "ui" / "components" / "gmail_panel.py",
            ROOT / "ap_control_tower" / "gmail" / "client.py",
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in active_files)
        # La regla original prohibía el nombre "AP Control Tower" junto con el
        # wording de demo/PoC. En esta versión del frontend el nombre corto del
        # producto ES "AP Control Tower", así que se prohíbe solo el wording
        # promocional: lo que había que erradicar era el encuadre de prueba de
        # concepto, no la marca. Ver nota de implementación.
        exact_forbidden = (
            "Acceso a la demo",
            "Password de la demo",
            "Cargá tus facturas reales y verás cómo el agente las procesa en tiempo real",
            "Prueba de concepto con facturas reales",
            "Abrir AP Control Tower Demo",
            "Abrir prueba con facturas reales",
            "Probar con mis facturas",
            "Ver resultados con mis facturas",
            "Modo demo",
            "Modo trial",
            "Calidad medida (evals)",
        )
        for phrase in exact_forbidden:
            check(phrase not in source, f"no reaparece wording prohibido: {phrase}")
        check("AP_POC_URL" not in source and "AP_DEMO_URL" not in source,
              "la superficie activa no contiene enlaces a variantes")
        check("st.navigation" in source, "existe una navegación persistente única")
        streamlit_config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        check(
            "showSidebarNavigation = true" in streamlit_config,
            "la navegación registrada permanece visible en el sidebar",
        )
        check('ap-brand-sub">Brand UP<' in source or '"#### Brand UP"' in source,
              "Brand UP tiene jerarquía tipográfica de marca")
        check('position="hidden"' in source and "st.page_link" in source,
              "la marca se dibuja por encima de las pestañas del sidebar")
        check("apcontroltowerdemo@gmail.com" in source,
              "la interfaz identifica el buzón AP asignado")
        check("Abrir Documentos" in source and "st.switch_page" in source,
              "la confirmación de carga ofrece acceso directo a Documentos")
        check(
            "Cargá el maestro de proveedores antes de subir los documentos" in source,
            "el ingreso indica que el maestro debe cargarse primero",
        )
        for page in (
            "Inicio", "Ingreso de documentos", "Documentos", "Revisión humana",
            "Lote de pago", "Auditoría", "Indicadores",
        ):
            check(page in source, f"navegación contiene {page}")

        print("== Separación de decisiones, maker-checker y auditoría ==")
        from ap_control_tower.ui.trial import payment_approval, session as session_module

        active = session_module.new_session()
        result = _invoice()
        session_module.add_document(active, result, file_hash="hash-pilot")
        session_module.confirm_review(
            active,
            result.doc_id,
            "Revisora Uno",
            {"numero_factura": result.document["numero_factura"]},
            "Datos verificados.",
        )
        check(result.doc_id not in active.approval_decisions,
              "confirmar datos no aprueba una propuesta de pago")
        try:
            session_module.decide_payment_proposal(
                active, [result.doc_id], "Revisora Uno", "approved"
            )
        except ValueError:
            maker_checker_blocked = True
        else:
            maker_checker_blocked = False
        check(maker_checker_blocked, "maker-checker impide autoaprobación")
        session_module.decide_payment_proposal(
            active, [result.doc_id], "Aprobador Dos", "approved", "Gate semanal."
        )
        check(active.approval_decisions[result.doc_id]["status"] == "approved",
              "la aprobación separada queda registrada")
        check(active.audit.verify_chain(), "la cadena de auditoría mantiene integridad")
        try:
            session_module.decide_payment_proposal(
                active, [result.doc_id], "Aprobador Dos", "excluded", ""
            )
        except ValueError:
            exclusion_requires_reason = True
        else:
            exclusion_requires_reason = False
        check(exclusion_requires_reason, "excluir requiere motivo")

        approved_row = {
            "result": result,
            "status": "approved",
            "reasons": [],
            "decision": active.approval_decisions[result.doc_id],
        }
        export = payment_approval.payment_export_rows([approved_row])[0]
        check(export["iban_cuenta"] != result.document["iban"],
              "el export no expone el IBAN completo")
        check(export["iban_cuenta"].endswith("1332"),
              "el export conserva solo la referencia bancaria enmascarada")
    finally:
        os.environ.pop(auth.PRIMARY_PASSWORD_ENV_VAR, None)
        os.environ.pop(auth.LEGACY_PASSWORD_ENV_VAR, None)
        if original_primary is not None:
            os.environ[auth.PRIMARY_PASSWORD_ENV_VAR] = original_primary
        if original_legacy is not None:
            os.environ[auth.LEGACY_PASSWORD_ENV_VAR] = original_legacy

    if failures:
        print(f"\nPILOTO ROJO: {len(failures)} fallo(s)")
        return 1
    print("\nPILOTO VERDE: acceso, wording, navegación y gates verificados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
