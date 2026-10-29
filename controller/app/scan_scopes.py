"""Snapshot helpers for macro scans, reusing the existing task leases."""
import json
from ipaddress import ip_address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.dns_inventory import normalize_name


# Correlated predicates are shared by claims and snapshots so only destinations
# with actual differences are queued. Inventory IDs never stand for catalog IDs.
TARGET_EXCLUSIVE_PORTS = """
    SELECT p.id, p.port, p.protocol FROM port p
    WHERE p.target_id = t.id AND NOT EXISTS (
        SELECT 1 FROM target_port_catalog c JOIN port_catalog pc ON pc.id=c.port_catalog_id
        WHERE c.target_id=t.id AND pc.port=p.port AND pc.protocol=p.protocol)
"""

ASSET_EXCLUSIVE_PORTS = """
    SELECT p.id, p.port, p.protocol, p.service FROM port p
    WHERE p.asset_id = a.id AND NOT EXISTS (
        SELECT 1 FROM port_catalog pc WHERE pc.port=p.port AND pc.protocol=p.protocol AND (
            (n.port_mode='I' AND EXISTS (SELECT 1 FROM site_port_catalog c
                WHERE c.site_id=a.site_id AND c.port_catalog_id=pc.id)) OR
            (n.port_mode='C' AND EXISTS (SELECT 1 FROM network_port_catalog c
                WHERE c.network_id=n.id AND c.port_catalog_id=pc.id))))
"""

ASSET_EXCLUSIVE_NETWORK = """
    SELECT n.id, n.port_mode FROM network n JOIN scanner_network sn
        ON sn.network_id=n.id AND sn.site_id=n.site_id
    WHERE sn.scanner_id=%s AND n.site_id=a.site_id AND n.enabled='Y' AND a.ip <<= n.cidr
    ORDER BY masklen(n.cidr) DESC, n.id LIMIT 1
"""


def exclusive_asset_snapshot(connection, asset_id, scanner):
    asset = connection.execute("""
        SELECT a.id, host(a.ip) AS ip, a.status, a.discovered_hostname,
            n.id AS network_id, n.port_mode FROM asset a JOIN LATERAL (
    """ + ASSET_EXCLUSIVE_NETWORK + """) n ON TRUE
        WHERE a.id=%s AND a.site_id=%s AND a.status IN ('A','U')
    """, (scanner['id'], asset_id, scanner['site_id'])).fetchone()
    if asset is None:
        return None
    ports = connection.execute("""
        SELECT p.* FROM asset a JOIN network n ON n.id=%s
        JOIN LATERAL (
    """ + ASSET_EXCLUSIVE_PORTS + " FOR SHARE OF p" + """) p ON TRUE WHERE a.id=%s ORDER BY p.id
    """, (asset['network_id'], asset_id)).fetchall()
    return {**dict(asset), 'scope': 'exclusive', 'ports': ports}


def target_ports(connection, target_id, scope, lock=False):
    if scope == "exclusive":
        return connection.execute("SELECT p.* FROM target t JOIN LATERAL (" +
            TARGET_EXCLUSIVE_PORTS + (" FOR SHARE OF p" if lock else "") + ") p ON TRUE WHERE t.id=%s ORDER BY p.id",
            (target_id,)).fetchall()
    if scope == "targets":
        return []
    if scope == "certificates":
        return connection.execute("""
            SELECT id, port, protocol FROM port WHERE target_id=%s AND state='O'
            ORDER BY id
        """ + (" FOR SHARE" if lock else ""), (target_id,)).fetchall()
    return connection.execute("""
        SELECT c.id, p.port, p.protocol FROM target_port_catalog c
        JOIN port_catalog p ON p.id=c.port_catalog_id WHERE c.target_id=%s ORDER BY c.id
    """ + (" FOR SHARE OF c, p" if lock else ""), (target_id,)).fetchall()


def target_snapshot(connection, target, scope, lock=False):
    snapshot = {"status": target["status"],
                "ports": target_ports(connection, target["id"], scope, lock)}
    if scope != "full":
        snapshot["scope"] = scope
    return snapshot


def asset_snapshot(connection, asset_id, scanner, scope, base_config, dns_server=None, dns_port=53):
    if scope == "exclusive":
        return exclusive_asset_snapshot(connection, asset_id, scanner)
    if scope in ("full", "ports"):
        snapshot = base_config(connection, asset_id, scanner)
        if snapshot is not None and scope != "full":
            snapshot = {**snapshot, "scope": scope}
        return snapshot
    asset = connection.execute("""
        SELECT a.id, host(a.ip) AS ip, a.discovered_hostname FROM asset a
        WHERE a.id=%s AND a.site_id=%s
    """ + (""" AND a.status IN ('A','U') AND EXISTS (
        SELECT 1 FROM network n JOIN scanner_network sn ON sn.network_id=n.id AND sn.site_id=n.site_id
        WHERE sn.scanner_id=%s AND n.site_id=a.site_id AND n.enabled='Y' AND a.ip <<= n.cidr)
    """ if scope == "certificates" else ""),
        (asset_id, scanner["site_id"], scanner["id"]) if scope == "certificates"
        else (asset_id, scanner["site_id"])).fetchone()
    if asset is None:
        return None
    snapshot = {**dict(asset), "scope": scope, "ports": []}
    if scope == "ptr":
        snapshot.update(dns_server=dns_server, dns_port=dns_port)
    else:
        snapshot["ports"] = connection.execute("""
            SELECT id, port, protocol FROM port WHERE asset_id=%s AND state='O' ORDER BY id FOR SHARE
        """, (asset_id,)).fetchall()
    return snapshot


class PtrRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hostname: str = Field(min_length=1, max_length=1024)
    ttl: int = Field(strict=True, ge=0, le=4294967295)

    @field_validator("hostname")
    @classmethod
    def normalized(cls, value):
        try:
            import re
            name = normalize_name(value)
            if len(name.split('.')) < 2 or any(not re.fullmatch(
                r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in name.split('.')):
                raise ValueError("PTR must contain a valid domain")
            try:
                ip_address(name)
            except ValueError:
                return name
            raise ValueError("PTR cannot be an IP literal")
        except Exception as error:
            raise ValueError("Invalid PTR hostname") from error


class ReverseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: Literal["answered", "no_ptr", "nxdomain", "timeout", "servfail", "refused", "error"]
    response_code: int | None = Field(default=None, strict=True, ge=0, le=4095)
    error_message: str | None = Field(default=None, max_length=1000)
    records: list[PtrRecord] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_answers(self):
        if (self.result == "answered") != bool(self.records):
            raise ValueError("Only answered queries must contain PTR records")
        if len({record.hostname for record in self.records}) != len(self.records):
            raise ValueError("Duplicate PTR answer")
        expected = {"answered":0, "no_ptr":0, "nxdomain":3, "servfail":2, "refused":5}
        if self.result in expected and self.response_code != expected[self.result]:
            raise ValueError("PTR result and DNS response code disagree")
        return self


def normalize_server(value):
    address = ip_address(value)
    if getattr(address, "scope_id", None):
        raise ValueError("Scoped IPv6 DNS servers are not supported")
    return str(address)


def save_reverse(connection, scanner, snapshot, result):
    observations = json.dumps([{"ip":snapshot["ip"], **result.model_dump(mode="json")}])
    connection.execute(PRUNE_REVERSE_SQL, (observations,scanner["site_id"]))
    connection.execute(EMPTY_REVERSE_SQL, (observations,scanner["site_id"],snapshot["dns_server"],snapshot["dns_port"]))
    if result.result != "answered" or not result.records:
        return
    row = connection.execute("""
        INSERT INTO dns_reverse_check(site_id,scanner_id,asset_id,ip,dns_server,dns_port,
            result,response_code,error_message)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (scanner["site_id"],scanner["id"],snapshot["id"],snapshot["ip"],
          snapshot["dns_server"],snapshot["dns_port"],result.result,
          result.response_code,result.error_message)).fetchone()
    if result.records:
        connection.execute("""
            INSERT INTO dns_ptr_record(reverse_check_id,hostname,ttl)
            SELECT %s,r.hostname,r.ttl FROM jsonb_to_recordset(%s::jsonb)
                AS r(hostname text, ttl bigint)
            ON CONFLICT (site_id,ip,hostname) DO UPDATE SET
                reverse_check_id=EXCLUDED.reverse_check_id,ttl=EXCLUDED.ttl,
                first_seen=LEAST(dns_ptr_record.first_seen,EXCLUDED.first_seen),
                last_seen=GREATEST(dns_ptr_record.last_seen,EXCLUDED.last_seen)
        """, (row["id"],json.dumps([record.model_dump() for record in result.records])))


NETWORK_REVERSE_SQL = """
    WITH observations AS (
        SELECT * FROM jsonb_to_recordset(%s::jsonb)
            AS x(ip inet,result text,response_code integer,error_message text,records jsonb)
    ), saved AS (
        INSERT INTO dns_reverse_check(site_id,scanner_id,asset_id,ip,dns_server,dns_port,
            result,response_code,error_message,icmp_responded)
        SELECT %s,%s,a.id,o.ip,%s::inet,%s,o.result,o.response_code,o.error_message,
            o.ip=ANY(%s::inet[])
        FROM observations o LEFT JOIN asset a ON a.site_id=%s AND a.ip=o.ip
        WHERE o.result='answered' AND jsonb_array_length(o.records)>0
        RETURNING id,ip
    )
    INSERT INTO dns_ptr_record(reverse_check_id,hostname,ttl)
    SELECT s.id,p.hostname,p.ttl FROM observations o JOIN saved s ON s.ip=o.ip
    CROSS JOIN LATERAL jsonb_to_recordset(o.records) AS p(hostname text,ttl bigint)
            ON CONFLICT (site_id,ip,hostname) DO UPDATE SET
                reverse_check_id=EXCLUDED.reverse_check_id,ttl=EXCLUDED.ttl,
                first_seen=LEAST(dns_ptr_record.first_seen,EXCLUDED.first_seen),
                last_seen=GREATEST(dns_ptr_record.last_seen,EXCLUDED.last_seen)
"""


PRUNE_REVERSE_SQL = """
    DELETE FROM dns_ptr_record p USING dns_reverse_check c,
        jsonb_to_recordset(%s::jsonb) AS o(ip inet,result text,records jsonb)
    WHERE p.reverse_check_id=c.id AND c.site_id=%s AND c.ip=o.ip
        AND o.result IN ('answered','no_ptr','nxdomain')
        AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset(o.records) AS answer(hostname text)
                        WHERE answer.hostname=p.hostname)
"""


EMPTY_REVERSE_SQL = """
    DELETE FROM dns_reverse_check c
    WHERE c.ip IN (SELECT ip FROM jsonb_to_recordset(%s::jsonb) AS o(ip inet))
        AND c.site_id=%s AND c.dns_server=%s::inet AND c.dns_port=%s
        AND NOT EXISTS (SELECT 1 FROM dns_ptr_record p WHERE p.reverse_check_id=c.id)
"""


def save_network_reverse(connection, scanner, result, active_ips):
    # Remove obsolete answers only after a successful response, within the same transaction.
    observations = json.dumps([item.model_dump(mode="json") for item in result.observations])
    connection.execute(PRUNE_REVERSE_SQL, (observations,scanner["site_id"]))
    connection.execute(EMPTY_REVERSE_SQL, (observations,scanner["site_id"],result.dns_server,result.dns_port))
    connection.execute(NETWORK_REVERSE_SQL, (
        observations,
        scanner["site_id"],scanner["id"],result.dns_server,result.dns_port,
        active_ips,scanner["site_id"]))
