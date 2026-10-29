# Upgrade

Place SQLs here with zero-padded numeric prefixes, for example:

- 001_upgrade.sql
- 002_other.sql

At PostgreSQL startup, database/control.sh handles restore first, then executes
these SQLs in ascending filename order against POSTGRES_DB from the environment.
Each file is deleted after success. On an error, the file remains, following files
are not executed, and startup stops. The folder must be writable by PostgreSQL.

On a new installation without a restore, init/001_schema.sql runs first and the
upgrade SQLs follow. Author the SQLs for that sequence, using conditional/idempotent
DDL where the initialization schema may already include a change.

When adding update SQL scripts, remember to also update the init/001_schema.sql file.