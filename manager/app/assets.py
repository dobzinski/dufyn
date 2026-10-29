from ipaddress import ip_address

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection
from app.inventory import ASSET_COLUMNS, build_asset


router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: int = Field(gt=0)
    ip: str
    asset_type_id: int | None = Field(default=None, gt=0)
    operating_system_id: int | None = Field(default=None, gt=0)
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("ip", "display_name", mode="before")
    @classmethod
    def trim_fields(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value):
        if "%" in value:
            raise ValueError("Scoped IP addresses are not supported")
        return str(ip_address(value))

    @field_validator("display_name")
    @classmethod
    def normalize_name(cls, value):
        return value or None


def asset_conflict(error):
    if isinstance(error, UniqueViolation):
        return HTTPException(status_code=409, detail="An asset with this IP already exists in this site")
    return HTTPException(status_code=422, detail="Check the selected site, asset type and operating system, and any linked topology views")


@router.post("", status_code=201)
def create_asset(asset: AssetCreate):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(
                f"""INSERT INTO asset (site_id, ip, display_name, asset_type_id, operating_system_id, discovered_by_icmp,
                    discovered_hostname, latency_ms, status, first_seen, last_seen, last_seen_active)
                VALUES (%s, %s, %s, %s, %s, 'N', NULL, NULL, 'U', NOW(), NULL, NULL)
                RETURNING {ASSET_COLUMNS}""",
                (asset.site_id, asset.ip, asset.display_name, asset.asset_type_id, asset.operating_system_id),
            ).fetchone()
        return build_asset(row)
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise asset_conflict(error) from None


@router.put("/{asset_id}")
def update_asset(asset_id: int, asset: AssetCreate):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(
                f"""UPDATE asset SET site_id = %s, ip = %s, display_name = %s, asset_type_id = %s, operating_system_id = %s
                WHERE id = %s RETURNING {ASSET_COLUMNS}""",
                (asset.site_id, asset.ip, asset.display_name, asset.asset_type_id, asset.operating_system_id, asset_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Asset not found")
        return build_asset(row)
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise asset_conflict(error) from None


@router.delete("/{asset_id}")
def delete_asset(asset_id: int):
    with get_connection(read_only=False) as connection:
        row = connection.execute("DELETE FROM asset WHERE id = %s RETURNING id", (asset_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Asset not found")
    return {"id": row["id"], "deleted": True}
