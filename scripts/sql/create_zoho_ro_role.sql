-- ============================================================================
-- Rol de SOLO LECTURA para el BI externo sobre el esquema `analytics`.
--
-- Este script NO contiene secretos y no debe contenerlos nunca. La password se
-- inyecta como variable de psql en tiempo de ejecucion; el repo solo ve el
-- placeholder. Lo ejecuta un OPERADOR HUMANO con un usuario administrador.
--
-- Uso (la password la genera el operador y NUNCA se escribe en el repo):
--
--   PGPASSWORD="$ADMIN_PASSWORD" psql \
--     --set=ON_ERROR_STOP=1 \
--     -h "$INSTANCE_HOST" -U "$ADMIN_USER" -d "$DB_NAME" \
--     -v zoho_password="$(openssl rand -base64 36)" \
--     -v owner_role="$MIGRATION_ROLE" \
--     -f scripts/sql/create_zoho_ro_role.sql
--
--   zoho_password : password del rol de lectura. Generarla al vuelo y
--                   guardarla SOLO en Google Secret Manager (ver runbook).
--   owner_role    : rol con el que corren las migraciones Alembic, es decir el
--                   DUENO de las vistas de `analytics`. Necesario para que
--                   ALTER DEFAULT PRIVILEGES aplique a las vistas FUTURAS que
--                   cree ese rol (los default privileges son por rol creador:
--                   fijarlos con el admin no cubre lo que cree el otro).
--
-- Antes de ejecutar: aplicar la migracion 0006 (`alembic upgrade head`), que
-- es la que crea el esquema `analytics` y sus vistas.
--
-- Nota sobre logs: `CREATE ROLE ... PASSWORD` viaja en texto plano al servidor.
-- Si la instancia tiene `log_statement = 'ddl'` o `'all'`, la password queda en
-- los logs. Verificar el flag antes de correr esto (ver runbook).
-- ============================================================================

\set ON_ERROR_STOP on

-- --------------------------------------------------------------------------
-- 0. Precondiciones: el esquema y las vistas tienen que existir.
-- --------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'analytics') THEN
        RAISE EXCEPTION
            'No existe el esquema analytics. Correr primero: alembic upgrade head';
    END IF;
END $$;

-- --------------------------------------------------------------------------
-- 1. Rol de lectura (idempotente).
--
-- Se usa \gexec y no un bloque DO porque psql NO interpola sus variables
-- (:'zoho_password') dentro de strings dollar-quoted: adentro de un DO $$ ... $$
-- el placeholder viajaria literal y la password quedaria mal seteada.
-- --------------------------------------------------------------------------
SELECT format(
    'CREATE ROLE zoho_analytics_ro LOGIN PASSWORD %L '
    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION CONNECTION LIMIT 8',
    :'zoho_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zoho_analytics_ro')
\gexec

-- Si ya existia: se rotan la password y los atributos que Cloud SQL permite
-- administrar a un rol con CREATEROLE. No se repiten NOSUPERUSER/NOCREATEDB/
-- NOCREATEROLE/NOREPLICATION: PostgreSQL exige SUPERUSER para cambiar algunos
-- de esos flags, aunque sea para desactivarlos. La validacion siguiente corta
-- si el rol preexistente no cumple el perfil restringido.
SELECT format(
    'ALTER ROLE zoho_analytics_ro LOGIN PASSWORD %L '
    'NOINHERIT CONNECTION LIMIT 8',
    :'zoho_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zoho_analytics_ro')
\gexec

DO $$
DECLARE
    role_is_restricted boolean;
BEGIN
    SELECT NOT rolsuper
       AND NOT rolcreatedb
       AND NOT rolcreaterole
       AND NOT rolinherit
       AND NOT rolreplication
       AND rolcanlogin
       AND rolconnlimit = 8
      INTO role_is_restricted
      FROM pg_roles
     WHERE rolname = 'zoho_analytics_ro';

    IF role_is_restricted IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'zoho_analytics_ro existe con atributos incompatibles; requiere correccion por el administrador de Cloud SQL';
    END IF;
END $$;

-- --------------------------------------------------------------------------
-- 2. Quitar TODO primero. El rol arranca sin nada y solo se le da `analytics`.
-- --------------------------------------------------------------------------
REVOKE ALL ON SCHEMA public FROM zoho_analytics_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM zoho_analytics_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM zoho_analytics_ro;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM zoho_analytics_ro;

-- Cualquier otro esquema de usuario que exista hoy (excluye catalogos del
-- sistema, `public` -- ya cubierto arriba -- y el propio `analytics`).
SELECT format('REVOKE ALL ON SCHEMA %I FROM zoho_analytics_ro', nspname)
FROM pg_namespace
WHERE nspname NOT IN ('analytics', 'public', 'information_schema')
  AND nspname NOT LIKE 'pg\_%'
\gexec

SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM zoho_analytics_ro', nspname)
FROM pg_namespace
WHERE nspname NOT IN ('analytics', 'public', 'information_schema')
  AND nspname NOT LIKE 'pg\_%'
\gexec

-- Conectarse a la base: lo unico que se concede fuera de `analytics`.
SELECT format('GRANT CONNECT ON DATABASE %I TO zoho_analytics_ro', current_database())
\gexec

-- --------------------------------------------------------------------------
-- 3. Conceder SOLO lectura sobre `analytics`.
-- --------------------------------------------------------------------------
GRANT USAGE ON SCHEMA analytics TO zoho_analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO zoho_analytics_ro;

-- Las vistas llaman a las funciones tolerantes de `analytics` (fecha_iso_o_null,
-- numero_o_null, ts_o_null), asi que el rol necesita EXECUTE. PUBLIC ya lo trae
-- por defecto; se concede explicito para que la vista siga funcionando si
-- alguien endurece los permisos de PUBLIC.
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA analytics TO zoho_analytics_ro;

-- Vistas futuras: los default privileges son por rol CREADOR. Se fijan para el
-- rol que corre las migraciones (dueno de las vistas) y tambien para el usuario
-- actual, por si en algun entorno el admin es quien las crea.
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA analytics '
    'GRANT SELECT ON TABLES TO zoho_analytics_ro', :'owner_role')
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA analytics '
    'GRANT EXECUTE ON FUNCTIONS TO zoho_analytics_ro', :'owner_role')
\gexec

ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT SELECT ON TABLES TO zoho_analytics_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT EXECUTE ON FUNCTIONS TO zoho_analytics_ro;

-- Sin escritura en ningun caso, ni siquiera sobre `analytics`.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA analytics FROM zoho_analytics_ro;
REVOKE CREATE ON SCHEMA analytics FROM zoho_analytics_ro;

-- ============================================================================
-- 4. VERIFICACION. Todo lo de abajo debe pasar; si algo falla, el script corta
--    (ON_ERROR_STOP) y la conexion de Zoho NO debe configurarse.
--
--    SET ROLE requiere que el usuario actual sea superusuario o miembro del rol
--    (en PG >= 16 el creador de un rol recibe ADMIN OPTION automaticamente). Si
--    SET ROLE falla, saltear esta seccion y verificar conectandose directamente
--    con el rol (el runbook trae el comando).
-- ============================================================================
\if :{?verify_via_set_role}
\else
\set verify_via_set_role true
\endif

\if :verify_via_set_role
SET ROLE zoho_analytics_ro;

\echo '--- verificacion: identidad efectiva ---'
SELECT current_user AS usuario_efectivo;

-- 4.1 Las vistas de analytics SI se leen.
DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM analytics.v_documents;
    RAISE NOTICE 'OK: SELECT sobre analytics.v_documents permitido (% filas)', n;
END $$;

-- 4.2 Una tabla BASE no se lee. Se espera el fallo.
DO $$
BEGIN
    PERFORM 1 FROM public.trial_documents LIMIT 1;
    RAISE EXCEPTION
        'FALLO DE CONTROL: el rol pudo leer public.trial_documents (tabla base)';
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'OK: SELECT sobre tabla base denegado (%)', SQLERRM;
END $$;

-- 4.3 Otra tabla base con datos personales tampoco.
DO $$
BEGIN
    PERFORM 1 FROM public.trial_runs LIMIT 1;
    RAISE EXCEPTION
        'FALLO DE CONTROL: el rol pudo leer public.trial_runs (tabla base)';
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'OK: SELECT sobre public.trial_runs denegado';
END $$;

-- 4.4 No se escribe sobre una vista. Se espera el fallo.
--     El rechazo puede llegar por permisos (insufficient_privilege) o porque la
--     vista no es auto-actualizable al contener un CTE ("Views containing WITH
--     are not automatically updatable"). Las dos son "no escribe", asi que se
--     acepta CUALQUIER error y lo unico inaceptable es que el INSERT prospere.
--
--     El exito se marca en una variable y se levanta DESPUES del bloque que
--     atrapa: si el RAISE de la falla viviera dentro del BEGIN, el handler
--     WHEN others lo atraparia a el mismo y convertiria una falla de seguridad
--     real en un "OK" silencioso.
DO $$
DECLARE
    pudo_escribir boolean := false;
BEGIN
    BEGIN
        INSERT INTO analytics.v_documents (documento_id) VALUES ('probe-no-escribir');
        pudo_escribir := true;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'OK: INSERT sobre la vista denegado por permisos';
        WHEN others THEN
            RAISE NOTICE 'OK: INSERT sobre la vista rechazado (%: %)', SQLSTATE, SQLERRM;
    END;
    IF pudo_escribir THEN
        RAISE EXCEPTION 'FALLO DE CONTROL: el rol pudo INSERTar en analytics.v_documents';
    END IF;
END $$;

-- 4.5 No se crean objetos en `analytics`. Mismo patron que 4.4.
DO $$
DECLARE
    pudo_crear boolean := false;
BEGIN
    BEGIN
        EXECUTE 'CREATE TABLE analytics.probe_no_escribir (x integer)';
        pudo_crear := true;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'OK: CREATE TABLE en analytics denegado';
        WHEN others THEN
            RAISE NOTICE 'OK: CREATE TABLE en analytics rechazado (%: %)', SQLSTATE, SQLERRM;
    END;
    IF pudo_crear THEN
        EXECUTE 'DROP TABLE IF EXISTS analytics.probe_no_escribir';
        RAISE EXCEPTION 'FALLO DE CONTROL: el rol pudo crear una tabla en analytics';
    END IF;
END $$;

RESET ROLE;
\else
\echo '--- verificacion SET ROLE omitida; validar con conexion directa como zoho_analytics_ro ---'
\endif

-- 4.6 Resumen de privilegios efectivos. Revisar a ojo: las tres primeras
--     columnas deben dar true/false/false y ninguna tabla base debe listarse.
\echo '--- verificacion: privilegios efectivos del rol ---'
SELECT
    has_schema_privilege('zoho_analytics_ro', 'analytics', 'USAGE')   AS analytics_usage_true,
    has_schema_privilege('zoho_analytics_ro', 'analytics', 'CREATE')  AS analytics_create_false,
    has_table_privilege('zoho_analytics_ro', 'public.trial_documents', 'SELECT')
                                                                      AS lee_tabla_base_false;

-- 4.7 Hardening dependiente de version: en PostgreSQL < 15 el esquema `public`
--     trae CREATE concedido a PUBLIC, y PUBLIC lo hereda TODO rol. Si esto da
--     true, el rol puede crear objetos en `public` y hay que corregirlo a mano
--     (`REVOKE CREATE ON SCHEMA public FROM PUBLIC`). Ese REVOKE afecta a TODOS
--     los roles de la base: es una decision de infraestructura y por eso este
--     script la reporta en lugar de aplicarla.
\echo '--- verificacion: puede el rol crear objetos en public? (debe ser false) ---'
SELECT
    current_setting('server_version') AS version_postgres,
    has_schema_privilege('zoho_analytics_ro', 'public', 'CREATE') AS crea_en_public_false;

\echo '=== Rol zoho_analytics_ro listo: solo SELECT sobre el esquema analytics ==='
