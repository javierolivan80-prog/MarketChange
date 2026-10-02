-- dashboard_readonly_role.sql — rol de SOLO LECTURA para el panel (app/).
--
-- El panel no escribe nunca en la base (ver app/lib/db.ts), pero hasta ahora
-- usaba la misma DATABASE_URL que el pipeline, con permisos de escritura: un
-- fallo o una vulnerabilidad en el panel podía modificar o borrar datos.
-- Con este rol, lo peor que puede hacer el panel es leer.
--
-- Uso (una vez, con la URL de administración del pipeline):
--
--   psql "$DATABASE_URL" -v ro_password="'una-contraseña-larga'" -f pipeline/db/dashboard_readonly_role.sql
--
-- y en Vercel, DATABASE_URL = la misma URL cambiando usuario y contraseña por
-- dashboard_ro / esa contraseña. Idempotente: se puede volver a ejecutar
-- (por ejemplo, tras añadir tablas nuevas) sin error.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_ro') THEN
    CREATE ROLE dashboard_ro LOGIN;
  END IF;
END
$$;

ALTER ROLE dashboard_ro WITH LOGIN PASSWORD :ro_password;
-- Defensa adicional: aunque alguien le diera permisos de escritura por error,
-- las transacciones de este rol nacen en solo lectura.
ALTER ROLE dashboard_ro SET default_transaction_read_only = on;
-- Un panel que se cuelga no debe retener conexiones del plan gratuito.
ALTER ROLE dashboard_ro SET statement_timeout = '15s';

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO dashboard_ro', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dashboard_ro;
