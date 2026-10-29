import hashlib
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection
from app.scanner_view import configuration_rows

router = APIRouter(prefix="/api/scanners", tags=["scanners"])
COLUMNS = "id, site_id, name, enabled, last_seen, config_received_at, created_at"


class ScannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: int = Field(gt=0, le=9223372036854775807)
    name: str = Field(min_length=1, max_length=100)
    enabled: Literal["Y", "N"] = "Y"

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


def scanner_conflict(error):
    if isinstance(error, UniqueViolation):
        return HTTPException(409, "A scanner with this name already exists in the selected site")
    return HTTPException(409, "The site does not exist or the scanner has networks assigned; keep its current site")


@router.get("")
def list_scanners():
    with get_connection() as connection:
        return connection.execute("""
            SELECT sc.id, sc.site_id, s.name AS site_name, sc.name, sc.enabled,
                sc.last_seen, sc.config_received_at, sc.created_at,
                (SELECT COUNT(*) FROM scanner_network sn WHERE sn.scanner_id = sc.id) AS networks_count
            FROM scanner sc JOIN site s ON s.id = sc.site_id
            ORDER BY s.name, sc.name, sc.id
        """).fetchall()


@router.get("/{scanner_id}")
def get_scanner(scanner_id: int, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        row = connection.execute("""
            SELECT sc.id, sc.site_id, s.name AS site_name, sc.name, sc.enabled,
                sc.created_at, sc.last_seen, sc.config_received_at, sc.config_updated_at, sc.config_encrypted,
                (SELECT COUNT(*) FROM scanner_network sn WHERE sn.scanner_id = sc.id) AS networks_count
            FROM scanner sc JOIN site s ON s.id = sc.site_id WHERE sc.id = %s
        """, (scanner_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Scanner not found")
    result = dict(row)
    result["configuration"] = configuration_rows(result.pop("config_encrypted"))
    return result


@router.post("", status_code=201)
def create_scanner(scanner: ScannerInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                INSERT INTO scanner (site_id, name, enabled) VALUES (%s, %s, %s)
                RETURNING {COLUMNS}
            """, (scanner.site_id, scanner.name, scanner.enabled)).fetchone()
        return row
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise scanner_conflict(error) from None


@router.put("/{scanner_id}")
def update_scanner(scanner_id: int, scanner: ScannerInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                UPDATE scanner SET site_id = %s, name = %s, enabled = %s
                WHERE id = %s RETURNING {COLUMNS}
            """, (scanner.site_id, scanner.name, scanner.enabled, scanner_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Scanner not found")
        return row
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise scanner_conflict(error) from None


@router.delete("/{scanner_id}")
def delete_scanner(scanner_id: int):
    with get_connection(read_only=False) as connection:
        row = connection.execute("DELETE FROM scanner WHERE id = %s RETURNING id", (scanner_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Scanner not found")
    return {"id": row["id"], "deleted": True}


@router.post("/{scanner_id}/token")
def generate_scanner_token(scanner_id: int, response: Response):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_connection(read_only=False) as connection:
        scanner = connection.execute("""
            UPDATE scanner SET token_hash = %s WHERE id = %s RETURNING id
        """, (token_hash, scanner_id)).fetchone()
        if scanner is None:
            raise HTTPException(404, "Scanner not found")
    response.headers["Cache-Control"] = "no-store"
    return {"id": scanner["id"], "token": token}


class ScannerNetworksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_ids: list[int] = Field(max_length=10000)

    @field_validator("network_ids")
    @classmethod
    def validate_network_ids(cls, values):
        if any(value <= 0 or value > 9223372036854775807 for value in values):
            raise ValueError("Network IDs must be positive")
        if len(values) != len(set(values)):
            raise ValueError("Network IDs must be unique")
        return values


@router.get("/{scanner_id}/networks")
def scanner_networks(scanner_id: int):
    with get_connection() as connection:
        scanner = connection.execute("SELECT id, site_id FROM scanner WHERE id = %s", (scanner_id,)).fetchone()
        if scanner is None:
            raise HTTPException(404, "Scanner not found")
        networks = connection.execute("""
            SELECT n.id, n.name, n.cidr::text AS cidr, n.enabled,
                (sn.scanner_id IS NOT NULL) AS assigned
            FROM network n LEFT JOIN scanner_network sn
                ON sn.network_id = n.id AND sn.scanner_id = %s AND sn.site_id = n.site_id
            WHERE n.site_id = %s ORDER BY n.cidr, n.id
        """, (scanner_id, scanner["site_id"])).fetchall()
    return {"scanner_id": scanner_id, "site_id": scanner["site_id"], "networks": networks}


@router.put("/{scanner_id}/networks")
def set_scanner_networks(scanner_id: int, body: ScannerNetworksInput):
    try:
        with get_connection(read_only=False) as connection:
            scanner = connection.execute("SELECT id, site_id FROM scanner WHERE id = %s FOR UPDATE", (scanner_id,)).fetchone()
            if scanner is None:
                raise HTTPException(404, "Scanner not found")
            networks = connection.execute("""
                SELECT id FROM network WHERE site_id = %s AND id = ANY(%s::bigint[]) FOR SHARE
            """, (scanner["site_id"], body.network_ids)).fetchall()
            if {network["id"] for network in networks} != set(body.network_ids):
                raise HTTPException(422, "Select only networks belonging to this scanner's site")
            connection.execute("""
                DELETE FROM scanner_network WHERE scanner_id = %s AND NOT (network_id = ANY(%s::bigint[]))
            """, (scanner_id, body.network_ids))
            with connection.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO scanner_network (scanner_id, network_id, site_id)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """, [(scanner_id, network_id, scanner["site_id"]) for network_id in body.network_ids])
        return {"scanner_id": scanner_id, "network_ids": body.network_ids}
    except ForeignKeyViolation:
        raise HTTPException(409, "Scanner or network changed. Reload the assignments.") from None
