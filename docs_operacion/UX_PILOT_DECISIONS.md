# Decisiones de UX del piloto Brand UP

Fecha de revisión: 22/07/2026
Producto: **Torre de Control para Cuentas a Pagar**

## Matriz de paridad funcional previa al cambio

| Funcionalidad existente | Archivo o módulo responsable | Estado antes del cambio | Cambio de UX propuesto | Forma de verificar que se conservó |
|---|---|---|---|---|
| Acceso protegido por contraseña | `ap_control_tower/ui/auth.py`, `ap_control_tower/ui/bootstrap.py` | Activo con `AP_DEMO_PASSWORD`; wording de demostración y label oculto | Login único con wording Brand UP, label accesible y `AP_SYSTEM_PASSWORD` con fallback temporal | Pruebas del gate, AppTest y revisión visual sin autenticar/autenticado |
| Carga múltiple de PDF | `ap_control_tower/ui/trial/intake.py`, `ap_control_tower/ui/components/extraction_view.py` | Activa, separada bajo el modo Trial | Página `Ingreso de documentos`, con estado vacío, progreso y errores recuperables | Prueba focalizada de carga y recorrido local con PDF sintético |
| Ingreso por correo AP | `ap_control_tower/ui/components/gmail_panel.py`, `ap_control_tower/gmail/client.py`, `ap_control_tower/gmail/imap_client.py` | Solo lectura y opcional | Sección `Bandeja de correo`; informar con claridad si no está configurada | Pruebas de Gmail/IMAP y estado local sin credenciales |
| Document AI y fallback local | `ap_control_tower/extraction/document_ai.py`, `ap_control_tower/app.py` | Configurable; degradación segura disponible | Mantener el motor, comunicar el fallback como estado operativo | `evals/run_evals.py`, pruebas del adaptador y arranque sin integración |
| Clasificación y validaciones documentales | `ap_control_tower/extraction/schema.py`, `ap_control_tower/ui/trial/workflow.py` | Activas | Mostrar tipo, campos críticos, advertencias y motivo de derivación en lista/detalle | Evals de extracción y pruebas del workflow |
| Controles ARCA | `ap_control_tower/controls/arca/`, `ap_control_tower/ui/trial/intake.py` | Modos off/mock/live; señales integradas a la revisión | Mostrar controles aplicados y estado sin afirmar verificación cuando está en off | `evals/test_controles_arca.py` y estado local sin configuración |
| Repetidos por hash y duplicados comerciales | `ap_control_tower/ui/trial/intake.py`, `ap_control_tower/ui/trial/session.py`, `ap_control_tower/ui/trial/workflow.py` | Activos | Explicar omisión por hash y priorizar posibles duplicados en revisión | Pruebas de sesión/workflow y carga repetida sintética |
| Resultados, detalle y exportación | `ap_control_tower/ui/components/extraction_view.py`, `ap_control_tower/ui/trial/results.py` | Activos; detalle en expanders | Página `Documentos` con búsqueda, filtros, selección de fila y detalle de negocio | AppTest, revisión visual y pruebas CSV/Excel |
| Revisión humana y edición autorizada | `ap_control_tower/ui/trial/human_review.py`, `ap_control_tower/ui/trial/session.py`, `ap_control_tower/ui/trial/workflow.py` | Activa; confirmación, retención y excepción | Cola priorizada lista→detalle, formulario precompletado y confirmaciones para retener/excepcionar | Pruebas del workflow, maker-checker y auditoría |
| Propuesta de pago | `ap_control_tower/ui/trial/payment_approval.py`, `ap_control_tower/ui/trial/session.py` | Gate humano separado; export CSV/Excel | Selección en tabla, totales por moneda y diálogo previo a aprobar, excluir o rechazar | Pruebas del gate y verificación de que confirmar datos no aprueba pago |
| Audit trail e integridad | `ap_control_tower/audit.py`, `ap_control_tower/ui/components/extraction_view.py` | Cadena hash activa y verificable | Página `Auditoría` con filtros, estado de integridad y exportación | `evals/run_evals.py`, pruebas de decisiones y alteración de cadena |
| Persistencia e historial opcionales | `ap_control_tower/persistence/`, `ap_control_tower/ui/trial/persistence_bridge.py` | PostgreSQL opcional; reanudación disponible | Reanudación desde páginas operativas y estado explícito de guardado/no guardado | Pruebas con SQLite/PostgreSQL opcional y arranque sin base |
| Indicadores operativos y de calidad | `ap_control_tower/ui/trial/business_case.py`, `ap_control_tower/ui/trial/quality.py`, `evals/quality_summary.json` | Separados con lenguaje comercial y técnico | Página `Indicadores` con métricas operativas de sesión y calidad de extracción | Pruebas de cálculo y revisión de wording prohibido |
| Invariantes financieros y maker-checker | `ap_control_tower/engine/`, `ap_control_tower/ui/trial/workflow.py`, `evals/run_evals.py` | Cubiertos por evals | Mantener motor sin cambios; adaptar solo la presentación y confirmaciones | Evals completos verdes y pruebas focalizadas |

## Arquitectura de información aplicada

- **Inicio:** estado operativo y tareas que requieren atención.
- **Ingreso de documentos:** carga manual y bandeja de correo opcional.
- **Documentos:** búsqueda, filtros, lista seleccionable y detalle.
- **Revisión humana:** cola priorizada, evidencia, edición controlada y decisiones.
- **Propuesta de pago:** elegibilidad, exclusiones, totales y gate humano separado.
- **Auditoría:** eventos, responsable, documento, integridad y exportación.
- **Indicadores:** métricas de operación y calidad de extracción ya calculadas.

No se usa una página separada para variantes del producto. `app.py` es el punto de entrada operativo y `app_trial.py` queda únicamente como shim interno de compatibilidad.

## Fuentes y decisiones aplicadas

| Fuente | Decisión aplicada | Pantallas afectadas |
|---|---|---|
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | Labels visibles, foco nativo visible, navegación por teclado, nombre textual de estados, contraste AA y confirmación de acciones de riesgo | Login y todas las páginas |
| [Nielsen Norman Group: 10 heurísticas de usabilidad](https://www.nngroup.com/articles/ten-usability-heuristics/) | Estado del sistema visible, lenguaje del área, prevención de errores, salida clara y mensajes con problema más solución | Ingreso, revisión, propuesta y estados vacíos/errores |
| [SAP Fiori: list report](https://www.sap.com/design-system/fiori-design-web/page-types/floorplans/list-report-floorplan-sap-fiori-element/) | Buscar, filtrar, ordenar y seleccionar antes de abrir el detalle; acciones próximas al objeto | Documentos, revisión y propuesta |
| [SAP Fiori: object page](https://experience.sap.com/fiori-design-web/object-page/) | Detalle agrupado por identidad, estado, controles, decisiones y auditoría | Detalle de documento |
| [GOV.UK Design System: error message](https://design-system.service.gov.uk/components/error-message/) | Conservar valores del formulario y explicar qué ocurrió y cómo corregirlo, sin códigos internos | Login, revisión y propuesta |
| Documentación local de Streamlit 1.59.1 | `st.navigation`, `st.dataframe` con selección, `st.form`, `st.dialog`, `st.container(border=True)`, Material Symbols y parámetros `width` | Arquitectura y componentes de toda la aplicación |

## Decisiones visuales

- Tema claro y neutral con un único azul principal; semánticos separados para éxito, advertencia y error.
- APIs nativas de Streamlit primero; CSS limitado a ajustes que Streamlit no expone y sin selectores internos para navegación o tablas.
- Máximo de cuatro métricas por fila y contenedores con borde para agrupaciones operativas.
- Material Symbols en navegación y acciones; los estados siempre incluyen texto.
- Brand UP se presenta como wordmark tipográfico porque no hay un asset oficial de marca versionado en el repositorio.
- Fechas visibles en `dd/mm/aaaa`; importes con separador local y moneda explícita, manteniendo `Decimal` en la lógica.

## Alcance de accesibilidad verificable

Se verifican teclado, orden de foco, labels, foco visible, contraste, zoom, nombres de columnas, estados textuales y confirmaciones. Streamlit controla parte del marcado final; por eso el objetivo es WCAG 2.2 AA en lo que la aplicación puede configurar y no una declaración de conformidad certificada.
