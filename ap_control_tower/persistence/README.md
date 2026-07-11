# Capa de persistencia (Fase 1)

Persistencia **opcional y aditiva** para AP Control Tower. Sin
`AP_DATABASE_URL` el sistema se comporta EXACTAMENTE como la demo actual
(estado en `session_state` + dataset sintetico). El motor (`engine/`) no
importa este paquete: sigue siendo puro y solo-stdlib. SQLAlchemy y Alembic
viven unicamente aca.

## Que hay

| Modulo | Rol |
|---|---|
| `config.py` | Resuelve la URL de base SOLO desde entorno (`AP_DATABASE_URL`/`DATABASE_URL`). Nunca credenciales en repo. |
| `models_sql.py` | Modelo relacional (documentos, facturas, proveedores + historial bancario, OC/lineas, controles, excepciones, revision humana, lotes, aprobaciones, pagos, auditoria encadenada). |
| `session.py` | Fabrica de engine/sesion; fuerza FK en SQLite; `session_scope` transaccional. |
| `repositories.py` | Round-trip motor→base (`persist_run`) idempotente + lecturas con datos bancarios enmascarados. |
| `masking.py` | Enmascarado de IBAN / cuenta / tax_id para UI, logs y respuestas. |

Migraciones versionadas en `migrations/` (Alembic). El modelo se hace cumplir
con: id de documento unico, indice parcial de unicidad de factura fiscal entre
facturas activas (los duplicados que C2 bloquea coexisten), FKs contra
huerfanos, CHECK de estados, una factura en un solo lote, y auditoria
append-only con revalidacion de la cadena de hash antes de insertar.

## Puesta en marcha (dev, WSL)

```bash
pip install -r requirements-persistence.txt

# Postgres local aislado (no es la imagen de la demo)
docker compose -f docker-compose.dev.yml up -d

export AP_DATABASE_URL="postgresql+psycopg://ap:ap_dev_local@localhost:5432/ap_control_tower"
python -m alembic upgrade head          # crea el esquema (base vacia o existente)
```

Alternativa sin Docker (portable, dev/tests): `AP_DATABASE_URL=sqlite+pysqlite:///./ap_local.db`.

## Persistir una corrida del motor

```python
from ap_control_tower.engine.pipeline import run_month
from ap_control_tower.models import load_dataset
from ap_control_tower.persistence.session import build_engine, session_scope
from ap_control_tower.persistence.repositories import persist_run

dataset = load_dataset("data/synthetic_month.json")
result, audit, ctx = run_month(dataset)
engine = build_engine()                 # usa AP_DATABASE_URL
with session_scope(engine) as s:
    print(persist_run(s, dataset, result, audit))   # idempotente
```

## Operacion, rollback y recuperacion

- **Aplicar migraciones:** `python -m alembic upgrade head`. No destructivo
  (usa `checkfirst`): correr sobre una base ya poblada no borra ni recrea.
- **Ver version aplicada:** `python -m alembic current`.
- **Rollback de la ultima migracion:** `python -m alembic downgrade -1`.
  `downgrade base` deja solo `alembic_version` (esquema vacio) — usar solo en
  dev; en datos reales evaluar impacto antes.
- **Recuperacion de la demo:** ante cualquier problema con la base, borrar
  `AP_DATABASE_URL` del entorno: la app vuelve al comportamiento actual sin
  persistencia. La demo desplegada no depende de esta capa.
- **Reset del Postgres local:** `docker compose -f docker-compose.dev.yml down -v`
  (borra el volumen) y volver a `up -d` + `alembic upgrade head`.
- **Integridad de auditoria:** `repositories.verify_persisted_chain(session, run_id)`
  revalida la cadena de hash desde la base.

## Verificacion

```bash
python evals/test_persistence.py        # round-trip + migraciones + restricciones (exit 0/1)
```

Corre sobre SQLite temporal por defecto; con `AP_TEST_DATABASE_URL` apunta a
Postgres. Hace SKIP (exit 0) si SQLAlchemy no esta instalado.
