import os

import psycopg
from psycopg.rows import dict_row


def database_credentials():
    """Allow a staged migration from the bootstrap administrator to an application role."""
    user = os.getenv("POSTGRES_APP_USER", "").strip()
    password = os.getenv("POSTGRES_APP_PASSWORD", "")
    if bool(user) != bool(password):
        raise psycopg.OperationalError("Configure both application database credentials")
    if user:
        return user, password
    return os.getenv("POSTGRES_USER", "dufyn"), os.getenv("POSTGRES_PASSWORD", "")


def get_connection(*, read_only: bool = True):
    user, password = database_credentials()
    if not password:
        raise psycopg.OperationalError("Database credentials are not configured")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "dufyn"),
        user=user,
        password=password,
        connect_timeout=5,
        # Decode text/CHAR fields as str even when the server defaults to SQL_ASCII.
        client_encoding="UTF8",
        options=f"-c default_transaction_read_only={'on' if read_only else 'off'} -c statement_timeout=10000",
        row_factory=dict_row,
    )
