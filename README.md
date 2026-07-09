# AP Control Tower

Sistema de agentes maker-checker para el proceso de Cuentas a Pagar (AP/P2P) de una consultora.
Demo comercial que corre 100% local: **sin API keys, sin integraciones externas, sin acceso a red**. Todo con datos sinteticos.

> **El sistema se auto-bloquea ante alertas. La aprobación para liberar dinero es siempre humana.**

Cada etapa tiene un agente *maker* que produce y un agente *checker* independiente que valida contra reglas explicitas. Los controles que bloquean son automaticos: la factura que falla queda en la cola de excepciones sin intervencion humana. El unico gate humano del sistema es la liberacion del lote de pago al banco.

Repositorio **privado**. El material de referencia del proceso real vive en `docs/` (gitignoreado, confidencial, nunca se commitea).

## Estado (Dia 3 de 4)

| Entregable | Estado |
|---|---|
| Repo privado + docs/ blindado | Listo |
| Dataset sintetico (junio 2026, 36 facturas, 10 casos plantados) | Auditado y aprobado (Dia 1) |
| Motor de controles C1-C7 + audit trail hash-chained | Listo (Dia 2) |
| Doble sign-off agentico + gate humano + cierre | Listo (Dia 2) |
| UI Streamlit: 6 vistas + theming corporativo + gate vivo | Listo |
| Password gate server-side (env AP_DEMO_PASSWORD) | Listo |
| Dockerfile + .dockerignore (puerto por CLI/PORT, sin hardcodeo) | Listo (build no ejecutado localmente: sin Docker en esta maquina) |
| Evals con contrato de exit code (14 grupos, incl. arranque de la app) | Verdes (exit 0) |
| Ensayo humano + fixes | Dia 4 |

## Como correr la UI

```powershell
# Windows (PowerShell) - el password es el que VOS elijas, nunca esta en el repo
$env:AP_DEMO_PASSWORD = 'eleg-un-password'
streamlit run app.py --server.port 8501
```

```bash
# Linux / macOS
AP_DEMO_PASSWORD='eleg-un-password' streamlit run app.py --server.port 8501
```

- Sin `AP_DEMO_PASSWORD` la app muestra "demo no configurada" y no renderiza nada.
- El puerto se pasa SIEMPRE por CLI (`--server.port`); no hay puertos hardcodeados.
- La sesion autenticada dura lo que dura la session_state (recargar la pagina pide password de nuevo).

## Docker (ensayo local y Cloud Run)

```bash
# build (GIT_COMMIT queda en el audit trail de la imagen)
docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t ap-control-tower .

# ensayo local: mismo contrato que Cloud Run (PORT + AP_DEMO_PASSWORD por env)
docker run --rm -p 8080:8080 -e PORT=8080 -e AP_DEMO_PASSWORD=ensayo-local ap-control-tower
# abrir http://localhost:8080
```

`.dockerignore` excluye `docs/`, `*.docx`, `.env`, `.git`, `runs/` y `__pycache__`:
el material confidencial jamas entra a la imagen. En Cloud Run, `--port` y las
env vars se pasan en el deploy; la imagen no fija ninguno de los dos.

## TODO (post-validacion)

- La leyenda del sidebar "Modo demo · datos 100% sinteticos · Sin API keys ·
  sin red · local" se actualizara cuando el sistema este validado con
  documentos reales y deployado en cloud.

## Como correr y verificar (Dia 1)

Requiere Python 3.11+. El motor y los evals usan solo la libreria estandar.

```bash
# 1. Generar el dataset sintetico y los expected outputs
python -m ap_control_tower.dataset_builder

# 2. Correr el mes completo (36 facturas por el pipeline de controles)
python -m ap_control_tower.run_month

# 3. Evals: exit 0 = verde, distinto de 0 = contrato roto
python evals/run_evals.py            # 14 grupos (incluye arranque real de la app)
python evals/run_evals.py --sin-app  # salta el grupo de arranque (CI sin GUI)

# 4. (opcional) Regenerar las facturas visuales
python -m ap_control_tower.render_invoices
```

En Windows, si la consola falla con caracteres, anteponer `PYTHONIOENCODING=utf-8`.

## El mes sintetico (el guion de la venta)

Junio 2026: 36 facturas en EUR de 18 proveedores inventados, 4 jueves de pago (4, 11, 18, 25). Ningun dato real de ninguna empresa.

Casos plantados:

| Factura | Caso | Control que actua | Resultado |
|---|---|---|---|
| INV-023 | Duplicada exacta de INV-005 (reenvio del proveedor) | C2 duplicados | Bloqueada |
| INV-015 | Casi-duplicada de INV-007 (mismo importe, numero distinto, 3 dias) | C2 duplicados | Bloqueada |
| INV-014 | Email sin OC adjunta | C1 completitud | Bloqueada |
| INV-033 | OC sin saldo (ya consumido por INV-017) | C3 autorizacion | Bloqueada |
| INV-024 | **Fraude: IBAN distinto del maestro** (caso estrella) | C6 datos bancarios | Bloqueada + alerta |
| INV-025 | Match +18.27% vs OC (supera materialidad) | C5 match | Bloqueada |
| INV-029 | Divergencia cashflow vs ERP: Excel heredado con 1.476,30 tipeado a mano, la factura real dice 1.467,30 | C7 conciliacion | Bloqueada |
| INV-009 | Match +1.69% vs OC (bajo materialidad) | C5 match | Avanza con flag |
| INV-020 | Match +1.44% vs OC (bajo materialidad) | C5 match | Avanza con flag |

Resultado de la corrida: 26 facturas pagables en 4 lotes (EUR 94.279,35), 7 bloqueadas (EUR 46.907,30 retenidos), 3 programadas para el proximo ciclo, 5 con flag soft (2 match menor + 3 intercompany). Lotes: 04-jun 21.785,90 / 11-jun 36.005,90 / 18-jun 27.643,35 / 25-jun 8.844,20.

## El lote de pago y EL gate humano

Cada jueves, el lote propuesto atraviesa una maquina de estados que no admite atajos (`ap_control_tower/engine/batch.py`):

```
propuesto -> [checker A: revalida cada factura contra los 7 controles,
              con el estado del mundo AL jueves del lote]
          -> [checker B: valida el agregado: totales, limite por proveedor,
              duplicados cruzados dentro del lote y contra otros lotes, moneda]
          -> pendiente_aprobacion_humana
          -> approve(nombre) -> aprobado -> liberado_al_banco
          -> reject(nombre, motivo) -> rechazado (facturas a lote_devuelto)
```

Cualquier transicion invalida (liberar sin aprobar, aprobar sin los dos
sign-offs, aprobar sin nombre, cerrar sin liberar) levanta `GateViolation`.
La aprobacion registra nombre, decision y timestamp en el audit trail.
Tras la liberacion, el cierre (`engine/closing.py`) concilia automaticamente
cada pago contra su pasivo, lo cancela y reporta excepciones: el humano revisa
excepciones, no el 100%.

## Controles del pipeline

| # | Control | Severidad |
|---|---|---|
| C1 | Completitud documental (factura + OC en el email) | Hard |
| C2 | Duplicados y casi-duplicados | Hard |
| C3 | Autorizacion de OC: aprobada, vigente, con saldo | Hard |
| C4 | Imputacion contable y BU (maker propone, checker valida) + local/intercompany | Soft |
| C5 | Match factura vs OC con tolerancias (5% / EUR 750) | Hard sobre materialidad, soft debajo |
| C6 | Datos bancarios del proveedor vs maestro | Hard + alerta de fraude |
| C7 | Conciliacion pre-pago cashflow vs ERP | Hard |

Tolerancias y reglas viven en `ap_control_tower/config.py`, explicitas y configurables.

## Invariantes (los evals los hacen contrato)

1. La factura con fraude bancario **nunca** aparece en un lote de pago.
2. El estado "liberada al banco" es **inalcanzable** sin aprobacion humana registrada. Los evals lo prueban por la via dura: intentan saltarse el gate (liberar sin aprobar, aprobar sin sign-offs, aprobar sin nombre, liberar un lote rechazado) y exigen `GateViolation` en cada intento. Tambien prueban tampering (un pasivo adulterado detiene el lote via checker A) y limites del agregado (checker B).

## Estructura

```
app.py                 # entrypoint Streamlit (password gate primero)
Dockerfile             # python slim, requirements-first, ARG/ENV GIT_COMMIT
ap_control_tower/
  config.py            # tolerancias y reglas explicitas
  catalogs.py          # plan de cuentas, BUs, proyectos
  models.py            # data model (Decimal para dinero)
  audit.py             # audit trail hash-chained (run_id + commit)
  dataset_builder.py   # el mes sintetico + expected outputs
  render_invoices.py   # facturas HTML para la vista documento->datos
  run_month.py         # CLI de corrida (llega hasta el gate, jamas lo cruza)
  engine/
    controls.py        # C1-C7 (makers y checkers)
    pipeline.py        # MonthRunner incremental + orquestacion del mes
    batch.py           # doble sign-off agentico + maquina de estados del gate
    closing.py         # cierre: conciliacion pago vs pasivo
  ui/
    auth.py            # password gate server-side (AP_DEMO_PASSWORD)
    theme.py           # theming corporativo (paleta, badges, cards, tablas)
    state.py           # corrida y workflows en session_state
    views/             # inbox, detalle, excepciones, gate, auditoria, caso de negocio
data/                  # dataset + expected + doc_previews (committeados)
evals/run_evals.py     # exit 0/1 como contrato (14 grupos)
runs/                  # audit trails por corrida (gitignoreado)
docs/                  # confidencial, gitignoreado, NUNCA commitear
```

## Las 6 vistas del tablero

1. **Corrida del mes**: "Procesar mes" corre el motor real factura por factura
   con progreso visible y velocidad regulable (instantaneo / ~40 s / modo
   reunion ~3 min). KPIs, lotes y las 36 facturas con badges de estado.
2. **Detalle de factura**: el documento sintetico renderizado al lado de los
   datos extraidos y el resultado de cada control, en orden.
3. **Cola de excepciones**: las bloqueadas con control, evidencia (esperado vs
   recibido) y dueno sugerido. El fraude bancario tiene pantalla dedicada con
   el diff contra el maestro y accion recomendada.
4. **Aprobacion de pagos (EL gate)**: lote del jueves con los dos sign-offs
   agenticos, total a liberar, y el flujo humano vivo: aprobar pide nombre y
   registra decision + timestamp; rechazar devuelve el lote con motivo. Tras
   liberar: cierre con conciliacion pago vs pasivo.
5. **Registro de auditoria**: tabla cronologica completa filtrable por factura
   / agente / control, cadena de hashes verificada en vivo, export a CSV.
6. **Caso de negocio**: sus metricas declaradas vs lo medido en la corrida;
   cobertura de los 3 huecos (duplicados, fraude bancario, conciliacion).
   Unico numero estimado: horas/mes, con el parametro visible y ajustable.
