# Actualización de case studies · AP Control Tower

Contenido bilingüe listo para pegar. Mantiene el tono y las convenciones de cada sitio (honestidad sobre alcance, sin afirmaciones de exactitud auditada). Los números salen de los reportes run1 y run2 (Fable 5/AP Evals/Reportes).

---

## A · ignacioviola.com/ap-control-tower

### A1. REEMPLAZAR el bloque "Observed PoC result / Resultado observado en el PoC" completo por esta sección nueva:

**EN**

## Measured quality: the evaluation framework

The system is evaluated against a versioned golden dataset: 106 documents (8 real invoices, 76 public documents with independent labels and 22 synthetic stress cases including exact and near-duplicates, altered IBANs, unauthorized purchase orders, credit notes and non-invoice documents), each with its expected outcome defined by hand before processing.

The first evaluation run exposed a design flaw. Routing to human review was driven by the extractor's confidence score, and the score turned out to be inverted relative to measured accuracy: documents reported at 90-100% confidence had the lowest real accuracy (88.8%), while documents below 80% confidence were extracted correctly 97.8% of the time. As a result, 88% of documents were reaching human review.

The routing policy was redesigned around deterministic validations (net + VAT = total, format checks on tax IDs and IBANs, date plausibility, supplier distinct from the buying entity) and re-run against the same frozen dataset. Review load dropped from 88% to 9.2%, routing accuracy rose from 14.6% to 94.8%, field extraction accuracy reached 96.2%, and no payment-risk case slipped through: every document that had to be stopped was stopped.

The framework ships with the product. A "Measured quality" tab inside the PoC shows the dataset composition, per-field accuracy, the calibration finding and the improvement cycle, so evaluation evidence is part of the tool rather than a slide.

These figures describe evaluation runs over the declared dataset mix. They are not a claim of audited accounting accuracy, and the golden dataset with real invoices is not published.

**ES**

## Calidad medida: el framework de evaluación

El sistema se evalúa contra un golden dataset versionado: 106 documentos (8 facturas reales, 76 documentos públicos con etiquetas independientes y 22 casos sintéticos de estrés que incluyen duplicados exactos y casi-duplicados, IBAN alterados, órdenes de compra sin autorizar, notas de crédito y documentos que no son facturas), cada uno con su resultado esperado definido a mano antes de procesarlo.

La primera corrida de evaluación expuso un defecto de diseño. La derivación a revisión humana dependía del score de confianza del extractor, y ese score resultó estar invertido respecto de la exactitud medida: los documentos reportados con confianza 90-100% tenían la peor exactitud real (88,8%), mientras que los de confianza menor a 80% se extraían bien el 97,8% de las veces. La consecuencia: el 88% de los documentos llegaba a revisión humana.

La política de derivación se rediseñó sobre validaciones deterministas (neto + IVA = total, chequeos de formato de NIF e IBAN, plausibilidad de fechas, proveedor distinto de la entidad compradora) y se recorrió contra el mismo dataset congelado. La revisión bajó de 88% a 9,2%, la exactitud de ruteo subió de 14,6% a 94,8%, la extracción por campo llegó a 96,2%, y ningún caso de riesgo de pago pasó de largo: todo documento que debía frenarse, se frenó.

El framework viaja con el producto. Una pestaña "Calidad medida" dentro del PoC muestra la composición del dataset, la exactitud por campo, el hallazgo de calibración y el ciclo de mejora: la evidencia de evaluación es parte de la herramienta, no una lámina.

Estas cifras describen corridas de evaluación sobre la mezcla de documentos declarada. No constituyen una afirmación de exactitud contable auditada, y el golden dataset con facturas reales no se publica.

### A2. AGREGAR un bullet al final de "Control design / Diseño de controles":

**EN**
- Review routing is decided by deterministic validations, not by the extractor's confidence score: evaluation showed the score is inverted relative to real accuracy, so arithmetic, format and plausibility checks gate what reaches a person.

**ES**
- La derivación a revisión se decide con validaciones deterministas, no con el score de confianza del extractor: la evaluación mostró que el score está invertido respecto de la exactitud real, así que chequeos de aritmética, formato y plausibilidad definen qué llega a una persona.

### A3. OPCIONAL, chip de estado (junto a "Working private PoC · Real-invoice validation"):

**EN**: Evaluated against a 106-document golden dataset
**ES**: Evaluado contra un golden dataset de 106 documentos

---

## B · getdeterma.com/systems/ap-control-tower

### B1. REEMPLAZAR la sección "What the working PoC proves / Qué demuestra el PoC funcionando" por:

**EN**

## What the evaluation proves

Every version of the system is evaluated against a golden dataset: 106 documents whose correct outcome was defined by hand before processing, including real invoices, public documents and synthetic stress cases built to fail (duplicates, altered bank details, credit notes, documents that are not invoices).

The first run measured a review overload: 88% of documents were routed to a person, because routing depended on the extractor's confidence score, and the score proved inverted relative to real accuracy. The routing logic was rebuilt on deterministic validations and re-evaluated against the same dataset: review load dropped to 9.2%, extraction accuracy reached 96.2%, and no payment-risk case slipped through.

The result is visible inside the product: a "Measured quality" tab shows the dataset, the metrics of each run and the improvement cycle.

These figures describe evaluation runs over a declared test mix; they are not a claim of audited accounting accuracy.

**ES**

## Qué demuestra la evaluación

Cada versión del sistema se evalúa contra un golden dataset: 106 documentos cuyo resultado correcto se definió a mano antes de procesarlos, con facturas reales, documentos públicos y casos sintéticos de estrés construidos para fallar (duplicados, datos bancarios alterados, notas de crédito, documentos que no son facturas).

La primera corrida midió una sobrecarga de revisión: el 88% de los documentos llegaba a una persona, porque la derivación dependía del score de confianza del extractor, y ese score resultó invertido respecto de la exactitud real. La lógica se reconstruyó sobre validaciones deterministas y se re-evaluó contra el mismo dataset: la revisión bajó a 9,2%, la extracción llegó a 96,2% y ningún caso de riesgo de pago pasó de largo.

El resultado se ve dentro del producto: una pestaña "Calidad medida" muestra el dataset, las métricas de cada corrida y el ciclo de mejora.

Estas cifras describen corridas de evaluación sobre una mezcla de prueba declarada; no constituyen una afirmación de exactitud contable auditada.

### B2. REEMPLAZAR el bloque "Business outcome / Resultado de negocio" por:

**EN**

The team can judge automation on evidence, not promises: what the system reads, what it flags, what a person decides, and the measured quality of every version before it ships. When something fails, the evaluation catches it, the fix is verified against the same dataset, and the cycle is visible inside the tool.

**ES**

El equipo puede evaluar la automatización con evidencia, no promesas: qué lee el sistema, qué alerta, qué decide una persona, y la calidad medida de cada versión antes de desplegarse. Cuando algo falla, la evaluación lo detecta, el fix se verifica contra el mismo dataset y el ciclo queda visible dentro de la herramienta.

### B3. Meta description sugerida (reemplaza la actual):

**EN**: A working Accounts Payable control tower with Document AI extraction, maker-checker controls, human approval and a built-in evaluation framework: 106-document golden dataset, measured accuracy and a visible improvement cycle.

---

## Notas de consistencia

- Los dos sitios citan hoy "8 documentos, 23,4 s, 143 campos": esa corrida queda superada por la historia de evals; si querés conservarla, va como nota histórica, no como resultado principal.
- No afirmar "cero falsos negativos" a secas: la formulación correcta y defendible es "ningún caso de riesgo de pago pasó de largo" (la nota de crédito que pasó la revisión quedaba retenida por el gate de aprobación, y el fix posterior está verificado).
- El repo de GitHub linkeado en ambos sitios debería estar actualizado con los cambios antes de publicar, porque un lector técnico va a ir a mirarlo.
