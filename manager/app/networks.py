from ipaddress import IPv4Network
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection

router = APIRouter(prefix="/api/networks", tags=["networks"])
COLUMNS = "id, site_id, cidr::text AS cidr, name, enabled, port_mode, ptr_enabled, created_at"


class NetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: int = Field(gt=0, le=9223372036854775807)
    cidr: str
    name: str | None = Field(default=None, max_length=100)
    enabled: Literal["Y", "N"] = "Y"
    port_mode: Literal["I", "C"] = "I"
    ptr_enabled: Literal["Y", "N"] = "N"

    @field_validator("name", "cidr", mode="before")
    @classmethod
    def trim_fields(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        return value or None

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value):
        if "/" not in value:
            raise ValueError("Enter an IPv4 CIDR with a prefix, for example 192.168.1.0/24")
        try:
            return str(IPv4Network(value, strict=True))
        except ValueError:
            raise ValueError("Enter a valid IPv4 network CIDR without host bits") from None


@router.get("")
def list_networks():
    with get_connection() as connection:
        return connection.execute("""
            SELECT n.id, n.site_id, s.name AS site_name, n.cidr::text AS cidr,
                n.name, n.enabled, n.port_mode, n.ptr_enabled, n.created_at,
                CASE WHEN n.port_mode = 'I'
                    THEN (SELECT COUNT(*) FROM site_port_catalog p WHERE p.site_id = n.site_id)
                    ELSE (SELECT COUNT(*) FROM network_port_catalog p WHERE p.network_id = n.id)
                END AS ports_count
            FROM network n JOIN site s ON s.id = n.site_id
            ORDER BY s.name, n.cidr, n.id
        """).fetchall()


@router.post("", status_code=201)
def create_network(network: NetworkInput):
    try:
        with get_connection(read_only=False) as connection:
            result = connection.execute(f"""
                INSERT INTO network (site_id, cidr, name, enabled, port_mode, ptr_enabled)
                VALUES (%s, %s::cidr, %s, %s, %s, %s) RETURNING {COLUMNS}
            """, (network.site_id, network.cidr, network.name, network.enabled, network.port_mode, network.ptr_enabled)).fetchone()
        return result
    except UniqueViolation:
        raise HTTPException(409, "This network CIDR already exists in the selected site") from None
    except ForeignKeyViolation:
        raise HTTPException(409, "The selected site no longer exists") from None


@router.put("/{network_id}")
def update_network(network_id: int, network: NetworkInput):
    try:
        with get_connection(read_only=False) as connection:
            result = connection.execute(f"""
                UPDATE network SET site_id = %s, cidr = %s::cidr, name = %s,
                    enabled = %s, port_mode = %s, ptr_enabled = %s WHERE id = %s RETURNING {COLUMNS}
            """, (network.site_id, network.cidr, network.name, network.enabled, network.port_mode, network.ptr_enabled, network_id)).fetchone()
            if result is None:
                raise HTTPException(404, "Network not found")
        return result
    except UniqueViolation:
        raise HTTPException(409, "This network CIDR already exists in the selected site") from None
    except ForeignKeyViolation:
        raise HTTPException(409, "The site does not exist or this network is assigned to scanners; keep its current site") from None


@router.delete("/{network_id}")
def delete_network(network_id: int):
    with get_connection(read_only=False) as connection:
        result = connection.execute("DELETE FROM network WHERE id = %s RETURNING id", (network_id,)).fetchone()
        if result is None:
            raise HTTPException(404, "Network not found")
    return {"id": result["id"], "deleted": True}
