# Torre de Control para Cuentas a Pagar

Aplicación piloto para la operación de Cuentas a Pagar de **Brand UP**. Reúne
ingreso documental, extracción, controles, revisión humana, propuesta de pago,
auditoría e indicadores en una única experiencia Streamlit.

El rediseño completó la revisión local y recibió aprobación explícita para su
publicación controlada el 22/07/2026. El despliegue conserva secretos, identidad
de servicio y límites de escalado del servicio existente.

Las decisiones de revisión y de propuesta de pago permanecen separadas. Una
aprobación en esta aplicación incorpora documentos a una propuesta controlada;
no contabiliza, no genera un archivo bancario y no libera dinero.

Cada etapa tiene un agente *maker* que produce y un agente *checker* independiente que valida contra reglas explicitas. Los controles que bloquean son automaticos: la factura que falla queda en la cola de excepciones sin intervencion humana. El unico gate humano del sistema es la liberacion del lote de pago al banco.

Repositorio **privado**. El material de referencia del proceso real vive en `docs/` (gitignoreado, confidencial, nunca se commitea).

## Estado del piloto local

| Entregable | Estado |
|---|---|
| Repo privado + docs/ blindado | Listo |
| Dataset sintético de evaluación (junio 2026, 42 documentos) | Auditado |
| Motor de controles C1-C7 + audit trail hash-chained | Listo (Dia 2) |
| Doble sign-off agentico + gate humano + cierre | Listo (Dia 2) |
| UI Streamlit unificada y operativa | Listo para revisión local |
| Password gate server-side (`AP_SYSTEM_PASSWORD`) | Listo |
| Dockerfile + .dockerignore (puerto por CLI/PORT, sin hardcodeo) | Build y smoke test verdes en Docker dentro de WSL |
| Evals con contrato de exit code (21 grupos, incl. app, Document AI, Sage y memoria histórica) | Verdes (exit 0) |
| Maestro Sage (Fase 1.5): Tax ID, nombre normalizado, fuzzy seguro y auditoría | Función lista; validación real pausada hasta recibir el export correcto |
| Ensayo humano + fixes | Listo |

## Cómo ejecutar la aplicación

```powershell
# Windows (PowerShell). La contraseña se define solo en la sesión del proceso.
$env:AP_SYSTEM_PASSWORD = 'elegir-una-contraseña-temporal'
.\.venv\Scripts\streamlit.exe run app.py --server.port 8501
```

```bash
# Linux / macOS
AP_SYSTEM_PASSWORD='elegir-una-contraseña-temporal' streamlit run app.py --server.port 8501
```

- Sin `AP_SYSTEM_PASSWORD` la aplicación informa que el acceso no está configurado.
- `AP_DEMO_PASSWORD` se acepta temporalmente como compatibilidad y se retirará en una etapa posterior.
- El puerto se pasa SIEMPRE por CLI (`--server.port`); no hay puertos hardcodeados.
- La sesion autenticada dura lo que dura la session_state (recargar la pagina pide password de nuevo).

## Docker local

```bash
# Dentro de WSL. El login ADC es interactivo y se hace una sola vez.
gcloud auth application-default login

# build (GIT_COMMIT queda en el audit trail de la imagen)
docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t ap-control-tower .

# ensayo local: monta ADC de WSL como archivo de solo lectura
docker run --rm -p 8080:8080 \
  -e PORT=8080 -e AP_SYSTEM_PASSWORD=revision-local \
  --env-file config/gcp-runtime.example \
  -e GOOGLE_APPLICATION_CREDENTIALS=/var/secrets/google/adc.json \
  -v "$HOME/.config/gcloud/application_default_credentials.json:/var/secrets/google/adc.json:ro" \
  ap-control-tower
# abrir http://localhost:8080
```

Configuracion no secreta validada:

- Proyecto: `singular-backup-501617-r6`
- Invoice Parser: `ap-control-tower-invoice-parser`
- Location: `us`
- Processor ID: `761304b2b69eba0`

Los valores viven en `config/gcp-runtime.example`; el password y las
credenciales nunca se escriben en el repo.

`.dockerignore` excluye `docs/`, `*.docx`, `.env`, `.git`, `runs/` y `__pycache__`:
el material confidencial jamas entra a la imagen. En Cloud Run, `--port` y las
env vars se pasan en el deploy; la imagen no fija ninguno de los dos.

La cuenta de servicio de Cloud Run necesita `roles/documentai.apiUser`. En
Cloud Run las credenciales vienen del metadata server: no se monta ADC ni se
pasa `DOCUMENT_AI_ACCESS_TOKEN`. Las facturas se envian al Invoice Parser del
proyecto Google Cloud; la app no persiste una copia local. Proformas y ordenes
de compra conservan el flujo deterministico local.

### Infraestructura existente de referencia

Los siguientes datos describen el servicio anterior. El producto unificado de
este repositorio no fue desplegado durante la etapa de revisión local.

- Servicio: `ap-control-tower`
- Region: `us-central1`
- Escalado: 0 a 1 instancia (control de costo)
- Identidad: `ap-control-tower-runner@singular-backup-501617-r6.iam.gserviceaccount.com`
- Password: Secret Manager `ap-demo-password` (nunca en imagen o repo)
- Job de diagnostico: `ap-document-ai-smoke`, factura sintetica generada en
  memoria para verificar identidad de servicio -> Document AI.

## Extraccion de documentos (esquema v2)

Modulo `ap_control_tower/extraction/`, ajustado con el analisis de facturas
reales del cliente (las facturas reales viven fuera del repo: `invoices & OC/`
y `Golden Records.xlsx` estan gitignoreados y un eval lo verifica).

- **Invoice Parser** (`document_ai.py`): OCR y extraccion visual administrada
  para facturas. Mapea entidades normalizadas, reconcilia proveedor/cliente,
  valida base + IVA = total y deriva revision cuando falta evidencia critica.
- **Esquema** (`schema.py`): `document_type` PRIMERO ("invoice" |
  "proforma_or_advance_request" | "other"; la clasificacion es parte del
  output evaluado) + 29 campos: identidad fiscal del proveedor (razon social
  NUNCA inventada), fechas (vencimiento texto crudo + calculado), periodo de
  servicio estructurado, IVA con `tratamiento_iva`, metodo de pago, banco,
  cuenta local/CCC, IBAN validado por checksum, BIC validado, y separacion
  estricta `po_reference` (solo si esta
  etiquetado como PO/OC/pedido) vs `project_reference` (ORD-xxx y similares).
- **Prompt** (`prompt.py`): generado desde el esquema, con la regla
  anti-alucinacion explicita: campo no visible = null, nunca inferido;
  "no esta" se distingue de "esta pero ilegible" via `campos_ilegibles`.
- **Comparador** (`comparator.py`): los null cuentan (null==null es acierto);
  inventar valor donde el humano etiqueto null es **alucinacion** y se
  reporta por separado; ademas omisiones y discrepancias, con normalizacion
  por tipo de campo.
- **Fixtures** (`data/extraction/`): 5 documentos sinteticos que cubren los
  casos reales: proforma sin CIF, domiciliacion SEPA, intracomunitaria con
  reverse charge, IBAN enmascarado, vencimiento "45 days end of month".
  Regenerar con `python -m ap_control_tower.extraction.synthetic_fixtures`.
- **Etiquetado**: `data/extraction/labels_template.csv` (columnas
  sincronizadas al esquema por eval) para etiquetar documentos nuevos.
- **Evaluacion administrada**: `python evals/run_document_ai_poc.py docs/poc-real`
  procesa una carpeta ignorada y deja el CSV resultante bajo `runs/`, tambien
  ignorado por Git.
- **Prueba real autorizada (2026-07-11)**: 11 documentos procesados; 8
  facturas por Invoice Parser y 3 documentos no-factura por el extractor local.
  Los PDFs y resultados detallados permanecen fuera de Git. Tambien se valido
  el circuito Docker (WSL) -> Document AI con una factura real montada en modo
  solo lectura.

## Persistencia opcional (Fase 1 · industrializacion)

Capa **aditiva** de PostgreSQL detras de repositorios, en `ap_control_tower/persistence/`
(SQLAlchemy 2.0 + Alembic). **Sin `AP_DATABASE_URL` la aplicación funciona
sin persistencia**: no hay base y no cambia el motor. Las dependencias viven
en `requirements-persistence.txt` (fuera de la imagen base).

```bash
pip install -r requirements-persistence.txt
docker compose -f docker-compose.dev.yml up -d      # Postgres local aislado (WSL)
export AP_DATABASE_URL="postgresql+psycopg://ap:ap_dev_local@localhost:5432/ap_control_tower"
python -m alembic upgrade head                       # migraciones (base vacia o existente)
python evals/test_persistence.py                     # round-trip motor->base (exit 0/1)
```

Detalle, modelo relacional y guia de operacion/rollback/recuperacion:
`ap_control_tower/persistence/README.md`.

## Maestro de proveedores Sage (Fase 1.5)

La página `Ingreso de documentos` acepta un export XLSX del maestro de
**proveedores** de Sage. El archivo vive solo en memoria y se descarta; la
persistencia conserva únicamente el resumen y el resultado no sensible del
match. La política prioriza Tax ID exacto, luego nombre fuertemente normalizado
y recién después similitud fuzzy con un umbral único compartido. Un match fuzzy
único se acepta con FYI visible y auditada; múltiples candidatos o ninguno se
derivan a revisión.

El archivo local `output sage.xlsx` fue identificado como maestro de clientes
(`Cód. proveedor` constante y categoría `CLI`), por lo que el guardrail lo
rechaza. Se necesita el export de proveedores para la validación con datos
reales. Operación, privacidad y rollback:
`docs_operacion/runbook_sage_vendor_master.md`.

## Memoria histórica de evidencia documental

El corpus privado Q1-Q2 puede reforzar `proveedor_registro`,
`periodo_servicio_desde`, `periodo_servicio_hasta` y `condiciones_pago` sin
convertir una inferencia en dato maestro. La memoria SQLite se habilita de
forma explícita y en modo de solo lectura:

```bash
export AP_EVIDENCE_MEMORY_PATH="/ruta/privada/historical_evidence.sqlite3"
streamlit run app.py
```

Los valores verificados pueden corregir el mismo PDF por su hash. Entre
documentos sólo se reutiliza un registro mercantil verificado, único y ligado
por Tax ID exacto o nombre exacto normalizado. Los períodos de servicio y las
condiciones de pago nunca se heredan de otra factura. La base está excluida de
Git, Docker y Cloud Build; si la variable no existe, el runtime conserva el
comportamiento anterior. Un hallazgo `model_corroborated` es una coincidencia
exacta normalizada entre el extractor local y Google Document AI: puede
completar únicamente ese mismo PDF por hash, nunca se propaga a otro documento
y no se etiqueta como ground truth humano.

Checkpoint vigente al 23/07/2026: la evaluación queda pausada hasta recibir el
maestro correcto de proveedores. No se hará uptraining antes de reconciliar los
154 nombres pendientes y volver a medir el pipeline híbrido. Estado, evidencia
y secuencia de reanudación:
`docs_operacion/checkpoint_espera_maestro_proveedores.md`.

## API interna opcional (Fase 4 · industrializacion)

API HTTP **separada de la UI** (FastAPI) sobre la capa `app/`, para que ERP/
portales consuman las operaciones controladas (corrida, gate, revisión,
excepciones, auditoría, métricas, carga de documentos). Versionada en `/v1`,
con OpenAPI en `/docs`, idempotencia, paginación, correlación y datos bancarios
enmascarados. Dependencias en `requirements-api.txt` (fuera de la imagen de la aplicación).

```bash
pip install -r requirements-api.txt
uvicorn ap_control_tower.api.main:app --port 8000   # http://localhost:8000/docs
python evals/test_api.py                            # verificacion (exit 0/1)
```

Detalle y endpoints: `ap_control_tower/api/README.md`.

## Cola de tareas opcional (Fase 5 · industrializacion)

Procesamiento **asincrono** (extraccion / Document AI / controles) con Celery +
Redis, para no bloquear la request. El nucleo de la cola (reintentos con backoff,
timeout, dead-letter, idempotencia, reproceso manual) es puro y se testea sin
infra; Celery/Redis son el transporte. `POST /v1/documents` encola y devuelve
`202 + task_id`; el resultado se consulta en `GET /v1/tasks/{id}`.

```bash
pip install -r requirements-worker.txt
docker compose -f docker-compose.dev.yml up -d redis worker
export AP_BROKER_URL=redis://localhost:6379/0
python evals/test_worker.py && python evals/test_worker_celery.py
```

Justificacion Cloud Run (workers vs escala-a-0) y endpoints: `ap_control_tower/worker/README.md`.

## Como correr y verificar (Dia 1)

Requiere Python 3.11+. El motor y los evals usan solo la libreria estandar.

```bash
# 1. Generar el dataset sintetico y los expected outputs
python -m ap_control_tower.dataset_builder

# 2. Correr el mes completo (36 facturas por el pipeline de controles)
python -m ap_control_tower.run_month

# 3. Evals: exit 0 = verde, distinto de 0 = contrato roto
python evals/run_evals.py            # 21 grupos (incluye app, Document AI, Sage y memoria)
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
| INV-014 | Factura sin OC (ruta non-PO sin aprobador/CC/soporte) | C10 gobierno non-PO | Pendiente de datos internos |
| INV-101 | Proforma: anticipo pagado sin factura final | C0 + C8 anticipos | Excepcion (jamas en lote) |
| INV-102 | Domiciliacion SEPA con mandato, non-PO gobernada | C11 mandato | Tarea conciliacion post-debito |
| INV-103 | Pago con tarjeta, non-PO gobernada | flujo tarjeta | Tarea conciliacion extracto |
| INV-104 | Intracomunitaria con inversion del sujeto pasivo (con OC) | C4 tratamiento IVA | En lote 25-jun |
| INV-105 | Non-PO limpia con gobierno completo (notaria) | C10 + C4 por reglas | En lote 25-jun |
| INV-106 | Non-PO sin datos internos (mensajeria) | C10 gobierno non-PO | Pendiente de datos internos |

Resultado de la corrida: 42 documentos; 28 facturas pagables en 4 lotes (EUR 98.859,85), 6 bloqueadas (EUR 42.057,30 retenidos), 2 pendientes de datos internos, 2 tareas de conciliacion (DD/tarjeta), 1 anticipo en excepcion, 3 programadas para el proximo ciclo, 5 con flag soft. Lotes: 04-jun 21.785,90 / 11-jun 36.005,90 / 18-jun 27.643,35 / 25-jun 13.424,70.

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

| # | Control | Efecto |
|---|---|---|
| C0 | Clasificacion del documento: factura / proforma / otro (etapa 0) | Enruta el flujo |
| C1 | Completitud documental (si referencia OC, el PDF de la OC es obligatorio) | Hard |
| C2 | Duplicados y casi-duplicados | Hard |
| C3 | Autorizacion de OC: aprobada, vigente, con saldo (solo ruta PO) | Hard |
| C4 | Imputacion contable, BU y tratamiento de IVA (maker propone, checker valida) | Soft |
| C5 | Match factura vs OC con tolerancias (5% / EUR 750; solo ruta PO) | Hard sobre materialidad, soft debajo |
| C6 | Datos bancarios del proveedor vs maestro (SOLO transferencias) | Hard + alerta de fraude |
| C7 | Conciliacion pre-pago cashflow vs ERP | Hard |
| C8 | Anticipo pagado sin factura final posterior | Excepcion |
| C9 | Completitud del maestro de proveedores (tax_id, razon social) | Retencion |
| C10 | Gobierno non-PO: aprobador + centro de coste + contrato/soporte | Retencion |
| C11 | Mandato SEPA registrado para domiciliaciones | Hard |

Tolerancias y reglas viven en `ap_control_tower/config.py`, explicitas y configurables.

## Flujos reales (rama feat/real-world-flows)

La realidad AP del cliente es mayormente non-PO, con proformas y varios
metodos de pago. El motor lo refleja:

- **Etapa 0**: clasificador de documento. Una proforma NUNCA entra al flujo
  de facturas: va al flujo de anticipos (exige aprobacion interna del
  presupuesto; anticipo pagado sin factura final = excepcion C8) y JAMAS
  puede aparecer en un lote de pago (INVARIANTE-3, bajo eval).
- **Bifurcacion PO / non-PO**: sin OC ya no es bloqueo hard. La ruta non-PO
  gobernada exige aprobador interno + centro de coste + contrato/soporte;
  si falta algo, el documento queda en la cola "pendiente de datos internos"
  (distinto de bloqueada) con la PROPUESTA del agente por reglas
  proveedor->area; el humano confirma.
- **Metodos de pago**: transferencia -> IBAN vs maestro + lote del jueves +
  gate humano. Domiciliacion -> mandato SEPA + tarea de conciliacion
  post-debito, sin lote. Tarjeta -> tarea de conciliacion contra extracto,
  sin lote. El control de fraude por IBAN aplica solo a transferencias.
- **Maestro de proveedores**: sin tax_id o razon social ambigua -> retencion
  hasta completar el alta (datos bancarios siempre con doble aprobacion humana).
- **Tratamiento de IVA** como atributo del asiento propuesto: nacional /
  intracomunitario con inversion del sujeto pasivo / no desglosado (esto
  ultimo solo posible en proformas).

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
    auth.py            # password gate server-side (AP_SYSTEM_PASSWORD; fallback temporal compatible)
    theme.py           # theming corporativo (paleta, badges, cards, tablas)
    state.py           # corrida y workflows en session_state
    views/             # inbox, detalle, excepciones, gate, auditoria, caso de negocio
data/                  # dataset + expected + doc_previews (committeados)
evals/run_evals.py     # exit 0/1 como contrato (21 grupos)
runs/                  # audit trails por corrida (gitignoreado)
docs/                  # confidencial, gitignoreado, NUNCA commitear
```

## Las 7 vistas del tablero

**El humano interviene en dos lugares**: confirma datos en *Revisión humana*,
libera dinero en *Aprobación de pagos*. Confirmar datos nunca libera un pago:
si una factura confirmada entra a un lote, ese lote pierde sus sign-offs, se
revalida por los dos checkers y vuelve al gate. Invariantes bajo eval:
INVARIANTE-4 (una non-PO sin confirmación humana de datos internos jamás
llega a un lote) e INVARIANTE-5 (confirmar datos no libera pagos).

0. **Revisión humana**: cola de trabajo de confirmaciones que no son dinero:
   (a) non-PO pendientes de datos internos, con la propuesta del agente
   (centro de coste, aprobador, con su regla) y confirmar/corregir que cambia
   el estado real; (b) anticipos/proformas con su flujo propio (aprobar
   registra quién y cuándo; pendiente de factura final visible); (c) vendor
   master incompleto. Ámbar = espera confirmación humana; rojo = bloqueada
   por control. Todo click queda en el audit trail con nombre y timestamp.

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
