# Corrida de evals · run2 (post-cambios de derivación)

Fecha: 2026-07-14
Golden dataset: v1.0 con correcciones del usuario (REDCOM→REDECOM, vencimientos GD-002/GD-005, públicas GD-063/066/073; pendiente: GD-001 oc_referenciada → vacía)
Cambios deployados a evaluar:
1. workflow.py: validación determinista pesa más que score de confianza; datos bancarios no estructurados = FYI, no deriva.
2. document_ai.py: regla proveedor = empresa propia (AP_OWN_COMPANY_NAMES, default "Meridia Consulting") deriva siempre.
3. pdf_poc.py: patrón OC "Nuestra ref.: XXX99/9999" (BMC de Gesmar). ORD- de Dynata queda como project_reference por diseño.

Deploy: gcloud run deploy ap-control-tower-trial --source . (primera vez desde source; creó repo cloud-run-source-deploy en Artifact Registry).

Target de la corrida: derivación a revisión humana <= 10% de la muestra, manteniendo recall 100% (cero falsos negativos).
Referencia run1: 88% derivado, precision 4.7%, recall 100%.

## Chequeo de humo (previo al lote completo)

| Doc | Comportamiento esperado | Resultado |
|---|---|---|
| GD-115 (proveedor sin NIF; extractor confunde emisor/receptor) | Deriva con motivo "empresa propia" | pendiente |
| GD-117 (domiciliación, sin datos bancarios estructurados) | NO deriva; advertencia bancaria solo FYI | pendiente |

## Tandas

(se completa durante la corrida)

## Incidencias

(ninguna aún)
