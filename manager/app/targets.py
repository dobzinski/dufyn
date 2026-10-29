import re
from ipaddress import ip_address
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection

router = APIRouter(prefix="/api/targets", tags=["targets"])
COLUMNS = "id, site_id, fqdn, status, latency_ms, last_seen, created_at"


class TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: int = Field(gt=0, le=9223372036854775807)
    status: Literal["A", "I", "U"] = "U"
    fqdn: str = Field(min_length=1, max_length=253)

    @field_validator("fqdn", mode="before")
    @classmethod
    def normalize_fqdn(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip().rstrip(".").lower()
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("Enter a valid FQDN") from None
        return value

    @field_validator("fqdn")
    @classmethod
    def validate_fqdn(cls, value):
        labels = value.split(".")
        if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            raise ValueError("Enter a FQDN without protocol, port or path")
        try:
            ip_address(value)
        except ValueError:
            return value
        raise ValueError("Enter a FQDN instead of an IP address")


def target_conflict(error):
    if isinstance(error, UniqueViolation):
        return HTTPException(409, "This FQDN already exists in the selected site")
    return HTTPException(422, "Selected site does not exist")


@router.get("")
def list_targets():
    with get_connection() as connection:
        return connection.execute("""
            SELECT t.id, t.site_id, s.name AS site_name, t.fqdn,
                t.status, t.latency_ms, t.last_seen, t.created_at,
                (SELECT COUNT(*) FROM target_port_catalog c WHERE c.target_id = t.id) AS ports_count
            FROM target t JOIN site s ON s.id = t.site_id
            ORDER BY t.fqdn, t.id
        """).fetchall()


@router.post("", status_code=201)
def create_target(target: TargetInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                INSERT INTO target (site_id, fqdn, status) VALUES (%s, %s, %s)
                RETURNING {COLUMNS}
            """, (target.site_id, target.fqdn, "U")).fetchone()
            connection.execute("""
                INSERT INTO target_port_catalog (target_id, port_catalog_id)
                SELECT %s, id
                FROM port_catalog WHERE protocol = 'T' AND port IN (80, 443)
            """, (row["id"],))
        return row
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise target_conflict(error) from None


@router.put("/{target_id}")
def update_target(target_id: int, target: TargetInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                UPDATE target SET site_id = %s, fqdn = %s, status = %s WHERE id = %s
                RETURNING {COLUMNS}
            """, (target.site_id, target.fqdn, target.status, target_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Target not found")
        return row
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise target_conflict(error) from None


@router.delete("/{target_id}")
def delete_target(target_id: int):
    with get_connection(read_only=False) as connection:
        row = connection.execute("DELETE FROM target WHERE id = %s RETURNING id", (target_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Target not found")
    return {"id": row["id"], "deleted": True}


@router.get("/results")
def target_results():
    with get_connection() as connection:
        return connection.execute("""
            SELECT p.id, t.site_id, s.name AS site_name, t.fqdn, t.status AS target_status,
                p.port, p.protocol, p.check_protocol, p.state, p.last_http_status,
                p.response_time_ms, p.first_seen, p.last_seen
            FROM port p JOIN target t ON t.id = p.target_id JOIN site s ON s.id = t.site_id
            WHERE p.last_seen IS NOT NULL AND p.check_protocol IS NOT NULL
            ORDER BY s.name, t.fqdn, p.port, p.protocol
        """).fetchall()
