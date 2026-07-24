"""Eval de las superficies sumadas al frontend AI-native. exit 0 = verde.

Cubre lo que el spec de completado pidió y que ningún eval anterior tocaba:
barra superior, launcher, filtros de la bandeja, workspace de revisión v2 y su
fallback, barra fija de pagos, auditoría estructurada, indicadores, workspace de
proveedores, degradación sin IA y sin persistencia, ausencia de APIs deprecadas
y estructura responsive.

Hermético: no usa red, no arranca servidores y no toca el maestro provisionado
de la instalación.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Igual que en test_trial_session: el eval verifica el producto, no el archivo
# que tenga esta máquina en data/sage/.
os.environ["AP_VENDOR_MASTER_PATH"] = str(ROOT / "evals" / "_sin_maestro.xlsx")

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


@dataclass
class FakeResult:
    doc_id: str
    document: dict
    engine: str = "fallback_local"
    confidence: Decimal = Decimal("0.9")
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)
    pages: int = 1
    text_chars: int = 100


def _doc(doc_id: str, *, importe: str = "1000.00", vence: str | None = None,
         proveedor: str = "Proveedor Uno SL", numero: str = "F-1",
         moneda: str = "EUR") -> FakeResult:
    return FakeResult(doc_id, {
        "document_type": "invoice",
        "proveedor_nombre_comercial": proveedor,
        "proveedor_tax_id": "B12345678",
        "numero_factura": numero,
        "fecha_emision": "2026-07-01",
        "fecha_vencimiento_calculada": vence or "2026-08-15",
        "moneda": moneda,
        "importe_total": importe,
    })


def _session_with(results):
    from ap_control_tower.ui.trial import session as sess

    active = sess.new_session()
    for result in results:
        sess.add_document(active, result, file_hash=f"hash-{result.doc_id}")
    return active


# ------------------------------------------------------------------ barra superior
def test_topbar() -> None:
    from ap_control_tower.ui import topbar

    print("== Barra superior ==")
    check(set(topbar.PAGE_GROUP) >= {
        "Inicio", "Documentos", "Revisión", "Pagos", "Proveedores",
        "Indicadores", "Auditoría", "Ingreso de documentos"},
        "cada página declara su grupo para el breadcrumb")
    check(topbar.PRIMARY_ACTION["Revisión"][2] == "app_pages/propuesta_pago.py",
          "la acción primaria de Revisión lleva al gate de pago")
    check(topbar.PRIMARY_ACTION["Pagos"][2] == "app_pages/revision_humana.py",
          "la acción primaria de Pagos vuelve a la revisión")
    check(all(len(value) == 3 for value in topbar.PRIMARY_ACTION.values()),
          "toda acción primaria declara etiqueta, icono y destino")


# ------------------------------------------------------------------ launcher
def test_launcher() -> None:
    from ap_control_tower.ui import launcher

    print("== Launcher global ==")
    active = _session_with([
        _doc("DOC-1", proveedor="Acme Servicios SL", numero="A-77"),
        _doc("DOC-2", proveedor="Beta Consulting SA", numero="B-12"),
    ])
    index = launcher.search_index(active)
    check(any(item["tipo"] == "documento" for item in index)
          and any(item["tipo"] == "proveedor" for item in index),
          "el índice ofrece documentos y proveedores")
    check(len(launcher.filter_index(index, "acme")) >= 1,
          "la búsqueda encuentra por proveedor")
    check(len(launcher.filter_index(index, "B-12")) >= 1,
          "la búsqueda encuentra por número de factura")
    check(launcher.filter_index(index, "") == [],
          "sin texto no se devuelven resultados")
    check(launcher.filter_index(index, "no-existe-esto") == [],
          "una búsqueda sin coincidencias no inventa resultados")
    blob = " ".join(str(item) for item in index)
    check("B12345678" not in blob,
          "el índice del launcher no expone identificadores fiscales")
    source = (ROOT / "ap_control_tower" / "ui" / "launcher.py").read_text(encoding="utf-8")
    check("setTriggerValue" in source and "addEventListener" in source,
          "el atajo global se implementa con un evento real, no simulado")
    check("components.v1" not in source, "el launcher no usa la API de componentes v1")


# ------------------------------------------------------------------ bandeja
def test_document_filters() -> None:
    from ap_control_tower.ui import pilot_pages_documents as docs
    from ap_control_tower.ui.pilot_pages_common import safe_document_rows

    print("== Filtros de la bandeja ==")
    hoy = date.today()
    active = _session_with([
        _doc("DOC-1", importe="100.00", vence=str(hoy + timedelta(days=3)),
             proveedor="Acme SL"),
        _doc("DOC-2", importe="9000.00", vence=str(hoy + timedelta(days=40)),
             proveedor="Beta SA"),
        _doc("DOC-3", importe="500.00", vence=str(hoy - timedelta(days=5)),
             proveedor="Gamma SRL", moneda="USD"),
    ])
    rows = safe_document_rows(active)
    check(len(rows) == 3, "la bandeja lista los documentos de la sesión")

    check(len(docs._filter_rows(rows, amount_min=400)) == 2,
          "el filtro de importe mínimo acota el conjunto")
    check(len(docs._filter_rows(rows, amount_max=400)) == 1,
          "el filtro de importe máximo acota el conjunto")
    check(len(docs._filter_rows(rows, amount_min=200, amount_max=1000)) == 1,
          "el intervalo de importe combina ambos extremos")
    check(len(docs._filter_rows(rows, due_from=hoy)) == 2,
          "el filtro de vencimiento desde excluye lo vencido")
    check(len(docs._filter_rows(rows, due_to=hoy)) == 1,
          "el filtro de vencimiento hasta deja sólo lo ya vencido")
    check(len(docs._filter_rows(rows, currencies=["USD"])) == 1,
          "el filtro de moneda sigue funcionando")
    check(len(docs._filter_rows(rows, query="acme")) == 1,
          "la búsqueda libre encuentra por proveedor")
    check(len(docs._filter_rows(rows)) == 3,
          "sin criterios no se filtra nada")
    check(len(docs._filter_rows(rows, priorities=["Normal"]))
          == sum(1 for row in rows if row["Prioridad"] == "Normal"),
          "el filtro de prioridad coincide con la prioridad calculada")

    check(set(docs.PRIORITY_MARK) == set(docs.PRIORITIES),
          "toda prioridad tiene una marca de forma, no sólo de color")
    check(len(set(docs.PRIORITY_MARK.values())) == len(docs.PRIORITIES),
          "las marcas de prioridad son distinguibles entre sí")
    check("_docs_quick_view" in docs.FILTER_KEYS,
          "limpiar filtros también restablece la vista rápida")
    check(all(key.startswith("_docs_") for key in docs.FILTER_KEYS),
          "las claves de filtro comparten prefijo y se limpian juntas")


# ------------------------------------------------------------------ revisión v2
def test_review_workspace() -> None:
    from ap_control_tower.ui import review_layout, review_workspace

    print("== Workspace de revisión v2 y fallback ==")
    layout = dict(review_layout.DEFAULT_LAYOUT)
    check(len(review_layout.column_ratios(layout)) == 3,
          "con las tres zonas visibles hay tres columnas")
    colapsada = review_layout.toggle(layout, "toggle_queue")
    check(colapsada["queue_collapsed"] is True,
          "la cola se puede colapsar")
    check(len(review_layout.column_ratios(colapsada)) == 2,
          "al colapsar la cola quedan dos columnas")
    ambas = review_layout.toggle(colapsada, "toggle_copilot")
    check(len(review_layout.column_ratios(ambas)) == 1,
          "con cola y copiloto colapsados queda una sola zona")
    # Regresión concreta: con proporciones que suman el ancho completo, el
    # contenedor de columnas de Streamlit desborda por el gap y envuelve, y la
    # cola salta a su propia fila rompiendo el layout de tres zonas.
    for variante in (layout, colapsada, ambas):
        check(abs(sum(review_layout.column_ratios(variante)) - 1.0) < 1e-9,
              "el reparto de anchos son fracciones que suman 1, no porcentajes")
    check(all(0 < value < 1 for value in review_layout.column_ratios(layout)),
          "ninguna zona pide más ancho del disponible")
    check("1 0%" in (ROOT / "ap_control_tower" / "ui" / "review_layout.py")
          .read_text(encoding="utf-8"),
          "el arrastre reparte por proporción, no por base porcentual")
    check(review_layout.toggle(layout, "next") == layout,
          "una acción de navegación no altera la disposición")
    # Regresión: colapsar escribia el layout dentro de STATE_KEY, que es la
    # clave del propio componente. Tocar el estado de un widget ya instanciado
    # levanta excepcion y tumbaba la pagina entera al pulsar Cola o Copiloto;
    # el deslizador no pasaba por ese camino y por eso nunca fallaba.
    fuente = (ROOT / "ap_control_tower" / "ui" / "review_workspace.py").read_text(
        encoding="utf-8")
    check("STATE_KEY" not in fuente,
          "el workspace no escribe en la clave del componente")
    check("apply_action" in fuente,
          "el colapso pasa por el estado propio de Python")
    check(review_layout.COLLAPSE_KEY != review_layout.STATE_KEY,
          "el estado de colapso vive en una clave distinta a la del widget")

    source = (ROOT / "ap_control_tower" / "ui" / "review_layout.py").read_text(encoding="utf-8")
    check("st.components.v2" in source or "ccv2.component" in source,
          "el workspace usa la API de componentes v2")
    check("components.v1" not in source and "setComponentValue" not in source,
          "el componente no arrastra patrones de la API v1")
    check("setTriggerValue" in source and "setStateValue" in source,
          "el componente emite eventos y estado hacia Python")
    check("keydown" in source, "el componente registra navegación por teclado")
    check(":focus-visible" in source, "el componente marca el foco de teclado")
    check(len(review_layout.SHORTCUTS) >= 5 and all(
        len(item) == 2 for item in review_layout.SHORTCUTS),
        "los atajos están documentados con combinación y efecto")

    workspace = (ROOT / "ap_control_tower" / "ui" / "review_workspace.py").read_text(
        encoding="utf-8")
    check("max-width: 900px" in workspace,
          "hay layout de una zona por vez en pantallas angostas")
    check("ap_review_actions" in workspace and "position: sticky" in workspace,
          "la barra de acciones queda fija")
    check("coordenada" in workspace.casefold(),
          "la ausencia de vinculación campo-a-PDF queda documentada en el código")
    for regla in ("sess.confirm_review", "confirm_retention", "confirm_exception"):
        check(regla in workspace,
              f"la decisión sigue pasando por Python ({regla})")
    check("importe" not in source.casefold() and "iban" not in source.casefold(),
          "el componente JS no contiene reglas financieras")


def test_review_queue_order() -> None:
    from ap_control_tower.ui import review_workspace

    print("== Cola de revisión ==")
    active = _session_with([_doc("DOC-1"), _doc("DOC-2", numero="F-2")])
    for result in active.results:
        result.document["numero_factura"] = None   # fuerza motivo de revisión
    ordered = review_workspace.ordered_queue(active)
    check(len(ordered) == 2, "la cola incluye los documentos derivados")
    rank = {"Crítica": 0, "Alta": 1, "Media": 2, "Normal": 3}
    valores = [rank.get(row["Prioridad"], 9) for row in ordered]
    check(valores == sorted(valores),
          "la cola se ordena por consecuencia económica")
    # Regresión: al confirmar o retener, la tarjeta seguía en la cola y el
    # contador de la barra lateral (que cuenta pendientes) quedaba en desacuerdo
    # con la lista. Lo resuelto se consulta en Documentos y en Auditoría.
    active.review_decisions[active.results[0].doc_id] = {
        "status": "confirmed", "actor": "iv", "timestamp": "2026-07-24T00:00:00Z",
    }
    quedan = review_workspace.ordered_queue(active)
    check(len(quedan) == 1,
          "un documento confirmado sale de la cola de revisión")
    check(all(row["item"]["pending"] for row in quedan),
          "en la cola sólo queda lo que espera decisión")
    active.review_decisions[active.results[1].doc_id] = {
        "status": "retained", "actor": "iv", "timestamp": "2026-07-24T00:00:00Z",
    }
    check(review_workspace.ordered_queue(active) == [],
          "retener también saca el documento de la cola")


# ------------------------------------------------------------------ pagos
def test_payment_bar() -> None:
    from ap_control_tower.ui import pilot_pages_workflow as flow

    print("== Barra fija de pagos ==")
    rows = []
    for index, importe in enumerate(["100.00", "200.00", "300.00", "50000.00",
                                     "150.00", "250.00"], start=1):
        result = _doc(f"DOC-{index}", importe=importe)
        rows.append({"result": result, "status": "eligible", "reasons": [],
                     "decision": {}})
    altos = flow.high_amount_ids(rows)
    check(len(altos) >= 1 and "DOC-4" in altos,
          "el importe atípico del lote queda marcado como alto importe")
    check(flow.high_amount_ids(rows[:2]) == set(),
          "con un lote demasiado chico no se inventa un umbral")

    riesgoso = {"result": _doc("DOC-9"), "status": "eligible",
                "reasons": ["La cuenta de cobro no coincide con la registrada",
                            "posible duplicado"],
                "decision": {}}
    banderas = flow.payment_risk_list(riesgoso)
    check("Cambio bancario" in banderas and "Duplicado" in banderas,
          "las señales de riesgo del lote se enuncian sin datos crudos")
    check(not any(char.isdigit() for char in " ".join(banderas)),
          "las señales no arrastran importes ni números de cuenta")

    hoy = date.today()
    proximos = flow.upcoming_due(
        [{"result": _doc("DOC-A", vence=str(hoy + timedelta(days=2))),
          "status": "eligible", "reasons": [], "decision": {}},
         {"result": _doc("DOC-B", vence=str(hoy - timedelta(days=1))),
          "status": "eligible", "reasons": [], "decision": {}},
         {"result": _doc("DOC-C", vence=str(hoy + timedelta(days=90))),
          "status": "eligible", "reasons": [], "decision": {}}],
        today=hoy)
    check([item["doc_id"] for item in proximos] == ["DOC-B", "DOC-A"],
          "los próximos vencimientos se ordenan por fecha y excluyen lo lejano")
    check(proximos[0]["Vencido"] is True,
          "lo ya vencido se distingue de lo por vencer")

    source = (ROOT / "ap_control_tower" / "ui" / "pilot_pages_workflow.py").read_text(
        encoding="utf-8")
    check("st.bottom" in source, "la barra de decisiones queda fija al pie")
    for requisito in ("acknowledgement", "_confirm_payment_decision",
                      "Ingresá el motivo de la exclusión o el rechazo"):
        check(requisito in source,
              f"se conserva el requisito previo a decidir ({requisito})")


# ------------------------------------------------------------------ auditoría
def test_audit_origin() -> None:
    from ap_control_tower.ui import pilot_pages_reporting as rep

    print("== Auditoría: origen estructurado y evidencia segura ==")

    class Event:
        def __init__(self, **kwargs):
            self.__dict__.update({
                "seq": 1, "ts": "2026-07-01T10:00:00", "agent": "Ana",
                "action": "revision-humana-confirmada", "invoice_id": "DOC-1",
                "control_id": None, "result": "confirmed", "evidence": {},
                **kwargs,
            })

    check(rep.event_origin(Event(control_id="C10_PADRON")) == "Control",
          "un evento con control identificado es de origen Control")
    check(rep.event_origin(Event(agent="sistema", action="ingesta")) == "Sistema",
          "un evento del sistema es de origen Sistema")
    check(rep.event_origin(Event(action="consulta-asistente-ap")) == "IA",
          "una respuesta del asistente es de origen IA")
    check(rep.event_origin(Event()) == "Humano",
          "una decisión de una persona es de origen Humano")
    # La regresión concreta: antes el texto del resultado ganaba sobre el dato.
    check(rep.event_origin(Event(result="error-de-validacion")) == "Humano",
          "el texto del resultado NO reclasifica el origen de un evento humano")
    check(rep.event_tone(Event(result="error-de-validacion")) == "risk",
          "la severidad sí se deriva del resultado")

    evidencia = rep.safe_evidence({
        "campos_corregidos": ["numero_factura"],
        "motivo_informado": True,
        "proveedor_iban": "ES9121000418450200051332",
        "texto_documento": "x" * 200,
    })
    check("numero_factura" in evidencia and "sí" in evidencia,
          "la evidencia estructurada se resume de forma legible")
    check("ES9121000418450200051332" not in evidencia,
          "la evidencia nunca publica una cuenta bancaria")
    check("xxxx" not in evidencia,
          "la evidencia nunca publica contenido del documento")
    check(rep.safe_evidence(None) == "—", "sin evidencia se informa un guion")


# ------------------------------------------------------------------ indicadores
def test_indicators() -> None:
    from ap_control_tower.ui import indicators
    from ap_control_tower.ui.command_center import (
        format_hours,
        median_cycle_hours,
        recent_activity,
    )

    print("== Indicadores ==")
    hoy = date.today()
    active = _session_with([
        _doc("DOC-1", vence=str(hoy + timedelta(days=3))),
        _doc("DOC-2", vence=str(hoy - timedelta(days=2)), importe="4000.00"),
    ])
    estados = indicators.state_distribution(active)
    check(sum(estados.values()) == 2, "la distribución por estado cubre todo el lote")
    vencimientos = indicators.due_distribution(active, today=hoy)
    check(vencimientos["Vencido"] == 1 and vencimientos["0–7 días"] == 1,
          "los horizontes de vencimiento clasifican correctamente")
    aging = indicators.aging_distribution(active, today=date(2026, 7, 15))
    check(sum(aging.values()) == 2, "la antigüedad cubre todo el lote")

    check(indicators.touchless_rate(active) is not None,
          "la tasa touchless es calculable con documentos en la sesión")
    check(0.0 <= indicators.touchless_rate(active) <= 1.0,
          "la tasa touchless es una proporción")
    check(indicators.human_review_rate(active) is not None,
          "la tasa de revisión humana es calculable")
    vacia = _session_with([])
    check(indicators.touchless_rate(vacia) is None,
          "sin documentos la tasa touchless no se inventa")
    check(indicators.percent(None) == "—",
          "un indicador no calculable se muestra como guion")

    check(median_cycle_hours(active) is None,
          "sin decisiones todavía no hay tiempo de ciclo")
    check(format_hours(None) == "—", "el tiempo de ciclo no calculable es un guion")
    check(format_hours(0.5) == "30 min" and format_hours(72) == "3.0 d",
          "el tiempo de ciclo se expresa en la unidad legible")

    retenido = indicators.retained_amounts(active)
    check(all(isinstance(value, Decimal) for value in retenido.values()),
          "el importe retenido se calcula en decimal, sin coma flotante")
    check(len(recent_activity(active)) >= 1,
          "la actividad reciente informa eventos de la sesión")


def test_cycle_time_with_decisions() -> None:
    from ap_control_tower.ui.command_center import cycle_hours
    from ap_control_tower.ui.trial import session as sess

    print("== Tiempo de ciclo sobre auditoría real ==")
    active = _session_with([_doc("DOC-1")])
    try:
        sess.confirm_review(active, "DOC-1", "Ana Revisora",
                            {"numero_factura": "F-1"}, "verificada")
    except ValueError as exc:  # pragma: no cover - diagnóstico
        check(False, f"la confirmación debería proceder ({exc})")
        return
    horas = cycle_hours(active)
    check(len(horas) == 1, "un documento decidido aporta un tiempo de ciclo")
    check(horas[0] >= 0, "el tiempo de ciclo nunca es negativo")


# ------------------------------------------------------------------ proveedores
def test_vendor_workspace() -> None:
    from ap_control_tower.ui import vendor_intake

    print("== Workspace de proveedores ==")
    active = _session_with([
        _doc("DOC-1", proveedor="Acme SL", numero="A-1"),
        _doc("DOC-2", proveedor="Acme SL", numero="A-2", importe="2500.00"),
        _doc("DOC-3", proveedor="Beta SA", numero="B-1"),
    ])
    active.results[2].document["proveedor_tax_id"] = "B87654321"
    entidades = vendor_intake.vendor_entities(active)
    check(len(entidades) == 2, "los documentos se agrupan por proveedor")
    acme = next(item for item in entidades if item["nombre"] == "Acme SL")
    check(len(acme["documentos"]) == 2, "la ficha lista los documentos asociados")
    check(acme["por_moneda"]["EUR"] == Decimal("3500.00"),
          "la exposición por moneda suma los importes del proveedor")
    blob = " ".join(str(item) for item in entidades)
    check("B12345678" not in blob and "B87654321" not in blob,
          "la ficha nunca expone el identificador fiscal completo")
    check(all(item["tax_id"] != "" for item in entidades),
          "la ficha muestra el identificador enmascarado, no vacío")

    source = (ROOT / "ap_control_tower" / "ui" / "vendor_intake.py").read_text(
        encoding="utf-8")
    check("mask_iban" in source and "mask_tax_id" in source,
          "los datos bancarios y fiscales se enmascaran")
    check("Posible desvío de pago" in source,
          "el cambio de cuenta bancaria se enuncia como riesgo de pago")


# ------------------------------------------------------------------ degradación
def test_degraded_modes() -> None:
    from ap_control_tower.ui import agent_panel

    print("== Degradación sin IA y sin persistencia ==")
    active = _session_with([_doc("DOC-1")])
    briefing = agent_panel.deterministic_briefing(active, active.results[0])
    check(set(briefing) == {"riesgos", "evidencia", "proximo"},
          "el resumen determinista trae riesgos, evidencia y próximo paso")
    check(briefing["evidencia"], "la evidencia se calcula sin llamar al modelo")
    check(isinstance(briefing["riesgos"], list),
          "los riesgos son los motivos del sistema, no texto del modelo")

    previa = os.environ.pop("OPENAI_API_KEY", None)
    try:
        from ap_control_tower.agent.config import AgentSettings

        settings = AgentSettings.from_env()
        check(settings.availability_message() is not None,
              "sin clave configurada el copiloto se declara no disponible")
        # El resumen determinista sigue disponible con la IA caída.
        check(agent_panel.deterministic_briefing(active, active.results[0])["proximo"],
              "el próximo paso se calcula igual con el copiloto apagado")
    finally:
        if previa is not None:
            os.environ["OPENAI_API_KEY"] = previa

    from ap_control_tower.ui.trial import session as sess

    check(isinstance(sess.persistence_available(), bool),
          "la disponibilidad de historial se consulta sin levantar excepción")
    check(sess.persist(active) in (True, False),
          "persistir sin base configurada devuelve un resultado, no un error")
    check(active.audit.verify_chain(),
          "la cadena de auditoría permanece íntegra tras la degradación")


# ------------------------------------------------------------------ responsive
def test_responsive_and_css() -> None:
    import re

    print("== Estructura responsive y CSS estable ==")
    modulos = [
        ROOT / "ap_control_tower" / "ui" / "design.py",
        ROOT / "ap_control_tower" / "ui" / "topbar.py",
        ROOT / "ap_control_tower" / "ui" / "review_workspace.py",
        ROOT / "ap_control_tower" / "ui" / "review_layout.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_shell.py",
    ]
    con_media = [path.name for path in modulos
                 if "@media" in path.read_text(encoding="utf-8")]
    check(len(con_media) >= 3,
          f"las superficies clave declaran reglas responsive ({con_media})")

    for path in modulos:
        texto = path.read_text(encoding="utf-8")
        for bloque in re.findall(r"<style>(.*?)</style>", texto, re.DOTALL):
            check("emotion-cache" not in bloque,
                  f"{path.name}: el CSS no cuelga de clases internas inestables")
        check("!important" not in texto,
              f"{path.name}: el CSS no necesita forzar la cascada")

    design_source = modulos[0].read_text(encoding="utf-8")
    check("prefers-reduced-motion" in design_source,
          "la animación de carga respeta la preferencia de movimiento reducido")
    check(":focus-visible" in design_source,
          "el foco de teclado es visible en toda la aplicación")


def test_design_components() -> None:
    from ap_control_tower.ui import design

    print("== Componentes compartidos del sistema visual ==")
    for nombre in ("skeleton", "action_bar", "entity_header", "confidence",
                   "activity_panel", "empty_state", "error_state", "alert",
                   "kpi", "timeline", "chip"):
        check(hasattr(design, nombre), f"design expone el componente {nombre}")

    check(design.confidence(None) == "",
          "sin confianza informada no se dibuja un indicador inventado")
    check("%" in design.confidence(0.42),
          "la confianza informada se muestra con su valor")
    check("B42318" in design.confidence(0.10).upper(),
          "una confianza baja se marca en el tono de riesgo")
    check(design.esc("<script>") == "&lt;script&gt;",
          "el escape neutraliza marcado en texto de documentos")
    check(design.money(Decimal("1234.5"), "EUR") == "EUR 1.234,50",
          "los importes usan formato europeo")


# ------------------------------------------------------------------ versión
def test_build_stamp() -> None:
    from ap_control_tower.ui import pilot_shell

    print("== Sello de versión en el pie del sidebar ==")
    previa = os.environ.get("GIT_COMMIT")
    try:
        os.environ["GIT_COMMIT"] = "abcdef1234567890"
        sello = pilot_shell.build_stamp()
        check("abcdef1" in sello, "el sello incluye el commit corto")
        check("abcdef1234567890" not in sello,
              "el sello no publica el commit completo")
        check(pilot_shell.APP_VERSION in sello, "el sello incluye la versión")
        os.environ.pop("GIT_COMMIT")
        check("local" in pilot_shell.build_stamp(),
              "sin GIT_COMMIT el sello indica ejecución local")
    finally:
        if previa is None:
            os.environ.pop("GIT_COMMIT", None)
        else:
            os.environ["GIT_COMMIT"] = previa


def main() -> int:
    try:
        import streamlit  # noqa: F401
    except Exception:
        print("== Completado del frontend: SALTEADO (Streamlit no instalado) ==")
        return 0

    test_topbar()
    test_launcher()
    test_document_filters()
    test_review_workspace()
    test_review_queue_order()
    test_payment_bar()
    test_audit_origin()
    test_indicators()
    test_cycle_time_with_decisions()
    test_vendor_workspace()
    test_degraded_modes()
    test_responsive_and_css()
    test_design_components()
    test_build_stamp()

    print()
    if failures:
        print(f"COMPLETADO DEL FRONTEND ROJO: {len(failures)} fallo(s)")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("COMPLETADO DEL FRONTEND VERDE: topbar, launcher, filtros, workspace v2, "
          "pagos, auditoría, indicadores, proveedores, degradación y responsive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
