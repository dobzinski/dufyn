import re
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app.database import get_connection
from app.inventory import CERTIFICATE_COLUMNS, build_certificate

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


class CertificateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    asset_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    subject_cn: str | None = None
    issuer_cn: str | None = None
    valid_from: date
    valid_until: date
    note: str | None = Field(default=None, max_length=100)

    @field_validator("subject_cn", "issuer_cn", "note", mode="before")
    @classmethod
    def trim_fields(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("valid_from", "valid_until", mode="before")
    @classmethod
    def validate_date(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Enter a date in YYYY-MM-DD format")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def validate_period(self):
        if (self.asset_id is None) == (self.port_id is None):
            raise ValueError("Select exactly one asset or port")
        if self.valid_until < self.valid_from:
            raise ValueError("Expiration date must be on or after the start date")
        return self

    def values(self):
        return (self.port_id, self.asset_id, self.subject_cn, self.issuer_cn,
                datetime.combine(self.valid_from, time.min, timezone.utc),
                datetime.combine(self.valid_until, time.min, timezone.utc),
                self.note)


def certificate_conflict(error):
    if isinstance(error, UniqueViolation):
        return HTTPException(409, "A certificate already exists for the selected port")
    return HTTPException(422, "Selected asset or port does not exist")


@router.get("/port-options")
def port_options():
    with get_connection() as connection:
        return connection.execute("""
            SELECT p.id,
                CASE WHEN p.asset_id IS NULL THEN 'target' ELSE 'asset' END AS owner_type,
                p.port::text || '/' || CASE p.protocol WHEN 'T' THEN 'TCP' ELSE 'UDP' END || ' | ' ||
                CASE WHEN a.id IS NOT NULL THEN
                    concat_ws(' - ',host(a.ip),COALESCE(NULLIF(a.display_name,''),NULLIF(a.discovered_hostname,'')))
                    ELSE t.fqdn END AS name,
                COALESCE(n.cidr::text, CASE WHEN t.id IS NOT NULL THEN 'Targets' ELSE 'Sem network' END) || ' - ' || s.name AS network_group,
                COALESCE('network:' || n.id::text, 'site:' || COALESCE(a.site_id, t.site_id)::text) AS network_group_id
            FROM port p LEFT JOIN asset a ON a.id = p.asset_id
            LEFT JOIN target t ON t.id = p.target_id
            JOIN site s ON s.id = COALESCE(a.site_id, t.site_id)
            LEFT JOIN LATERAL (
                SELECT id, cidr FROM network
                WHERE site_id = a.site_id AND a.ip <<= cidr
                ORDER BY masklen(cidr) DESC, id LIMIT 1
            ) n ON TRUE
            ORDER BY s.id, n.cidr NULLS LAST, a.ip, t.fqdn, p.port, p.protocol
        """).fetchall()


@router.post("", status_code=201)
def create_certificate(certificate: CertificateInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                INSERT INTO certificate AS c (port_id, asset_id, subject_cn, issuer_cn,
                    valid_from, valid_until, note, fingerprint_sha256, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
                RETURNING {CERTIFICATE_COLUMNS}
            """, certificate.values()).fetchone()
        return build_certificate(row)
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise certificate_conflict(error) from None


@router.put("/{certificate_id}")
def update_certificate(certificate_id: int, certificate: CertificateInput):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute(f"""
                UPDATE certificate AS c SET port_id = %s, asset_id = %s, subject_cn = %s,
                    issuer_cn = %s, valid_from = %s, valid_until = %s,
                    note = %s
                WHERE c.id = %s RETURNING {CERTIFICATE_COLUMNS}
            """, (*certificate.values(), certificate_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Certificate not found")
        return build_certificate(row)
    except (UniqueViolation, ForeignKeyViolation) as error:
        raise certificate_conflict(error) from None


@router.delete("/{certificate_id}")
def delete_certificate(certificate_id: int):
    with get_connection(read_only=False) as connection:
        row = connection.execute("DELETE FROM certificate WHERE id = %s RETURNING id", (certificate_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Certificate not found")
    return {"id": row["id"], "deleted": True}
