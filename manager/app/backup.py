"""Manual ZIP exports. No archives are retained after the download."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from datetime import datetime
from threading import Lock
from typing import Literal
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from app.config import CONFIG_FILE, load_version
from app.database import get_connection, database_credentials
from app.passwords import cipher

router = APIRouter(prefix="/api/backup", tags=["backup"])
backup_lock = Lock()
ENV_FILE = Path(os.getenv("BACKUP_ENV_FILE", "/opt/dufyn/backup-source/.env"))
ENVIRONMENT_KEYS = (
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "MANAGER_LOGIN_USER", "MANAGER_LOGIN_PASSWORD", "PKI_MASTER_KEY",
    "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD",
)


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal["all", "configuration", "data"]
    timezone: str = Field(default="UTC", min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA time zone") from None
        return value


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, default=lambda item: item.isoformat()) + "\n").encode("utf-8")


def dump_database(destination, snapshot):
    executable = shutil.which("pg_dump")
    if not executable:
        raise HTTPException(503, "PostgreSQL backup client is unavailable. Update the Manager installation.")
    user, password = database_credentials()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update({
        "PGHOST": os.getenv("POSTGRES_HOST", "postgres"),
        "PGPORT": os.getenv("POSTGRES_PORT", "5432"),
        "PGDATABASE": os.getenv("POSTGRES_DB", "dufyn"),
        "PGUSER": user,
        "PGPASSWORD": password,
        "PGCONNECT_TIMEOUT": "10", "PGAPPNAME": "dufyn-backup",
    })
    try:
        result = subprocess.run([
            executable, "--format=plain", "--encoding=UTF8", "--no-owner", "--no-privileges",
            "--no-password", "--lock-wait-timeout=10s", "--snapshot=" + snapshot,
            "--file=" + str(destination),
        ], env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        raise HTTPException(503, "Database backup exceeded the 10-minute limit. Try again outside busy periods.") from None
    except OSError:
        raise HTTPException(503, "Unable to start the PostgreSQL backup client.") from None
    if result.returncode != 0 or not destination.is_file() or not destination.stat().st_size:
        # Client stderr can contain connection details. Never send it to the browser.
        raise HTTPException(503, "Unable to create the database dump. Check database access and PostgreSQL client compatibility.")


def configuration_files(connection):
    try:
        original = CONFIG_FILE.read_bytes()
        environment_file = ENV_FILE.read_bytes() if ENV_FILE.is_file() else None
    except OSError:
        raise HTTPException(503, "Unable to read the configuration files for backup.") from None
    files = {
        "configuration/manager/config.yaml": original,
        # Both supported deployments mount Manager config.yaml into the Controller.
        "configuration/controller/config.yaml": original,
        "configuration/environment.json": json_bytes({key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}),
    }
    if environment_file is not None:
        files["configuration/.env"] = environment_file
    scanners = connection.execute("""
        SELECT sc.id, sc.name, sc.site_id, s.name AS site_name, sc.enabled,
            sc.config_received_at, sc.config_updated_at, sc.config_encrypted
        FROM scanner sc JOIN site s ON s.id=sc.site_id ORDER BY sc.id
    """).fetchall()
    index = []
    missing = []
    decoder = None
    for scanner in scanners:
        metadata = {key: value for key, value in scanner.items() if key != "config_encrypted"}
        encrypted = scanner["config_encrypted"]
        metadata["included"] = encrypted is not None
        if encrypted is None:
            missing.append(scanner["id"])
        else:
            if decoder is None:
                decoder = cipher()
            try:
                settings = json.loads(decoder.decrypt(bytes(encrypted)).decode("utf-8"))
                if not isinstance(settings, dict):
                    raise ValueError()
            except (InvalidToken, ValueError, UnicodeError, TypeError, RecursionError):
                raise HTTPException(503, "A scanner configuration cannot be decrypted. Verify the original PKI_MASTER_KEY before retrying.") from None
            # Authentication tokens are intentionally not recovered from snapshots.
            controller = settings.get("controller")
            if isinstance(controller, dict):
                controller.pop("token", None)
            files[f"configuration/scanners/scanner-{scanner['id']}.json"] = json_bytes({"scanner": metadata, "config": settings})
        index.append(metadata)
    files["configuration/scanners/index.json"] = json_bytes(index)
    return files, {"scanners_without_configuration": missing, "original_env_included": environment_file is not None,
                   "environment_keys_included": [key for key in ENVIRONMENT_KEYS if key in os.environ]}


RESTORE_GUIDE = """DUFYN manual backup

The ZIP is not password protected. Configuration exports include credentials and
scanner DNS secrets. Keep this archive private.

Contents depend on the selected option. manifest.json lists the option, timestamp,
application/database versions, file hashes and scanners without a saved configuration.
A missing scanner snapshot cannot be recovered from this backup.

Configuration:
- Restore configuration/manager/config.yaml to manager/config.yaml. The supported
  deployments also mount this file into the Controller.
- configuration/.env, when present, is the original Compose input. Review
  configuration/environment.json for the effective Manager environment (shell
  overrides may differ from the original .env). Kubernetes uses these values in
  its Secrets/environment entries, rather than reading Compose .env.
- Preserve the exact original PKI_MASTER_KEY to open encrypted passwords,
  private certificate keys and scanner snapshots stored in the database.
- configuration/scanners/scanner-ID.json contains the last reported effective
  settings for that remote scanner, not a live read of its disk. Use the config
  object as a guide to reconstruct its config.yaml. Tokens are not exported from
  scanner snapshots: retain an existing token or issue a new one after recovery.

Data:
- database/restore.sql is a complete PostgreSQL schema+data dump without
  owners/privileges. Preserve your original configuration and PKI_MASTER_KEY.
- With the updated deployment, put this SQL in database/restore/ and restart
  PostgreSQL. Leave only one complete SQL file there. No extra request is needed.
- database/control.sh uses POSTGRES_DB from the environment, renames the current
  database to NAME_YYYYMMDDHHMMSS (UTC), creates an empty database with the original
  name, and imports the SQL. The old database is retained under the dated name.
- After success, the SQL is deleted. On import failure, the failed replacement is
  removed, the original database name is restored and the SQL is kept.
- Then control.sh executes SQLs in database/upgrade/ in ascending filename order
  (001_name.sql, 002_name.sql), deleting each after success and stopping on error.
- There are no control tables or execution history. A file copied back into a
  queue executes again. Both folders must be writable by the PostgreSQL user.
- Alternatively import the dump manually into an empty database with:
  psql -X --set ON_ERROR_STOP=1 --single-transaction -U USER -d DATABASE -f restore.sql
- Restore configurations/keys before resuming application and scanner operation.

A Data-only ZIP does not contain environment/configuration files or master keys.
Use a corresponding Configuration backup or your separately retained configuration.
"""


@router.post("")
def create_backup(body: BackupRequest):
    if not backup_lock.acquire(blocking=False):
        raise HTTPException(409, "Another backup is being generated. Wait for it to finish.")
    directory = None
    try:
        directory = tempfile.TemporaryDirectory(prefix="dufyn-backup-")
        folder = Path(directory.name)
        now = datetime.now(ZoneInfo(body.timezone))
        filename = f"dufyn-backup-{body.option}-{now:%Y%m%d%H%M%S}.zip"
        manifest = {"format_version": 1, "option": body.option, "created_at": now.isoformat(),
                    "timezone": body.timezone, "application_version": load_version()}
        files = {"README.txt": RESTORE_GUIDE.encode("utf-8")}
        with get_connection() as connection:
            # Scanner snapshots and the dump share one consistent database snapshot.
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            database = connection.execute("SELECT current_setting('server_version') AS version, pg_export_snapshot() AS snapshot").fetchone()
            manifest["postgresql_version"] = database["version"]
            if body.option in ("all", "configuration"):
                configuration, summary = configuration_files(connection)
                files.update(configuration)
                manifest["configuration"] = summary
            if body.option in ("all", "data"):
                sql = folder / "restore.sql"
                dump_database(sql, database["snapshot"])
                files["database/restore.sql"] = sql
        entries = []
        with ZipFile(folder / filename, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for name, content in files.items():
                if isinstance(content, Path):
                    with content.open("rb") as source:
                        checksum = hashlib.file_digest(source, "sha256").hexdigest()
                    size = content.stat().st_size
                    archive.write(content, name)
                else:
                    checksum = hashlib.sha256(content).hexdigest()
                    size = len(content)
                    archive.writestr(name, content)
                entries.append({"path": name, "bytes": size, "sha256": checksum})
            manifest["files"] = entries
            archive.writestr("manifest.json", json_bytes(manifest))
        missing = len(manifest.get("configuration", {}).get("scanners_without_configuration", []))
        return FileResponse(folder / filename, media_type="application/zip", filename=filename,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Backup-Missing-Scanners": str(missing)},
            background=BackgroundTask(directory.cleanup))
    except BaseException:
        if directory is not None:
            directory.cleanup()
        raise
    finally:
        backup_lock.release()
