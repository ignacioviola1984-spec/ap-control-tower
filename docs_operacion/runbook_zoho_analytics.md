# Runbook — exposición de AP Control Tower a Zoho Analytics (EU)

Conecta Zoho Analytics (datacenter EU, `analytics.zoho.eu`) contra la base
PostgreSQL de AP Control Tower en Cloud SQL, **en modo lectura**, usando el
conector nativo *Google Cloud SQL PostgreSQL*. Sin API propia y sin cambiar el
comportamiento de nada de lo existente.

> **Este runbook lo ejecuta un operador humano.** El repositorio no contiene ni
> puede contener secretos: todos los valores sensibles van como placeholders
> (`$VARIABLE`) y se resuelven en el momento de ejecutar, desde Google Secret
> Manager o generados al vuelo. Ningún comando de acá se corre automáticamente
> en CI ni en un deploy.

**Qué entrega el PR asociado y qué no:**

| Entrega el PR | Lo hace el operador con este runbook |
|---|---|
| Migración `0006_analytics_views` (esquema `analytics` + 6 vistas) | Aplicarla a la instancia real |
| `scripts/sql/create_zoho_ro_role.sql` (rol de lectura, parametrizado) | Ejecutarlo con una password real |
| Evals de migración, enmascarado y permisos | Correrlos contra la instancia real |
| — | Todo lo de Google Cloud y todo lo de la UI de Zoho |

---

## 0. Modelo de exposición (leer antes de tocar nada)

Zoho **nunca** ve las tablas base. Solo ve el esquema `analytics`, que contiene
vistas curadas:

```
trial_runs, trial_documents        ← tablas base (Zoho NO tiene acceso)
        │
        └── analytics.v_*          ← vistas curadas (único acceso de Zoho)
                 ▲
        zoho_analytics_ro          ← rol con SELECT y nada más
```

Reglas que hacen cumplir las vistas, verificadas por `evals/test_analytics_views.py`:

- **IBAN**: sale como `****` + los 4 últimos. Nunca completo.
- **Tax IDs**: enmascarados con el mismo criterio de
  `ap_control_tower/persistence/masking.py` (3 últimos visibles).
- **Fuera de alcance**: texto libre del documento (`condiciones_pago`,
  `fecha_vencimiento_texto`), identidad del cliente (`cliente_nombre`,
  `cliente_tax_id`), notas escritas por humanos, y **nombres propios** de
  revisores y aprobadores — de ellos solo sale el **rol**.
- El enmascarado está **en la definición de la vista**, no en la aplicación: si
  algún día se escribiera un valor completo en la tabla base, la vista lo sigue
  enmascarando.

---

## 1. Precondiciones

| Necesitás | Notas |
|---|---|
| Cuenta con `roles/cloudsql.admin` en el proyecto | Para `gcloud sql instances patch` |
| Usuario administrador de la base | Para crear el rol de lectura |
| Cuenta de Zoho Analytics en el **datacenter EU** | `analytics.zoho.eu`, no `.com` |
| `psql` y `gcloud` instalados y autenticados | |

Variables que vas a usar (exportalas en tu shell; **no** las escribas en ningún
archivo del repo):

```bash
export PROJECT_ID="…"        # proyecto de Google Cloud
export INSTANCE="…"          # nombre de la instancia Cloud SQL
export REGION="us-central1"  # región actual de la instancia (ver §6, RGPD)
export DB_NAME="…"           # base de datos
export ADMIN_USER="…"        # usuario administrador de la base
export MIGRATION_ROLE="…"    # rol con el que corre Alembic (dueño de las vistas)
```

---

## 2. Lado Google Cloud

### 2.1 Aplicar la migración

Crea el esquema `analytics` y sus vistas. Es aditiva: no toca ninguna tabla ni
dato existente.

```bash
export AP_DATABASE_URL="postgresql+psycopg://${MIGRATION_ROLE}:${DB_PASSWORD}@${HOST}:5432/${DB_NAME}"
python -m alembic upgrade head
python -m alembic current      # debe mostrar 0006_analytics_views
```

> `DB_PASSWORD` y `HOST` salen de Secret Manager en el momento; no quedan en el
> historial del shell (`export HISTCONTROL=ignorespace` y prefijá con un espacio).

**Rollback:** `python -m alembic downgrade -1` borra las vistas, las funciones y
el esquema. Usa `DROP SCHEMA … RESTRICT` a propósito: si alguien dejó objetos
propios dentro de `analytics`, el downgrade **falla ruidosamente** en vez de
destruirlos en silencio. Si eso pasa, revisá qué hay ahí antes de forzar nada.

### 2.2 Crear el rol de solo lectura

Corré **después** de la migración (el script verifica que el esquema exista).

```bash
# Generá la password al vuelo y guardala SOLO en Secret Manager.
ZOHO_PW="$(openssl rand -base64 36)"

PGPASSWORD="$ADMIN_PASSWORD" psql \
  --set=ON_ERROR_STOP=1 \
  -h "$HOST" -U "$ADMIN_USER" -d "$DB_NAME" \
  -v zoho_password="$ZOHO_PW" \
  -v owner_role="$MIGRATION_ROLE" \
  -f scripts/sql/create_zoho_ro_role.sql

printf '%s' "$ZOHO_PW" | gcloud secrets create zoho-analytics-ro-password \
  --project="$PROJECT_ID" --data-file=-
unset ZOHO_PW
```

El script termina con una sección de verificación que **debe** pasar: comprueba
que el rol lee las vistas, que **no** puede leer las tablas base y que no puede
escribir ni crear objetos. Si algo de eso falla, **no configures Zoho**.

Detalles que el script resuelve y conviene conocer:

- **`owner_role` es obligatorio.** `ALTER DEFAULT PRIVILEGES` aplica solo a los
  objetos que crea el rol que lo ejecutó. Si lo corriera solo el admin, las
  vistas *futuras* creadas por Alembic no quedarían visibles para Zoho.
- **Password y logs.** `CREATE ROLE … PASSWORD` viaja en texto plano. Si la
  instancia tiene `log_statement = 'ddl'` o `'all'`, la password queda escrita
  en los logs. Verificá antes:
  ```bash
  gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
    --format="value(settings.databaseFlags)"
  ```
  Si está activo, rotá la password después (§7.1) o desactivá el flag durante la
  ejecución.
- **PostgreSQL < 15.** En esas versiones el esquema `public` trae `CREATE`
  concedido a `PUBLIC`, y `PUBLIC` lo hereda todo rol. La última query del
  script (`crea_en_public_false`) lo reporta. Si da `true`, el rol puede crear
  objetos en `public`: corregilo con `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.
  El script **no** lo hace solo porque ese REVOKE afecta a **todos** los roles de
  la base: es una decisión de infraestructura, no de este trabajo.

### 2.3 Habilitar IP pública (solo si la instancia no la tiene)

```bash
# ¿Tiene IP pública hoy?
gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
  --format="value(settings.ipConfiguration.ipv4Enabled)"

# Habilitarla
gcloud sql instances patch "$INSTANCE" --project="$PROJECT_ID" --assign-ip
```

Si preferís no exponer IP pública, saltá a §2.6 (Databridge).

### 2.4 Autorizar los rangos de Zoho Analytics EU

Los rangos **no están en este repo a propósito**: Zoho los cambia. Tomalos de la
página oficial en el momento de ejecutar:

**→ https://www.zoho.com/analytics/help/zohoanalytics-ip-address.html** —
sección del datacenter **EU (`https://analytics.zoho.eu`)**, en notación CIDR.

> ⚠️ **Al 16-07-2026 esa página anuncia rangos nuevos vigentes desde el
> 24-07-2026.** Copiá la lista del día que ejecutes y volvé a revisarla si la
> sincronización empieza a fallar sin causa aparente: un rango nuevo no
> autorizado se ve exactamente igual que un problema de red.

> ⚠️ **`--authorized-networks` REEMPLAZA la lista completa, no agrega.** Si pasás
> solo los rangos de Zoho, dejás afuera a todo lo que hoy se conecta. Listá lo
> existente primero y mandá la unión.

```bash
# 1) Lista actual (guardala)
gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
  --format="value(settings.ipConfiguration.authorizedNetworks[].value)"

# 2) Unión de lo existente + los rangos EU de Zoho (reemplazá los placeholders)
export ZOHO_EU_RANGES="<CIDR_1>,<CIDR_2>,<CIDR_N>"    # ← de la página de arriba
export EXISTING_RANGES="<lo_que_devolvió_el_paso_1>"

gcloud sql instances patch "$INSTANCE" --project="$PROJECT_ID" \
  --authorized-networks="${EXISTING_RANGES},${ZOHO_EU_RANGES}"
```

### 2.5 Forzar SSL

```bash
gcloud sql instances patch "$INSTANCE" --project="$PROJECT_ID" \
  --ssl-mode=ENCRYPTED_ONLY
```

`ENCRYPTED_ONLY` exige TLS pero no certificado de cliente. **No** uses
`TRUSTED_CLIENT_CERTIFICATE_REQUIRED`: exige certificados de cliente que el
conector nativo de Zoho no gestiona, y la conexión no va a levantar. En gcloud
viejo el flag equivalente es `--require-ssl`.

En Zoho, marcá la opción de conexión **SSL/TLS** al configurar el origen (§3.2).

### 2.6 Alternativa sin IP pública: Zoho Databridge (plan B)

Si la organización no acepta IP pública en la instancia, **Zoho Databridge** es
la salida: un agente Java que se instala en una máquina con acceso a la base y
abre una conexión **saliente** hacia Zoho. La base no necesita IP pública ni
reglas de entrada; el agente empuja los datos.

**→ https://www.zoho.com/analytics/help/import-data/databridge.html**

Costo operativo real, a decidir con los ojos abiertos:

- Hace falta una VM (o un contenedor) que lo hospede, con su parcheo, monitoreo
  y facturación. En este proyecto, donde Cloud Run escala a 0, es la única pieza
  que queda prendida siempre.
- Es un **punto único de falla**: si el agente se cae, los dashboards se quedan
  quietos y sin aviso. Hay que monitorearlo aparte.
- Suma una credencial más para custodiar (la del agente contra la base).

Recomendación: para el piloto, IP pública + rangos autorizados + SSL. Databridge
tiene sentido si la política de la organización prohíbe la IP pública o si se
mueve a una topología solo-privada.

---

## 3. Lado Zoho (con la cuenta de la organización, en `analytics.zoho.eu`)

### 3.1 Crear el Workspace

1. Entrá a **https://analytics.zoho.eu** (confirmá que el dominio es `.eu`; la
   cuenta `.com` es otro datacenter y otro juego de IPs).
2. **Create → New Workspace**. Nombre sugerido: `AP Control Tower`.

### 3.2 Conectar la base

1. **Import Data → Cloud Databases → Google Cloud SQL PostgreSQL**.
2. Completá:

   | Campo | Valor |
   |---|---|
   | Host / Server | IP pública de la instancia (Secret Manager, **nunca** el repo) |
   | Port | `5432` |
   | Database | `$DB_NAME` |
   | Username | `zoho_analytics_ro` |
   | Password | la del secreto `zoho-analytics-ro-password` |
   | SSL / TLS | **activado** (§2.5) |

3. **Seleccioná SOLO el esquema `analytics`.** Si la UI ofrece `public`, no lo
   marques: el rol igual no puede leerlo, pero elegirlo produce errores de
   permisos confusos. Importá las 6 vistas `v_*`.

### 3.3 Modo de sincronización — **importación programada cada 3 horas**

Elegí **Import / Scheduled Import**, cada **3 horas**. No uses **Live Connect**.

Motivo: Live Connect hace que cada refresco de cada dashboard dispare una query
contra la base **productiva**. Ata la latencia de los tableros a la carga del
sistema y expone la base al patrón de uso de Zoho. Con importación programada,
Zoho lee una vez y sirve todo lo demás desde su propio almacenamiento; la base
recibe una query cada 3 horas y nada más.

Los datos de AP no cambian minuto a minuto: se procesan en tandas. 3 horas es
más fresco que el ciclo real del negocio.

**Si la organización pide Live Connect igual:** en el origen de datos, *Edit
Setup → Connection Type → Live Connect*. Antes de cambiar, considerá: (a) sumar
una réplica de lectura y apuntar Zoho ahí en vez de a la primaria; (b) que el
`CONNECTION LIMIT 8` del rol es un tope pensado para importaciones periódicas —
Live Connect con varios usuarios concurrentes puede rozarlo.

### 3.4 Dashboard inicial

A construir en la UI de Zoho (no por API). Cada KPI con la vista que lo alimenta:

| # | KPI | Vista | Cómo |
|---|---|---|---|
| 1 | Documentos procesados por período | `v_documents` | Conteo de `documento_id`, eje temporal `procesado_en` (día/semana/mes) |
| 2 | % derivación a revisión humana | `v_run_metrics` | `pct_derivado_a_revision` por corrida; o desde `v_documents`: `estado_circuito = 'revision'` sobre el total |
| 3 | Aging de facturas por vencimiento | `v_documents` | Buckets sobre `fecha_vencimiento` (vencida / 0-30 / 31-60 / 61-90 / +90), midiendo `importe_total`. Filtrá `tipo_documental = 'invoice'` |
| 4 | Gasto por proveedor — top 10 | `v_documents` | Suma de `importe_total` agrupada por `proveedor`, top 10 desc. Agrupá por `proveedor_tax_id_enmascarado` cuando exista: es más estable que el nombre |
| 5 | Excepciones por tipo | `v_exceptions` | Conteo por `tipo_advertencia`, apilado por `severidad` |
| 6 | Confianza promedio por campo | `v_field_confidences` | Promedio de `confianza` agrupado por `campo`, ascendente (los peores primero) |

Notas para quien lo arme:

- `importe_total` puede venir **NULL**: es un documento cuyo importe el extractor
  no pudo leer. Es información, no un cero — no lo reemplaces por 0 en las sumas
  o vas a subestimar el gasto.
- `estado_circuito` toma: `procesado`, `revision`, `retenido`, `no_reconocido`.
- Una aprobación en `v_payment_proposals` **no es un pago**: significa que el
  documento entró a una propuesta controlada. La liberación de dinero es un gate
  humano que vive fuera de estas vistas.

---

## 4. Diccionario de vistas

Todas viven en el esquema `analytics`. Columnas explícitas, sin `SELECT *`.

**`v_documents`** — un registro por documento procesado.
`documento_id`, `run_id`, `archivo`, `tipo_documental`, `proveedor`,
`proveedor_razon_social`, `proveedor_tax_id_enmascarado`, `fecha_emision`,
`fecha_vencimiento`, `moneda`, `importe_neto`, `importe_iva`, `importe_total`,
`ruta_ap` (`po` | `non_po` | `anticipo` | `otro`), `referencia_oc`,
`estado_circuito`, `iban_ultimos4`, `engine`, `confianza_agregada`, `paginas`,
`segundos_procesamiento`, `procesado_en`, `run_creado_en`.

**`v_field_confidences`** — formato largo para análisis de calidad.
`run_id`, `documento_id`, `campo`, `confianza`.

**`v_review_queue`** — documentos derivados a revisión humana.
`documento_id`, `run_id`, `proveedor`, `motivos`, `motivos_cantidad`,
`estado_decision`, `revisor_rol`, `decidido_en`, `campos_corregidos`,
`procesado_en`.

**`v_payment_proposals`** — solo las propuestas **aprobadas**.
`documento_id`, `lote_run_id`, `proveedor`, `proveedor_tax_id_enmascarado`,
`importe`, `moneda`, `fecha_vencimiento`, `aprobado_en`, `aprobador_rol`,
`iban_ultimos4`, `run_creado_en`.

**`v_run_metrics`** — métricas por corrida.
`run_id`, `origen`, `creado_en`, `actualizado_en`, `documentos_procesados`,
`documentos_reconocidos`, `documentos_sin_error`, `errores`,
`derivados_a_revision`, `pct_derivado_a_revision`, `confianza_promedio`,
`segundos_procesamiento`.

**`v_exceptions`** — una fila por advertencia.
`run_id`, `documento_id`, `tipo_advertencia`, `severidad` (`alta`|`media`|`baja`),
`advertencia`, `procesado_en`.

> **Por qué hay campos que salen NULL.** El JSON del extractor es texto libre:
> un campo de fecha puede traer `"al inicio del estudio"` y uno de importe
> `"N/A"`. Las vistas convierten con funciones tolerantes
> (`analytics.fecha_iso_o_null`, `numero_o_null`, `ts_o_null`): lo que no es un
> valor válido sale **NULL**. Sin eso, un solo documento con basura haría fallar
> la consulta entera y **cortaría la sincronización de Zoho** — no es teórico:
> PostgreSQL 16 lanza `DatetimeFieldOverflow` ante una fecha como `2026-02-30`.

---

## 5. Verificación

```bash
# Evals del entregable, contra la instancia real (o el Postgres de dev)
export AP_TEST_DATABASE_URL="postgresql+psycopg://${ADMIN_USER}:${ADMIN_PASSWORD}@${HOST}:5432/${DB_NAME}"
python evals/test_analytics_views.py        # exit 0 = verde
```

Cubre: migración (upgrade / downgrade / re-upgrade), columnas exactas de las 6
vistas, enmascarado de IBAN y tax_id **insertando valores completos directo en
las tablas base**, ausencia de campos vetados, casts defensivos y permisos del
rol de lectura.

Comprobación manual rápida, conectado **como `zoho_analytics_ro`**:

```sql
SELECT count(*) FROM analytics.v_documents;      -- debe funcionar
SELECT 1 FROM public.trial_documents LIMIT 1;    -- debe dar: permission denied
```

### Troubleshooting

| Síntoma | Causa probable |
|---|---|
| Zoho no conecta / timeout | Rangos EU desactualizados (§2.4) o `--authorized-networks` pisó la lista |
| `permission denied for schema public` | Se marcó `public` al importar (§3.3). Importá solo `analytics` |
| `no pg_hba.conf entry … no encryption` | Falta activar SSL/TLS del lado Zoho (§2.5) |
| Una vista trae 0 filas | Normal si no hay corridas persistidas todavía (`trial_runs` vacía) |
| Faltan vistas nuevas tras un deploy | `ALTER DEFAULT PRIVILEGES` se fijó para otro `owner_role` (§2.2). Volvé a correr el script |

---

## 6. Nota RGPD — residencia de los datos

**Situación actual (piloto):** la instancia Cloud SQL está en **`us-central1`
(Estados Unidos)** y Zoho Analytics está en el datacenter **EU**. Cada
importación programada mueve datos **desde EEUU hacia la UE**. Los datos que se
sincronizan están minimizados (sin IBAN ni tax IDs completos, sin texto libre de
documentos, sin nombres de personas), pero incluyen datos de proveedores e
importes: hay tratamiento de datos y una transferencia internacional detrás.

**Recomendación para pasar de piloto a producción: mover la instancia (o una
réplica de lectura) a una región europea** (`europe-west1` o
`europe-southwest1` — esta última, Madrid, es la de menor latencia si la
operación es española). Así el dato en reposo queda en la UE y la sincronización
a Zoho EU deja de ser transferencia internacional.

Procedimiento de alto nivel (**fuera del alcance de este PR — solo documentado**):

1. **Decidir la topología.** Réplica de lectura en la UE apuntando Zoho ahí es
   lo menos disruptivo: la primaria no se toca y el analítico deja de pegarle.
   No resuelve la residencia del dato primario. Migrar la primaria sí, pero pide
   ventana de mantenimiento.
2. **Réplica de lectura entre regiones.** Crear la réplica en la región europea,
   esperar a que alcance a la primaria, apuntar Zoho al endpoint de la réplica.
   El esquema `analytics` y el rol se replican solos (la réplica es física).
3. **Migración completa de la primaria.** Cloud SQL no cambia de región en
   caliente: se hace con Database Migration Service o con dump/restore sobre una
   instancia nueva, con ventana de mantenimiento y actualizando la
   `AP_DATABASE_URL` de Cloud Run. Ensayar antes en una copia.
4. **Revisar lo que arrastra la región:** backups, PITR y read replicas heredan
   ubicación. Verificar que ninguno quede en EEUU.
5. **Cerrar el círculo documental:** actualizar el registro de actividades de
   tratamiento y el DPA con Zoho; si la primaria queda en EEUU, la transferencia
   necesita su base legal explícita.

Los PDFs originales se descartan tras procesar: lo que persiste (y lo único que
puede llegar a Zoho) son extracciones estructuradas, métricas y decisiones.

---

## 7. Operación

### 7.1 Rotar la password del rol

```bash
ZOHO_PW="$(openssl rand -base64 36)"
PGPASSWORD="$ADMIN_PASSWORD" psql --set=ON_ERROR_STOP=1 \
  -h "$HOST" -U "$ADMIN_USER" -d "$DB_NAME" \
  -v zoho_password="$ZOHO_PW" -v owner_role="$MIGRATION_ROLE" \
  -f scripts/sql/create_zoho_ro_role.sql       # idempotente: si existe, rota

printf '%s' "$ZOHO_PW" | gcloud secrets versions add zoho-analytics-ro-password \
  --project="$PROJECT_ID" --data-file=-
unset ZOHO_PW
```

Después, actualizá la password en Zoho: *Data Sources → (el origen) → Edit Setup*.
Hasta que lo hagas, las importaciones fallan.

### 7.2 Dar de baja el rol

`DROP ROLE` falla mientras el rol conserve privilegios
(`DependentObjectsStillExist`, incluidos los `EXECUTE` sobre las funciones de
`analytics`). El orden correcto:

```sql
DROP OWNED BY zoho_analytics_ro;   -- revoca TODO lo concedido en esta base
DROP ROLE zoho_analytics_ro;
```

### 7.3 Cortar el acceso ya (incidente)

De más rápido a más definitivo:

1. **Bloquear el login** (deja el rol y los grants intactos, reversible):
   ```sql
   ALTER ROLE zoho_analytics_ro NOLOGIN;
   ```
2. **Sacar los rangos de Zoho** de `--authorized-networks` (§2.4, acordate de
   mandar la lista completa sin ellos).
3. **Bajar el esquema entero**: `python -m alembic downgrade -1`.

Ninguna de las tres toca la aplicación: la demo y el pipeline no dependen de
`analytics`.
