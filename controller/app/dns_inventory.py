"""Authenticated DNS-zone registration and atomic publication of complete snapshots."""
import json
from datetime import datetime
from ipaddress import ip_address
from typing import Literal

import dns.name
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database import get_connection

MAX_RECORDS = 100000


def normalize_name(value):
    name = dns.name.from_text(value.strip()).canonicalize()
    text = name.to_text(omit_final_dot=True)
    if name == dns.name.root or len(text) > 253:
        raise ValueError("DNS name must be a non-root absolute name of at most 253 characters")
    return text


def normalize_address(value):
    address = ip_address(value)
    if getattr(address, "scope_id", None):
        raise ValueError("Use an unscoped IPv4 or IPv6 address")
    return str(address)


class ZoneSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=1024)
    dns_server: str
    dns_port: int = Field(default=53, strict=True, ge=1, le=65535)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value):
        try:
            return normalize_name(value)
        except (dns.exception.DNSException, UnicodeError, ValueError) as error:
            raise ValueError("Invalid DNS zone name") from error

    @field_validator("dns_server")
    @classmethod
    def server_address(cls, value):
        return normalize_address(value)


class ZoneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=1024)
    record_type: Literal["A", "AAAA", "CNAME"]
    address: str | None = None
    target_name: str | None = Field(default=None, max_length=1024)
    ttl: int = Field(strict=True, ge=0, le=4294967295)

    @field_validator("name", "target_name")
    @classmethod
    def normalized_name(cls, value):
        if value is None:
            return value
        try:
            return normalize_name(value)
        except (dns.exception.DNSException, UnicodeError, ValueError) as error:
            raise ValueError("Invalid DNS record name") from error

    @model_validator(mode="after")
    def validate_value(self):
        if self.record_type == "CNAME":
            if self.address is not None or self.target_name is None:
                raise ValueError("CNAME requires only target_name")
        else:
            if self.address is None or self.target_name is not None:
                raise ValueError("A/AAAA requires only address")
            self.address = normalize_address(self.address)
            address = ip_address(self.address)
            if address.version != (4 if self.record_type == "A" else 6):
                raise ValueError("Address family must match record_type")
            self.address = str(address)
        return self


class ZoneSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial: int = Field(strict=True, ge=0, le=4294967295)
    complete: bool = Field(strict=True)
    expected_last_synced_at: datetime | None = None
    records: list[ZoneRecord] = Field(max_length=MAX_RECORDS)

    @field_validator("expected_last_synced_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Synchronization timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self):
        if not self.complete:
            raise ValueError("Only complete zone transfers may be published")
        seen = set()
        owner_types = {}
        for record in self.records:
            key = (record.name, record.record_type, record.address, record.target_name)
            if key in seen:
                raise ValueError("Duplicate DNS record")
            seen.add(key)
            types = owner_types.setdefault(record.name, set())
            if (record.record_type == "CNAME" and types) or "CNAME" in types:
                raise ValueError("CNAME owner cannot have another CNAME, A or AAAA")
            types.add(record.record_type)
        return self


# Shared typed recordset keeps bulk publication bounded to a few SQL statements.
RECORDSET = """SELECT * FROM jsonb_to_recordset(%s::jsonb)
    AS r(name text, record_type text, address inet, target_name text, ttl bigint)"""


def create_dns_router(scanner_identity):
    router = APIRouter(prefix="/v1/private/dns", tags=["DNS inventory"])

    @router.post("/zones/register")
    def register_zone(body: ZoneSource, scanner: dict = Depends(scanner_identity)):
        with get_connection(read_only=False) as connection:
            zone = connection.execute("""
                INSERT INTO dns_zone(site_id, name, dns_server, dns_port)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(site_id, dns_server, dns_port, name)
                DO UPDATE SET name=EXCLUDED.name
                RETURNING id, name, host(dns_server) AS dns_server, dns_port, serial, last_synced_at
            """, (scanner["site_id"], body.name, body.dns_server, body.dns_port)).fetchone()
            connection.execute("UPDATE scanner SET last_seen=NOW() WHERE id=%s AND site_id=%s",
                               (scanner["id"], scanner["site_id"]))
        return dict(zone)

    @router.post("/zones/{zone_id}/records")
    def publish_zone(zone_id: int, body: ZoneSnapshot, scanner: dict = Depends(scanner_identity)):
        with get_connection(read_only=False) as connection:
            zone = connection.execute("""
                SELECT id, name, last_synced_at FROM dns_zone
                WHERE id=%s AND site_id=%s FOR UPDATE
            """, (zone_id, scanner["site_id"])).fetchone()
            if zone is None:
                raise HTTPException(404, "DNS zone not found in the scanner site")
            if zone["last_synced_at"] != body.expected_last_synced_at:
                raise HTTPException(409, "Zone changed during transfer; register and collect again")
            origin = dns.name.from_text(zone["name"])
            if any(not dns.name.from_text(record.name).is_subdomain(origin) for record in body.records):
                raise HTTPException(422, "DNS record owner must belong to the registered zone")
            payload = json.dumps([record.model_dump() for record in body.records])
            synced = connection.execute("""
                UPDATE dns_zone SET serial=%s, last_scanner_id=%s,
                    last_synced_at=GREATEST(clock_timestamp(),
                        COALESCE(last_synced_at, '-infinity'::timestamptz) + interval '1 microsecond')
                WHERE id=%s AND site_id=%s RETURNING last_synced_at
            """, (body.serial, scanner["id"], zone_id, scanner["site_id"])).fetchone()["last_synced_at"]
            # Remove obsolete values first, including old CNAME targets.
            connection.execute("""
                DELETE FROM dns_record d WHERE d.zone_id=%s AND NOT EXISTS (
                    SELECT 1 FROM (""" + RECORDSET + """) r
                    WHERE r.name=d.name AND r.record_type=d.record_type
                      AND r.address IS NOT DISTINCT FROM d.address
                      AND r.target_name IS NOT DISTINCT FROM d.target_name
                )
            """, (zone_id, payload))
            connection.execute("""
                INSERT INTO dns_record(zone_id,name,record_type,address,target_name,ttl,first_seen,last_seen)
                SELECT %s, r.name, r.record_type, r.address, r.target_name, r.ttl, %s, %s
                FROM (""" + RECORDSET + """) r
                ON CONFLICT(zone_id,name,record_type,address,target_name)
                DO UPDATE SET ttl=EXCLUDED.ttl, last_seen=EXCLUDED.last_seen
            """, (zone_id, synced, synced, payload))
            connection.execute("UPDATE scanner SET last_seen=NOW() WHERE id=%s AND site_id=%s",
                               (scanner["id"], scanner["site_id"]))
        return {"state": "completed", "zone_id": zone_id, "serial": body.serial,
                "records": len(body.records), "last_synced_at": synced}

    return router
