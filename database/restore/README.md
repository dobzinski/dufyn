# Restore

Place one complete SQL dump in this folder and restart PostgreSQL.
`database/control.sh` uses POSTGRES_DB from the container environment (.env in
Docker Compose), renames the current database to NAME_YYYYMMDDHHMMSS (UTC),
creates an empty database with the original name and imports the SQL into it.

The SQL is deleted after success. The previous database is kept under its dated
name. If the import fails, the failed new database is removed and the previous
database gets its original name back; the SQL remains and startup stops.

The folder must allow writes/deletion by the PostgreSQL user. Restore runs before
any SQLs in database/upgrade. No control tables, catalogs or request files are used.
Keep the original PKI_MASTER_KEY and application configuration for encrypted data.
