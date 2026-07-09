# AP Control Tower

Sistema de agentes maker-checker para el proceso de Cuentas a Pagar (AP/P2P) de una consultora.
Demo comercial que corre 100% local: **sin API keys, sin integraciones externas, sin acceso a red**. Todo con datos sinteticos.

> **El sistema bloquea solo. Liberar dinero siempre es humano.**

Cada etapa tiene un agente *maker* que produce y un agente *checker* independiente que valida contra reglas explicitas. Los controles que bloquean son automaticos: la factura que falla queda en la cola de excepciones sin intervencion humana. El unico gate humano del sistema es la liberacion del lote de pago al banco.

Repositorio **privado**. El material de referencia del proceso real vive en `docs/` (gitignoreado, confidencial, nunca se commitea).

## Estado (Dia 2 de 4)

| Entregable | Estado |
|---|---|
| Repo privado + docs/ blindado | Listo |
| Data model (proveedor, OC con lineas, factura, resultados, lotes) | Listo |
| Dataset sintetico completo (junio 2026, 36 facturas, 10 casos plantados) | Auditado y aprobado (Dia 1) |
| Expected outputs (derivados de la intencion, no del motor) | Listo |
| Motor de controles C1-C7 + audit trail hash-chained | Listo |
| Lote de pago: doble sign-off agentico (checker A + checker B) | Listo |
| Gate humano: maquina de estados con aprobacion nominada | Listo |
| Cierre: conciliacion automatica pago vs pasivo | Listo |
| Evals con contrato de exit code (12 grupos, incl. gate e invariantes) | Verdes (exit 0) |
| Facturas renderizadas como documento (6) | Listo (`data/doc_previews/`) |
| UI Streamlit (6 vistas) + theming | Dia 3 |

## Como correr y verificar (Dia 1)

Requiere Python 3.11+. El motor y los evals usan solo la libreria estandar.

```bash
# 1. Generar el dataset sintetico y los expected outputs
python -m ap_control_tower.dataset_builder

# 2. Correr el mes completo (36 facturas por el pipeline de controles)
python -m ap_control_tower.run_month

# 3. Evals: exit 0 = verde, distinto de 0 = contrato roto
python evals/run_evals.py

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
    pipeline.py        # orquestacion cronologica del mes
    batch.py           # doble sign-off agentico + maquina de estados del gate
    closing.py         # cierre: conciliacion pago vs pasivo
data/                  # dataset + expected + doc_previews (committeados)
evals/run_evals.py     # exit 0/1 como contrato
runs/                  # audit trails por corrida (gitignoreado)
docs/                  # confidencial, gitignoreado, NUNCA commitear
```
