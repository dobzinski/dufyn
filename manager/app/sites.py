from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection


router = APIRouter(prefix="/api/sites", tags=["sites"])


class SiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_fields(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value):
        return value or None


@router.get("")
def list_sites():
    with get_connection() as connection:
        return connection.execute(
            """SELECT s.id, s.name, s.description, s.created_at,
                (SELECT COUNT(*) FROM site_port_catalog p WHERE p.site_id = s.id) AS ports_count
                FROM site s ORDER BY s.name, s.id"""
        ).fetchall()


@router.post("", status_code=201)
def create_site(site: SiteCreate):
    try:
        with get_connection(read_only=False) as connection:
            result = connection.execute(
                """
                INSERT INTO site (name, description) VALUES (%s, %s)
                RETURNING id, name, description, created_at
                """,
                (site.name, site.description),
            ).fetchone()
        return result
    except UniqueViolation:
        raise HTTPException(status_code=409, detail="A site with this name already exists") from None


@router.put("/{site_id}")
def update_site(site_id: int, site: SiteCreate):
    try:
        with get_connection(read_only=False) as connection:
            result = connection.execute(
                """UPDATE site SET name = %s, description = %s WHERE id = %s
                RETURNING id, name, description, created_at""",
                (site.name, site.description, site_id),
            ).fetchone()
            if result is None:
                raise HTTPException(status_code=404, detail="Site not found")
        return result
    except UniqueViolation:
        raise HTTPException(status_code=409, detail="A site with this name already exists") from None


@router.delete("/{site_id}")
def delete_site(site_id: int):
    try:
        with get_connection(read_only=False) as connection:
            result = connection.execute(
                "DELETE FROM site WHERE id = %s RETURNING id", (site_id,)
            ).fetchone()
            if result is None:
                raise HTTPException(status_code=404, detail="Site not found")
        return {"id": result["id"], "deleted": True}
    except ForeignKeyViolation:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a site with networks, scanners or assets",
        ) from None
