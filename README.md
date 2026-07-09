# AP Control Tower

Sistema de agentes maker-checker para el proceso de Cuentas a Pagar (AP/P2P) de una consultora.
Demo comercial que corre 100% local: **sin API keys, sin integraciones externas, sin acceso a red**. Todo con datos sinteticos.

> **El sistema bloquea solo. Liberar dinero siempre es humano.**

Cada etapa tiene un agente *maker* que produce y un agente *checker* independiente que valida contra reglas explicitas. Los controles que bloquean son automaticos: la factura que falla queda en la cola de excepciones sin intervencion humana. El unico gate humano del sistema es la liberacion del lote de pago al banco.

Repositorio **privado**. El material de referencia del proceso real vive en `docs/` (gitignoreado, confidencial, nunca se commitea).

## Estado (Dia 1 de 4)

| Entregable | Estado |
|---|---|
| Repo privado + docs/ blindado | Listo |
| Data model (proveedor, OC con lineas, factura, resultados, lotes) | Listo |
| Dataset sintetico completo (junio 2026, 36 facturas, 9 casos plantados) | Listo, pendiente de auditoria humana |
| Expected outputs (derivados de la intencion, no del motor) | Listo |
| Motor de controles C1-C7 + audit trail hash-chained | Listo (basico; makers/checkers completos y lote con doble sign-off: Dia 2) |
| Evals con contrato de exit code | Verdes (exit 0) |
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
| INV-009 | Match +1.69% vs OC (bajo materialidad) | C5 match | Avanza con flag |
| INV-020 | Match +1.44% vs OC (bajo materialidad) | C5 match | Avanza con flag |

Resultado de la corrida: 27 facturas pagables en 4 lotes (EUR 95.746,65), 6 bloqueadas (EUR 45.440,00 retenidos), 3 programadas para el proximo ciclo, 5 con flag soft (2 match menor + 3 intercompany).

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
2. El estado "liberada al banco" es **inalcanzable** sin aprobacion humana registrada.

## Estructura

```
ap_control_tower/
  config.py            # tolerancias y reglas explicitas
  catalogs.py          # plan de cuentas, BUs, proyectos
  models.py            # data model (Decimal para dinero)
  audit.py             # audit trail hash-chained (run_id + commit)
  dataset_builder.py   # el mes sintetico + expected outputs
  render_invoices.py   # facturas HTML para la vista documento->datos
  run_month.py         # CLI de corrida
  engine/
    controls.py        # C1-C7 (makers y checkers)
    pipeline.py        # orquestacion cronologica del mes
data/                  # dataset + expected + doc_previews (committeados)
evals/run_evals.py     # exit 0/1 como contrato
runs/                  # audit trails por corrida (gitignoreado)
docs/                  # confidencial, gitignoreado, NUNCA commitear
```
