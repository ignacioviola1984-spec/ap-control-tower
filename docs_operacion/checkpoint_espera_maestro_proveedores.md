# Checkpoint · espera del maestro de proveedores

Fecha de corte: 23/07/2026  
Estado: **pausado de forma controlada hasta recibir el maestro de proveedores**.

## Decisión

No se hará uptraining de Google Document AI en este punto. La próxima decisión
de entrenamiento se tomará después de incorporar el maestro correcto, resolver
la identidad de los proveedores y volver a medir el sistema híbrido completo.

Ejecutar el Invoice Parser no modifica sus pesos: la corrida realizada fue de
inferencia y evaluación. Sus predicciones se conservaron como evidencia
auditada, sin convertirlas automáticamente en ground truth humano.

## Estado cerrado antes de la pausa

- Corpus privado inventariado: 260 PDF, de los cuales 164 son facturas, 79
  órdenes de compra y 17 otros documentos.
- Ground truth base: 100 documentos revisados.
- Corrida dirigida de Google Document AI: 50 PDF, 75 páginas enviadas, 87
  findings comparados y 0 errores.
- Resultado de la comparación: 64 findings `model_corroborated`.
- Revisión visual de excepciones: 18 valores del modelo confirmados y 5 valores
  del extractor local confirmados.
- Pendientes no nominales: 0.
- Pendientes nominales: 154 nombres comerciales. Permanecen como
  `auto_candidate` hasta contrastarlos con el maestro; no se fusionan ni se
  promueven silenciosamente.
- Memoria privada: 1.300 evidencias con 245 `verified_ground_truth`, 64
  `model_corroborated`, 278 `verified_absent`, 559 `not_observed` y 154
  `auto_candidate`.
- Se corrigió el extractor para distinguir `Invoice date`, `Date of service` y
  `Payment due date` en tablas de fechas separadas.
- Verificación final: 39 pruebas focalizadas y los 21 grupos de
  `evals/run_evals.py` en verde.

## Política de evidencia vigente

- `verified_ground_truth` puede corregir únicamente el mismo PDF por hash.
- `model_corroborated` requiere coincidencia exacta normalizada entre el
  extractor local y Google; sólo puede completar ese mismo PDF, nunca se hereda
  y no equivale a revisión humana.
- Entre documentos sólo puede reutilizarse un registro mercantil verificado,
  único y ligado por Tax ID exacto o nombre exacto normalizado.
- Los períodos de servicio y las condiciones de pago nunca se heredan de otra
  factura.
- La identidad del proveedor se resolverá con Tax ID prioritario y la política
  de nombre exacto/fuzzy definida en el runbook de Sage.

## Evidencia y privacidad

Los artefactos sensibles permanecen fuera de Git, Docker y Cloud Build:

- `private_evals/q1_q2_memory/historical_evidence.sqlite3`;
- `private_evals/q1_q2_memory/document_ai_targeted_v1/`;
- `private_evals/q1_q2_memory/field_evidence.csv`;
- `outputs/019f8acf-57d8-70a0-afcc-7a817910e533/memoria_historica_ap_q1_q2_v1.1.xlsx`.

La base no contiene bytes de los PDF. El runtime sólo la consulta si se
configura explícitamente `AP_EVIDENCE_MEMORY_PATH`, en modo de lectura.

## Insumo requerido para reanudar

Se necesita el export XLSX del maestro de **proveedores**, no el maestro de
clientes. Debe incluir como mínimo:

- `Cód. proveedor` o `Cód. contable`;
- `Razón social`;
- idealmente `Nombre cli/pro.`, `CIF/DNI`, `CIF europeo`, `Sigla nación`,
  `Cód. condiciones`, estado de baja e información bancaria disponible.

El archivo `output sage.xlsx` ya revisado corresponde a clientes y debe seguir
siendo rechazado por el guardrail.

## Secuencia exacta de reanudación

1. Validar el esquema y confirmar que el archivo es de proveedores.
2. Cargar el maestro antes de nuevos PDF y registrar su fingerprint.
3. Reconciliar los 154 nombres pendientes mediante Tax ID, nombre exacto
   normalizado y fuzzy seguro.
4. Derivar a revisión toda ambigüedad, Tax ID no encontrado o proveedor no
   encontrado.
5. Reprocesar los 100 documentos con el pipeline híbrido actualizado.
6. Recalcular precisión, recall, alucinaciones y tasa de revisión por campo.
7. Considerar uptraining únicamente si persiste alguno de estos síntomas:
   precisión inferior a 90–95% en un campo crítico, más de 5% de revisión por
   errores de extracción, formatos recurrentes no interpretados o una mejora
   potencial demostrable de al menos 8–10 puntos.
8. Si corresponde entrenar, crear una versión nueva con conjuntos de
   entrenamiento, validación y holdout sin solapamiento; no reemplazar la
   versión vigente hasta superar el baseline.

## Acciones suspendidas

Hasta recibir el maestro no se ejecutarán:

- promoción de los 154 nombres pendientes;
- uptraining o despliegue de una nueva versión de Document AI;
- cambios de umbral fuzzy;
- nuevo despliegue motivado por esta fase de evaluación.

El runbook operativo aplicable es
`docs_operacion/runbook_sage_vendor_master.md`.
