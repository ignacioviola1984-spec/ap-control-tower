# Reporte de evals · AP Control Tower · run2

Fecha: 2026-07-14 · Golden dataset: v1.0 corregido (106 documentos) · 109 procesados, 3 fuera de alcance
Cambios evaluados: derivación por validación determinista (no por score de confianza), datos bancarios no estructurados como FYI, regla proveedor=empresa propia, patrón OC adicional.

## 1. Delta run1 → run2 (mismos 106 documentos, mismo golden salvo correcciones documentadas)

| Métrica | run1 | run2 | Delta |
|---|---|---|---|
| Derivación a revisión humana | 96/109 (88%) | 10/109 (9.2%) | Target <10% CUMPLIDO |
| Exactitud de ruteo (alcance v1, 96 docs) | 14.6% | 94.8% | +80 pts |
| Precision de derivación | 4.7% | 42.9% | 9x |
| Recall de derivación | 100% | 75% | 1 falso negativo (ver §3) |
| Exactitud de extracción agregada | 95.3% | 96.2% | +0.9 pts |
| Exactitud proveedor | 85.8% | 89.6% | +3.8 pts (correcciones de golden + regla empresa propia) |

## 2. Los 10 derivados de run2, con veredicto

| Doc | Motivo del sistema | Veredicto |
|---|---|---|
| GESMAR (GD-004) | Clasificada proforma | Correcto (esperado del golden) |
| GD-115 | Proveedor = empresa propia | Correcto: regla nueva funcionando |
| GD-116, GD-120 | Proformas | Correcto |
| GD-085 | Proveedor ausente | Derivación justificada: la extracción falló de verdad |
| GD-060 | Proveedor = cliente | Derivación justificada: señal real del documento |
| GD-018 | Importes no cuadran aritméticamente | Derivación justificada |
| GD-105, GD-106, GD-108 | Grupo de duplicados Foxtrot | GD-106/108 correcto; GD-105 (la original) derivada por diseño del detector, que marca el grupo completo. Decisión pendiente: ¿la original debe frenarse junto con sus copias? Argumento a favor: hasta que un humano decida cuál es la buena, ninguna debería pagarse. |

Precision "efectiva": de los 4 falsos positivos formales, los 4 tienen causa legítima. Ninguna derivación de run2 es ruido de umbral, que era el 100% del problema en run1.

## 3. Falsos negativos (la sección que importa)

**GD-119 · nota de crédito con importe negativo · pasó la revisión como factura.** El clasificador la marcó invoice y ningún chequeo de revisión la frenó. Mitigante: el gate de aprobación posterior la retiene por "importe no positivo", así que el dinero está protegido; el fallo es de clasificación, no de riesgo de pago. Fix propuesto para la próxima iteración: importe_total <= 0 en revisión debe derivar con motivo "posible nota de crédito".

**GD-107 · casi-duplicado (número correlativo, 1 céntimo de diferencia) · no detectado.** Fuera del alcance v1 formal (esperado: bloqueada C2), pero el detector de duplicados del trial matchea solo proveedor+número exactos. Es exactamente la debilidad que este caso fue diseñado para exponer. Fix propuesto: matching difuso por proveedor + importe con tolerancia + ventana temporal.

**Nota sobre DYNATA (GD-001):** en run1 derivaba por baja confianza pese a extraer bien; el golden corregido la espera en lote y el sistema ahora la manda a lote con datos correctos. El caso pasó de falso positivo a acierto por el cambio de política.

## 4. Calibración (confirmación del hallazgo de run1)

La inversión persiste: bucket <80% de confianza → 97.8% de exactitud real; bucket 90-100% → 91.1%. Refuerza la decisión de diseño: el score del extractor no es señal de derivación. Con la política determinista, esta inversión ya no genera costo operativo.

## 5. Alcance pendiente (run3 / eval v2)

- Controles C2-C9 conectados al circuito del trial: 8 casos "bloqueada" esperando (incluidos los 2 de fraude por IBAN, GD-109/110), 1 anticipo, 1 conciliación.
- Nota de crédito: regla de importe negativo en revisión (fix de GD-119).
- Casi-duplicados: matching difuso (fix de GD-107).
- Export: incluir IBAN extraído y NIF completo para auditabilidad de C6.

## 6. Limitaciones

Las mismas de run1 (muestra real chica, públicas generadas, moneda de públicas asumida) más una nueva: recall de derivación calculado sobre base de 4 documentos esperados en revisión; con base tan chica, un solo miss mueve 25 puntos. Interpretar con esa cautela.

## Anexo

- detalle_run2.csv: 1.060 comparaciones celda por celda.
- Export de extracción run2 y golden v1.0 corregido, en las carpetas Corridas y Golden.
