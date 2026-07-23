# Capa de aplicación / casos de uso (Fase 3)

Punto de entrada único para la interfaz (y una futura API/ERP). La UI llama a
`ap_control_tower.app` y **no** importa `engine/` ni `extraction/` directamente,
ni contiene reglas centrales de negocio. Framework-agnóstica: sin Streamlit.

```
UI (Streamlit)  →  app/ (casos de uso)  →  engine/ (reglas)  ·  extraction/  ·  persistence/
```

| Módulo | Rol |
|---|---|
| `services.py` | Orquestación: `process_month`, `build_run`, `new_month_runner`/`finalize_runner`; gate (`approve_and_release`, `reject_batch`, `close_batch`); revisión (`confirm_internal_data`, `approve_anticipo`); `assignable_thursdays`, `reopen_workflow`. |
| `extraction_service.py` | `process_uploaded_document`, `document_ai_configured` (aísla a la UI del adaptador Document AI; punto de sustitución de Fase 8). |
| `master_data_service.py` | Import y resolución del maestro Sage: Tax ID, nombre normalizado, fuzzy seguro y resultados no sensibles. |
| `__init__.py` | Superficie pública: servicios + errores (`GateViolation`, `ReviewError`) + constantes de estado de lote + utilidades de display (`classify_document`, `FIELD_ORDER`). |

**Estado de corrida** (compatibilidad con la demo): el dict
`{"result", "audit", "ctx", "workflows", "closing_reports"}`. `ui/state.py` lo
guarda en `session_state` y delega toda la lógica acá; las otras vistas no
cambiaron. Sin `AP_DATABASE_URL` todo sigue en memoria como hoy; el enchufe de
persistencia (Fase 6) no cambia esta interfaz.

Verificación headless (sin Streamlit): `python evals/test_app_services.py`.
