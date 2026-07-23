# Runbook · maestro de proveedores Sage (Fase 1.5)

Fecha: 22/07/2026
Estado: listo para validación local; **no desplegado**.

## Objetivo y alcance

La fase 1.5 permite cargar un export XLSX del maestro de **proveedores** de
Sage y vincularlo con los documentos de la sesión. El workbook se procesa en
memoria y se descarta. No se guarda el archivo ni el maestro completo.

Se persisten únicamente el resumen técnico del import, el resultado no sensible
del match por documento y los eventos de auditoría. No se persisten nombres del
maestro, Tax IDs completos, IBAN ni códigos bancarios como evidencia del match.

## Export requerido

Columnas mínimas:

- `Cód. proveedor` (o `Cód. contable` como identificador de respaldo);
- `Razón social`.

Columnas usadas cuando están disponibles:

- `Nombre cli/pro.`;
- `CIF/DNI` y `CIF europeo`;
- `Sigla nación`;
- `I.B.A.N.` y `Cód. banco`;
- `Cód. condiciones`;
- `Baja empresa` y `Fecha baja`.

La aplicación rechaza un export con patrón de clientes: códigos de cliente
variables, código de proveedor constante y/o categoría `CLI`. Esto evita que
un tercero pagable se vincule silenciosamente contra la contraparte equivocada.

### Hallazgo sobre el archivo recibido

`output sage.xlsx` contiene 195 registros con 195 códigos de cliente únicos,
`Cód. proveedor = 168` en todas las filas y categoría `CLI` en todas las filas.
Por lo tanto es un maestro de clientes y el guardrail lo rechaza correctamente.
Para probar la fase con datos reales se necesita el export equivalente del
maestro de proveedores.

## Política canónica de vinculación

1. Si el documento informa Tax ID, se exige match exacto normalizado contra
   `CIF/DNI` o `CIF europeo`. Si no coincide, no se hace fallback por nombre.
2. Sin Tax ID confirmatorio, el nombre se normaliza con `casefold`, eliminación
   de acentos y puntuación, colapso de espacios y limpieza de sufijos legales
   finales (`SL`, `SLU`, `SA`, `SRL`, variantes puntuadas y equivalentes
   internacionales).
3. Se intenta match exacto sobre el nombre normalizado.
4. Recién después se calcula similitud con la utilidad compartida. El umbral
   único es `FUZZY_SIMILARITY_THRESHOLD` en `ap_control_tower/matching.py`.

Semántica sin Tax ID:

- un candidato fuzzy: se acepta y se muestra/audita el FYI
  `proveedor vinculado por similitud de nombre, sin tax ID que lo confirme`;
- múltiples candidatos: se deriva a revisión por ambigüedad;
- ningún candidato: se deriva como proveedor no encontrado.

El FYI no deriva por sí solo. Ambigüedad, Tax ID no encontrado, proveedor no
encontrado e identidad insuficiente sí quedan en la cola de revisión humana.
Si una reconciliación nueva deja un documento sin resolución segura, se
revierten su confirmación y aprobación de propuesta previas; la reversión queda
registrada en la misma cadena de auditoría.

## Operación local

1. Abrir `Ingreso de documentos`.
2. En `Maestro de proveedores de Sage`, elegir el XLSX.
3. Seleccionar `Validar y aplicar maestro`.
4. Revisar el conteo de proveedores activos y la referencia hash mostrada.
5. Cargar los PDF antes o después: aplicar el maestro reconcilia también los
   documentos que ya estaban en la sesión.
6. Consultar `Documentos` para ver `Vinculación Sage`, motivos, FYI y auditoría.

Al reanudar una sesión persistida se recuperan los matches ya auditados, pero
no el maestro sensible. Para conciliar documentos nuevos hay que volver a
cargar el export.

## Auditoría

Eventos posibles:

- `maestro-proveedores-sage-cargado`;
- `proveedor-vinculado-sage`;
- `proveedor-vinculado-por-similitud-nombre`;
- `proveedor-ambiguo-sage`;
- `proveedor-no-encontrado-sage`.

La evidencia incluye método, cantidad de candidatos, score, confirmación por
Tax ID y fingerprint del maestro. La cadena hash debe verificar después de
cada reconciliación.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m unittest evals.test_sage_vendor_master -v
.\.venv\Scripts\python.exe evals\test_trial_persistence.py
.\.venv\Scripts\python.exe evals\run_evals.py
```

Los casos cubren sufijos legales, acentos y mayúsculas, Tax ID prioritario,
match fuzzy único con FYI, dos proveedores legítimos parecidos que quedan
ambiguos, no encontrado, rechazo de export de clientes, privacidad y auditoría.

## Rollback y despliegue

No hay migración de base. Para desactivar la función, no cargar el maestro o
finalizar la sesión. El comportamiento canónico previo continúa sin cambios.

No ejecutar build, push, actualización de Cloud Run ni cambio de secretos hasta
la aprobación explícita del cliente.
