# Estado de preparación del piloto

Fecha de corte: 22/07/2026
Producto: **Torre de Control para Cuentas a Pagar**
Cliente: **Brand UP**

## Dictamen

La aplicación completó la **revisión funcional y visual local**. El 22/07/2026
se recibió aprobación explícita para commit, push y publicación controlada en
Cloud Run, conservando la identidad, los secretos y el escalado del servicio
existente.

## Alcance disponible

- Acceso único con el nombre del producto, Brand UP, `Acceso al Sistema` y
  `Contraseña`.
- Navegación persistente: Inicio, Ingreso de documentos, Documentos, Revisión
  humana, Propuesta de pago, Auditoría e Indicadores.
- Carga múltiple de PDF e ingreso opcional desde una bandeja de correo de solo
  lectura.
- Lista de documentos con búsqueda, filtros, selección y detalle localizado.
- Revisión humana, retención y excepción con responsable, motivo y auditoría.
- Propuesta de pago separada de la revisión documental, con maker-checker,
  confirmaciones y exportaciones CSV/Excel con datos bancarios enmascarados.
- Auditoría cronológica con verificación de integridad e indicadores operativos
  y de calidad.
- Persistencia opcional; sin base configurada, la sesión funciona solo en memoria.

## Google Document AI

El proyecto **incluye Google Document AI Invoice Parser** mediante el adaptador
`ap_control_tower/extraction/document_ai.py`. La respuesta del parser se mapea al
esquema interno y, ante falta de configuración o indisponibilidad, la aplicación
usa el motor local controlado y deriva a revisión cuando corresponde.

La vista previa actual usa datos sintéticos y no prueba credenciales reales.
Antes de desplegar se debe validar en el entorno de destino:

1. proyecto, ubicación e identificador del processor;
2. identidad de servicio y rol `roles/documentai.apiUser`;
3. procesamiento de un conjunto autorizado y no sensible de verificación;
4. degradación segura y mensajes operativos cuando el servicio no responde.

## Evidencia de verificación

| Verificación | Resultado |
|---|---|
| `evals/run_evals.py` | Verde: 20 grupos, motor, gates, confidencialidad, arranque, Document AI y Sage |
| `evals/test_app_modes.py` | Verde: wording, siete páginas, recorrido sintético y ambos entrypoints HTTP |
| `evals/test_pilot_ui.py` | Verde: login, maker-checker, auditoría y enmascaramiento bancario |
| `evals/test_sage_vendor_master.py` | Verde: import seguro, Tax ID, normalización, fuzzy, ambigüedad y FYI auditada |
| Sesión y workflow | Verdes: idempotencia, decisiones, exportación y no liberación automática |
| Gmail e IMAP | Verdes con clientes locales/falsos; interfaces de solo lectura |
| Controles ARCA | Verdes en modos off/mock y pruebas unitarias de integración |
| Revisión visual | Sin errores de consola ni desborde horizontal en la vista de escritorio validada |

## Limitaciones que requieren decisión antes de producción

- El acceso por contraseña compartida es apropiado para esta revisión, pero no
  reemplaza SSO, usuarios individuales, roles ni segregación de funciones.
- La vista previa se ejecuta en memoria. La persistencia productiva y su política
  de retención deben acordarse y validarse en el entorno del cliente.
- Las credenciales reales de Document AI y correo no forman parte del repositorio
  ni de esta prueba local.
- El archivo `output sage.xlsx` recibido corresponde a clientes, no a proveedores,
  y es rechazado de forma segura. Falta el export correcto de proveedores para
  validar la vinculación con datos reales.
- El wordmark Brand UP es tipográfico. Falta incorporar el activo oficial si el
  cliente entrega logo y lineamientos de marca.
- El equipo de Cuentas a Pagar debe validar textos, prioridades, responsables,
  campos editables y contenido de las exportaciones.

## Vista previa local

La revisión actual está disponible en `http://127.0.0.1:8501/` con la contraseña
temporal `revision-local`. Usa `AP_PREVIEW_MODE=1`, que carga exclusivamente
fixtures sintéticos. Esta configuración es solo para revisión local y no debe
usarse en un despliegue.

## Gate de despliegue

Aprobación explícita recibida el 22/07/2026. Antes de publicar se debe repetir
la batería completa, validar Document AI con la identidad de destino y confirmar
que el contexto de build no contiene facturas, ground truth ni secretos. El
rollback consiste en devolver el tráfico a la revisión anterior de Cloud Run.
