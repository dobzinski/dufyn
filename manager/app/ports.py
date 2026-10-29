from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection
from app.inventory import PORT_COLUMNS, build_port


router = APIRouter(prefix="/api/ports", tags=["ports"])


class PortInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    target_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    port: int = Field(ge=1, le=65535)
    protocol: Literal["TCP", "UDP"] = "TCP"
    service: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_destination(self):
        if (self.asset_id is None) == (self.target_id is None):
            raise ValueError("Select exactly one asset or target")
        return self

    @field_validator("service", mode="before")
    @classmethod
    def normalize_service(cls, value):
        return value.strip() or None if isinstance(value, str) else value


def port_conflict(error):
    if isinstance(error, UniqueViolation):
        return HTTPException(409, "This port and protocol already exist for the selected destination")
    return HTTPException(422, "Selected asset or target does not exist")


@router.get("/asset-options")
def asset_options():
    with get_connection() as connection:
        return connection.execute("""
            SELECT a.id,
                concat_ws(' - ',host(a.ip),COALESCE(NULLIF(a.display_name,''),NULLIF(a.discovered_hostname,''))) AS name,
                COALESCE(n.cidr::text, 'Sem network') || ' - ' || s.name AS network_group,
                COALESCE('network:' || n.id::text, 'site:' || a.site_id::text) AS network_group_id
            FROM asset a JOIN site s ON s.id = a.site_id
            LEFT JOIN LATERAL (
                SELECT id, cidr FROM network
                WHERE site_id = a.site_id AND a.ip <<= cidr
                ORDER BY masklen(cidr) DESC, id LIMIT 1
            ) n ON TRUE
            ORDER BY s.name, n.cidr NULLS LAST, a.ip, a.id
        """).fetchall()


@router.get("/target-options")
def target_options():
    with get_connection() as connection:
        return connection.execute("""
            SELECT t.id, t.fqdn AS name, s.name AS network_group,
                'site:' || s.id::text AS network_group_id
            FROM target t JOIN site s ON s.id = t.site_id ORDER BY s.name, t.fqdn
        """).fetchall()


@router.post("", status_code=201)
def create_port(port: PortInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                INSERT INTO port AS p (asset_id, target_id, port, protocol, state, service,
                    first_seen, last_seen, last_seen_open)
                VALUES (%s, %s, %s, %s, 'C', %s, NOW(), NULL, NULL)
                RETURNING {PORT_COLUMNS}
            """, (port.asset_id, port.target_id, port.port, {"TCP": "T", "UDP": "U"}[port.protocol],
                  port.service)).fetchone()
        return build_port(row)
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise port_conflict(error) from None


@router.put("/{port_id}")
def update_port(port_id: int, port: PortInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                UPDATE port AS p SET asset_id = %s, target_id = %s, port = %s, protocol = %s,
                    service = %s WHERE p.id = %s RETURNING {PORT_COLUMNS}
            """, (port.asset_id, port.target_id, port.port, {"TCP": "T", "UDP": "U"}[port.protocol],
                  port.service, port_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Port not found")
        return build_port(row)
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise port_conflict(error) from None


@router.delete("/{port_id}")
def delete_port(port_id: int):
    with get_connection(read_only=False) as connection:
        row = connection.execute("DELETE FROM port WHERE id = %s RETURNING id", (port_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Port not found")
    return {"id": row["id"], "deleted": True}
