#!/usr/bin/env bash
# On startup: initialize when needed, restore if supplied, then apply ordered upgrades.
set -Eeo pipefail
export LC_ALL=C
source /usr/local/bin/docker-entrypoint.sh

if [ "$#" -eq 0 ]; then set -- postgres; fi
if [ "${1:0:1}" = '-' ]; then set -- postgres "$@"; fi
if [ "$1" != postgres ] || _pg_want_help "$@"; then exec "$@"; fi

docker_setup_env
docker_create_db_directories
if [ "$(id -u)" = 0 ]; then
    exec gosu postgres bash "${BASH_SOURCE[0]}" "$@"
fi

database_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
restore_dir="$database_dir/restore"
upgrade_dir="$database_dir/upgrade"
# Never silently run applications as administrator on partial app credentials.
if { [ -n "${POSTGRES_APP_USER:-}" ] && [ -z "${POSTGRES_APP_PASSWORD:-}" ]; } ||
   { [ -z "${POSTGRES_APP_USER:-}" ] && [ -n "${POSTGRES_APP_PASSWORD:-}" ]; }; then
    echo 'DUFYN: configure both POSTGRES_APP_USER and POSTGRES_APP_PASSWORD' >&2
    exit 1
fi
shopt -s nullglob
restores=("$restore_dir"/*.sql)
upgrades=("$upgrade_dir"/*.sql)
shopt -u nullglob
if [ "${#restores[@]}" -gt 1 ]; then
    echo 'DUFYN: leave only one SQL file in database/restore' >&2
    exit 1
fi
for file in "${restores[@]}" "${upgrades[@]}"; do
    if [ ! -f "$file" ] || [ -L "$file" ] || [ ! -s "$file" ] || [ ! -w "$(dirname -- "$file")" ]; then
        echo "DUFYN: SQL must be a nonempty regular file in a writable directory: $file" >&2
        exit 1
    fi
done

# With an existing database and no SQLs, use the normal PostgreSQL startup.
if [ -n "$DATABASE_ALREADY_EXISTS" ] && [ "${#restores[@]}" -eq 0 ] && [ "${#upgrades[@]}" -eq 0 ] && [ -z "${POSTGRES_APP_USER:-}" ]; then
    unset "${!POSTGRES_@}"
    exec "$@"
fi
if [ -z "$DATABASE_ALREADY_EXISTS" ]; then
    docker_verify_minimum_env
    if declare -F docker_error_old_databases >/dev/null; then docker_error_old_databases; fi
    docker_init_database_dir
    pg_setup_hba_conf "$@"
fi
export PGPASSWORD="${PGPASSWORD:-$POSTGRES_PASSWORD}"
unset PGSERVICE PGSERVICEFILE PGHOSTADDR PGOPTIONS
export PGHOST=/var/run/postgresql
export PGPORT="${PGPORT:-5432}"
# Keep application/scanner connections out until the SQLs finish.
docker_temp_server_start "$@"
trap 'docker_temp_server_stop >/dev/null 2>&1 || true' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
if [ -z "$DATABASE_ALREADY_EXISTS" ]; then
    docker_setup_db
    if [ "${#restores[@]}" -eq 0 ]; then
        docker_process_init_files /docker-entrypoint-initdb.d/*
    fi
fi
psql_base=(psql -X --no-password --set ON_ERROR_STOP=1 --username "$POSTGRES_USER")

# Restore: preserve the current database under a dated name and import into its old name.
if [ "${#restores[@]}" -eq 1 ]; then
    if [ "$POSTGRES_DB" = template0 ] || [ "$POSTGRES_DB" = template1 ]; then
        echo 'DUFYN: template databases cannot be the restore destination' >&2
        exit 1
    fi
    maintenance=postgres
    if [ "$POSTGRES_DB" = postgres ]; then maintenance=template1; fi
    archive="${POSTGRES_DB}_$(date -u +%Y%m%d%H%M%S)"
    admin=("${psql_base[@]}" --dbname "$maintenance" --set "database=$POSTGRES_DB" --set "archive=$archive" --set "owner=$POSTGRES_USER")
    echo "DUFYN: preserving $POSTGRES_DB as $archive"
    "${admin[@]}" <<'SQL'
ALTER DATABASE :"database" RENAME TO :"archive";
CREATE DATABASE :"database" OWNER :"owner" TEMPLATE template0;
SQL
    echo "DUFYN: restoring ${restores[0]} into $POSTGRES_DB"
    if ! "${psql_base[@]}" --dbname "$POSTGRES_DB" --single-transaction --file "${restores[0]}"; then
        # The failed import is not the original database. Put the original name back.
        "${admin[@]}" <<'SQL'
DROP DATABASE :"database" WITH (FORCE);
ALTER DATABASE :"archive" RENAME TO :"database";
SQL
        echo 'DUFYN: restore failed; original database restored and SQL retained' >&2
        exit 1
    fi
    rm -- "${restores[0]}"
    echo "DUFYN: restore completed; previous database kept as $archive"
fi

# Upgrade: filename order (001_..., 002_..., ...), with no history or catalog.
for file in "${upgrades[@]}"; do
    echo "DUFYN: applying $(basename -- "$file") to $POSTGRES_DB"
    "${psql_base[@]}" --dbname "$POSTGRES_DB" --single-transaction --file "$file"
    rm -- "$file"
done

# Configure the restricted account after init/restore/upgrade, including its grants.
# No extra startup script, catalog or control table is needed.
if [ -n "${POSTGRES_APP_USER:-}" ]; then
    echo 'DUFYN: configuring restricted application database account'
    "${psql_base[@]}" --dbname "$POSTGRES_DB" <<'SQL'
\set ON_ERROR_STOP on
\set app_user ''
\set app_password ''
\getenv app_user POSTGRES_APP_USER
\getenv app_password POSTGRES_APP_PASSWORD
SELECT :'app_user' ~ '^[a-z_][a-z0-9_]{0,62}$'
       AND :'app_user' <> current_user
       AND octet_length(:'app_password') >= 24
       AND left(:'app_password', 13) <> 'REPLACE_WITH_' AS valid_input \gset
\if :valid_input
\else
    DO $dufyn$ BEGIN
        RAISE EXCEPTION 'Set a distinct lowercase application username and a password of at least 24 bytes.';
    END $dufyn$;
\endif
BEGIN;
SELECT EXISTS (
    SELECT 1 FROM pg_roles r
    WHERE r.rolname = :'app_user'
      AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
           OR r.rolbypassrls OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid))
) AS unsafe_role \gset
\if :unsafe_role
    DO $dufyn$ BEGIN
        RAISE EXCEPTION 'Refusing to reuse a privileged role. Choose a new application username.';
    END $dufyn$;
\endif
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user') \gexec
GRANT USAGE ON SCHEMA public TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
COMMIT;
SQL
fi

docker_temp_server_stop
trap - EXIT TERM INT
unset PGPASSWORD
unset "${!POSTGRES_@}"
exec "$@"
