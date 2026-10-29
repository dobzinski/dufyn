from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation
from app.database import get_connection

router = APIRouter(prefix="/api", tags=["port assignments"])
OWNERS = {"sites": ("site", "site_port_catalog", "site_id"), "networks": ("network", "network_port_catalog", "network_id"), "targets": ("target", "target_port_catalog", "target_id")}


class PortSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_ids: list[int] = Field(max_length=10000)

    @field_validator("service_ids")
    @classmethod
    def unique_positive(cls, values):
        if len(values) != len(set(values)) or any(v <= 0 or v > 9223372036854775807 for v in values):
            raise ValueError("Service IDs must be unique positive IDs")
        return values


class NetworkPortSelection(PortSelection):
    port_mode: Literal["I", "C"]


def list_selection(kind, owner_id):
    owner_table, link_table, owner_key = OWNERS[kind]
    columns = "id, site_id, port_mode" if kind == "networks" else "id"
    with get_connection() as connection:
        owner = connection.execute(f"SELECT {columns} FROM {owner_table} WHERE id = %s", (owner_id,)).fetchone()
        if owner is None:
            raise HTTPException(404, "Record not found")
        protocol_filter = ""
        services = connection.execute(f"""
            SELECT p.id, p.port, p.protocol, p.service,
                EXISTS (SELECT 1 FROM {link_table} a WHERE a.{owner_key} = %s AND a.port_catalog_id = p.id) AS assigned,
                EXISTS (SELECT 1 FROM site_port_catalog a WHERE a.site_id = %s AND a.port_catalog_id = p.id) AS inherited
            FROM port_catalog p {protocol_filter} ORDER BY p.port, p.protocol, p.id
        """, (owner_id, owner.get("site_id", owner_id))).fetchall()
    return {"id": owner_id, "services": services, "port_mode": owner.get("port_mode")}


def save_selection(kind, owner_id, body):
    owner_table, link_table, owner_key = OWNERS[kind]
    try:
        with get_connection(read_only=False) as connection:
            owner = connection.execute(f"SELECT id FROM {owner_table} WHERE id = %s FOR UPDATE", (owner_id,)).fetchone()
            if owner is None:
                raise HTTPException(404, "Record not found")
            services = connection.execute("SELECT id, protocol FROM port_catalog WHERE id = ANY(%s::bigint[]) FOR SHARE", (body.service_ids,)).fetchall()
            if {p["id"] for p in services} != set(body.service_ids):
                raise HTTPException(422, "A selected service no longer exists. Reload the list.")
            # Inheritance preserves the network's saved custom selection.
            if kind in ("sites", "targets") or body.port_mode == "C":
                connection.execute(f"DELETE FROM {link_table} WHERE {owner_key} = %s AND NOT (port_catalog_id = ANY(%s::bigint[]))", (owner_id, body.service_ids))
                with connection.cursor() as cursor:
                    if kind == "targets":
                        cursor.executemany("INSERT INTO target_port_catalog (target_id, port_catalog_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", [(owner_id, value) for value in body.service_ids])
                    else:
                        cursor.executemany(f"INSERT INTO {link_table} ({owner_key}, port_catalog_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", [(owner_id, value) for value in body.service_ids])
            if kind == "networks":
                connection.execute("UPDATE network SET port_mode = %s WHERE id = %s", (body.port_mode, owner_id))
        return {"id": owner_id, "saved": True}
    except ForeignKeyViolation:
        raise HTTPException(409, "Record or service changed. Reload the list.") from None


@router.get("/sites/{site_id}/ports")
def site_ports(site_id: int):
    return list_selection("sites", site_id)


@router.put("/sites/{site_id}/ports")
def save_site_ports(site_id: int, body: PortSelection):
    return save_selection("sites", site_id, body)


@router.get("/networks/{network_id}/ports")
def network_ports(network_id: int):
    return list_selection("networks", network_id)


@router.put("/networks/{network_id}/ports")
def save_network_ports(network_id: int, body: NetworkPortSelection):
    return save_selection("networks", network_id, body)


@router.get("/targets/{target_id}/ports")
def target_ports(target_id: int):
    return list_selection("targets", target_id)


@router.put("/targets/{target_id}/ports")
def save_target_ports(target_id: int, body: PortSelection):
    return save_selection("targets", target_id, body)
