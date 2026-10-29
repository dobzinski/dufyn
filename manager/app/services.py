from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection

router = APIRouter(prefix="/api/services", tags=["services"])
COLUMNS = "id, port, protocol, service"


class ServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: int = Field(ge=1, le=65535)
    protocol: Literal["T", "U"] = "T"
    service: str | None = Field(default=None, max_length=100)

    @field_validator("service", mode="before")
    @classmethod
    def trim_service(cls, value):
        return value.strip() or None if isinstance(value, str) else value


@router.get("")
def list_services():
    with get_connection() as connection:
        return connection.execute(f"SELECT {COLUMNS} FROM port_catalog ORDER BY port, protocol, id").fetchall()


@router.post("", status_code=201)
def create_service(service: ServiceInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                INSERT INTO port_catalog (port, protocol, service) VALUES (%s, %s, %s)
                RETURNING {COLUMNS}
            """, (service.port, service.protocol, service.service)).fetchone()
        return row
    except UniqueViolation:
        raise HTTPException(409, "This port and protocol already exist in the service catalog") from None


@router.put("/{service_id}")
def update_service(service_id: int, service: ServiceInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                UPDATE port_catalog SET port = %s, protocol = %s, service = %s
                WHERE id = %s RETURNING {COLUMNS}
            """, (service.port, service.protocol, service.service, service_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Service not found")
        return row
    except UniqueViolation:
        raise HTTPException(409, "This port and protocol already exist in the service catalog") from None


@router.delete("/{service_id}")
def delete_service(service_id: int):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute("DELETE FROM port_catalog WHERE id = %s RETURNING id", (service_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Service not found")
        return {"id": row["id"], "deleted": True}
    except ForeignKeyViolation:
        raise HTTPException(409, "Cannot delete a service assigned to sites or networks") from None
