import hashlib
import json
import os
from ipaddress import IPv4Address, ip_network
from datetime import datetime
from pydantic import model_validator
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database import get_connection
from app.config import load_version
from app.scan_scopes import TARGET_EXCLUSIVE_PORTS, ASSET_EXCLUSIVE_PORTS, ASSET_EXCLUSIVE_NETWORK, target_snapshot, asset_snapshot, ReverseResult, normalize_server, save_reverse, save_network_reverse

app = FastAPI(title="Network Discovery Controller", version=load_version())
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def token_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def token_value(credentials):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


def scanner_identity(credentials: Credentials):
    digest = token_digest(token_value(credentials))
    with get_connection() as connection:
        scanner = connection.execute(
            "SELECT id, site_id FROM scanner WHERE token_hash = %s AND enabled = 'Y'", (digest,)
        ).fetchone()
    if scanner is None:
        raise HTTPException(401, "Invalid or disabled scanner token")
    return scanner


Scanner = Annotated[dict, Depends(scanner_identity)]


@app.exception_handler(psycopg.Error)
def database_error(request, error):
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    with get_connection() as connection:
        connection.execute("SELECT 1")
    return {"status": "ready"}


@app.get("/v1/private/config")
def configuration(scanner: Scanner):
    with get_connection() as connection:
        networks = connection.execute("""
            SELECT n.id, n.cidr::text AS cidr, n.name, n.port_mode, n.ptr_enabled
            FROM network n JOIN scanner_network sn ON sn.network_id = n.id AND sn.site_id = n.site_id
            WHERE sn.scanner_id = %s AND n.site_id = %s AND n.enabled = 'Y' ORDER BY n.id
        """, (scanner["id"], scanner["site_id"])).fetchall()
    return {"scanner_id": scanner["id"], "site_id": scanner["site_id"], "networks": networks}


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=100)

    @field_validator("worker_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Worker ID must not be blank")
        return value.strip()


class DiscoveryClaim(Claim):
    scope: Literal["discovery", "ptr"] = "discovery"
    excluded_network_ids: list[int] = Field(default_factory=list, max_length=10000)

    @field_validator("excluded_network_ids")
    @classmethod
    def validate_excluded_ids(cls, values):
        if any(value <= 0 or value > 9223372036854775807 for value in values):
            raise ValueError("Excluded network IDs must be positive")
        return values


@app.post("/v1/private/discovery/claim")
def claim_task(body: DiscoveryClaim, scanner: Scanner):
    lease_seconds = max(60, int(os.getenv("DISCOVERY_LEASE_SECONDS", "90")))
    with get_connection(read_only=False) as connection:
        # Lock the network before inspecting leases: all workers use the same lock order.
        ptr_filter = " AND n.ptr_enabled='Y'" if body.scope == "ptr" else ""
        network = connection.execute("""
            SELECT n.id, n.cidr::text AS cidr, n.ptr_enabled FROM network n
            JOIN scanner_network sn ON sn.network_id = n.id AND sn.site_id = n.site_id
            WHERE sn.scanner_id = %s AND n.site_id = %s AND n.enabled = 'Y'
              AND family(n.cidr) = 4
              """ + ptr_filter + """
              AND NOT (n.id = ANY(%s::bigint[]))
              AND NOT EXISTS (SELECT 1 FROM discovery_task t WHERE t.network_id = n.id
                  AND t.state = 'running' AND t.lease_until > NOW())
            ORDER BY n.id FOR UPDATE OF n SKIP LOCKED LIMIT 1
        """, (scanner["id"], scanner["site_id"], body.excluded_network_ids)).fetchone()
        connection.execute("UPDATE scanner SET last_seen = NOW() WHERE id = %s", (scanner["id"],))
        if network is None:
            availability = connection.execute("""
                SELECT n.id, n.cidr::text AS cidr, n.enabled,
                    family(n.cidr) AS address_family,
                    n.id = ANY(%s::bigint[]) AS excluded_in_cycle,
                    EXISTS (SELECT 1 FROM discovery_task t WHERE t.network_id = n.id
                        AND t.state = 'running' AND t.lease_until > NOW()) AS reserved
                FROM network n JOIN scanner_network sn
                    ON sn.network_id = n.id AND sn.site_id = n.site_id
                WHERE sn.scanner_id = %s AND n.site_id = %s ORDER BY n.id
            """, (body.excluded_network_ids, scanner["id"], scanner["site_id"])).fetchall()
            return {"task": None, "availability": list(availability)}
        connection.execute("UPDATE discovery_task SET state = 'expired' WHERE network_id = %s AND state = 'running' AND lease_until <= NOW()", (network["id"],))
        task = connection.execute("""
            INSERT INTO discovery_task (id, scanner_id, network_id, site_id, worker_id, lease_until, scope)
            VALUES (%s, %s, %s, %s, %s, NOW() + %s * INTERVAL '1 second', %s)
            RETURNING id, network_id, worker_id, lease_until
        """, (uuid4(), scanner["id"], network["id"], scanner["site_id"], body.worker_id, lease_seconds, body.scope)).fetchone()
        task["scope"] = body.scope
        task["cidr"] = network["cidr"]
        task["ptr_enabled"] = network.get("ptr_enabled", "N")
    return {"task": task}


@app.post("/v1/private/discovery/{task_id}/heartbeat")
def heartbeat(task_id: UUID, body: Claim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        task = connection.execute("""
            UPDATE discovery_task SET lease_until = NOW() + %s * INTERVAL '1 second'
            WHERE id = %s AND scanner_id = %s AND site_id = %s AND worker_id = %s
              AND state = 'running' AND lease_until > NOW() RETURNING lease_until
        """, (max(60, int(os.getenv("DISCOVERY_LEASE_SECONDS", "90"))), task_id, scanner["id"], scanner["site_id"], body.worker_id)).fetchone()
        if task is None:
            raise HTTPException(409, "Task is unavailable or its lease expired")
        connection.execute("UPDATE scanner SET last_seen = NOW() WHERE id = %s", (scanner["id"],))
    return task


@app.post("/v1/private/discovery/{task_id}/cancel")
def cancel_discovery(task_id: UUID, body: Claim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        task = connection.execute("""
            UPDATE discovery_task SET state = 'expired', lease_until = NOW()
            WHERE id = %s AND scanner_id = %s AND site_id = %s AND worker_id = %s
              AND state = 'running' RETURNING id
        """, (task_id, scanner["id"], scanner["site_id"], body.worker_id)).fetchone()
    return {"task_id": task_id, "released": task is not None}


class Host(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip: IPv4Address
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    hostname: str | None = Field(default=None, max_length=255)


class NetworkReverseObservation(ReverseResult):
    ip: IPv4Address


class NetworkReverseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dns_server: str
    dns_port: int = Field(default=53, strict=True, ge=1, le=65535)
    observations: list[NetworkReverseObservation]

    @field_validator("dns_server")
    @classmethod
    def server_address(cls, value):
        return normalize_server(value)

    @field_validator("observations")
    @classmethod
    def unique_ips(cls, values):
        if len({item.ip for item in values}) != len(values):
            raise ValueError("Duplicate network PTR address")
        return values


class DiscoveryResult(Claim):
    scope: Literal["discovery", "ptr"] = "discovery"
    complete: Literal[True]
    hosts: list[Host]
    ptr: NetworkReverseResult | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope == "ptr" and (self.hosts or self.ptr is None):
            raise ValueError("Network PTR tasks require PTR results and no ICMP hosts")
        return self

    @field_validator("hosts")
    @classmethod
    def unique_hosts(cls, hosts):
        if len({host.ip for host in hosts}) != len(hosts):
            raise ValueError("Duplicate IP addresses")
        return hosts


def result_digest(body):
    hosts = sorted((host.model_dump(mode="json") for host in body.hosts), key=lambda item: item["ip"])
    if body.ptr is None:
        payload = hosts  # Preserve digest compatibility with older scanners.
    else:
        ptr = body.ptr.model_dump(mode="json")
        ptr["observations"] = sorted(ptr["observations"], key=lambda item: item["ip"])
        for observation in ptr["observations"]:
            observation["records"] = sorted(observation["records"], key=lambda item: item["hostname"])
        payload = {"hosts": hosts, "ptr": ptr}
        if body.scope == "ptr":
            payload["scope"] = "ptr"
    return token_digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


@app.post("/v1/private/discovery/{task_id}/result")
def finish_discovery(task_id: UUID, body: DiscoveryResult, scanner: Scanner):
    digest = result_digest(body)
    with get_connection(read_only=False) as connection:
        # Same network lock as claim prevents an expired result racing a replacement task.
        network = connection.execute("""
            SELECT n.id, n.cidr::text AS cidr, n.ptr_enabled FROM network n JOIN discovery_task t ON t.network_id = n.id
            JOIN scanner_network sn ON sn.network_id = n.id AND sn.scanner_id = t.scanner_id AND sn.site_id = n.site_id
            WHERE t.id = %s AND t.scanner_id = %s AND t.site_id = %s AND n.enabled = 'Y'
            FOR UPDATE OF n
        """, (task_id, scanner["id"], scanner["site_id"])).fetchone()
        if network is None:
            raise HTTPException(404, "Authorized task not found")
        task = connection.execute("SELECT *, lease_until > NOW() AS valid_lease FROM discovery_task WHERE id = %s FOR UPDATE", (task_id,)).fetchone()
        if task.get("scope", "discovery") != body.scope:
            raise HTTPException(409, "Task scope does not match submitted results")
        if task["worker_id"] != body.worker_id:
            raise HTTPException(409, "Task belongs to another worker")
        if task["state"] == "completed":
            if task["result_hash"] != digest:
                raise HTTPException(409, "Task already completed with a different result")
            return {"task_id": task_id, "state": "completed", "replayed": True}
        if task["state"] != "running" or not task["valid_lease"]:
            raise HTTPException(409, "Task lease expired; request another task")
        cidr = ip_network(network["cidr"])
        if any(host.ip not in cidr for host in body.hosts):
            raise HTTPException(422, "Result contains an IP outside the assigned network")
        if body.ptr is not None:
            if network.get("ptr_enabled") != "Y":
                raise HTTPException(409, "PTR is disabled for this network")
            usable_count = cidr.num_addresses if cidr.prefixlen >= 31 else cidr.num_addresses - 2
            if len(body.ptr.observations) != usable_count or any(
                observation.ip not in cidr or (cidr.prefixlen < 31 and observation.ip in
                    (cidr.network_address, cidr.broadcast_address)) for observation in body.ptr.observations
            ):
                raise HTTPException(422, "Network PTR results must cover every usable address of the assigned network")
        ips = [str(host.ip) for host in body.hosts]
        if body.scope == "discovery":
            connection.execute("""
                UPDATE asset SET status = 'I', latency_ms = NULL
                WHERE site_id = %s AND ip <<= %s::cidr AND status = 'A'
                  AND NOT (ip = ANY(%s::inet[]))
            """, (scanner["site_id"], network["cidr"], ips))
            with connection.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO asset (site_id, ip, discovered_hostname, discovered_by_icmp, latency_ms,
                        status, first_seen, last_seen, last_seen_active)
                    VALUES (%s, %s, %s, 'Y', %s, 'A', NOW(), NOW(), NOW())
                    ON CONFLICT (site_id, ip) DO UPDATE SET
                        discovered_hostname = COALESCE(EXCLUDED.discovered_hostname, asset.discovered_hostname),
                        discovered_by_icmp = 'Y', latency_ms = EXCLUDED.latency_ms, status = 'A',
                        first_seen = COALESCE(asset.first_seen, EXCLUDED.first_seen),
                        last_seen = NOW(), last_seen_active = NOW()
                """, [(scanner["site_id"], str(h.ip), h.hostname, h.latency_ms) for h in body.hosts])
        if body.ptr is not None:
            save_network_reverse(connection, scanner, body.ptr, ips if body.scope == "discovery" else None)
        connection.execute("UPDATE discovery_task SET state = 'completed', completed_at = NOW(), result_hash = %s WHERE id = %s", (digest, task_id))
        connection.execute("UPDATE scanner SET last_seen = NOW() WHERE id = %s", (scanner["id"],))
    result = {"task_id": task_id, "state": "completed", "replayed": False}
    if body.scope == "ptr":
        result["ptr_addresses"] = len(body.ptr.observations)
    else:
        result["active_hosts"] = len(ips)
    return result


class TargetClaim(Claim):
    scope: Literal["full", "targets", "ports", "exclusive", "certificates"] = "full"
    excluded_target_ids: list[int] = Field(default_factory=list, max_length=10000)

    @field_validator("excluded_target_ids")
    @classmethod
    def positive_ids(cls, values):
        if any(value <= 0 or value > 9223372036854775807 for value in values):
            raise ValueError("Target IDs must be positive")
        return values


@app.post("/v1/private/targets/claim")
def claim_target(body: TargetClaim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        exclusive_filter = " AND EXISTS (" + TARGET_EXCLUSIVE_PORTS + ")" if body.scope == "exclusive" else ""
        target = connection.execute("""
            SELECT t.id, t.fqdn, t.status FROM target t WHERE t.site_id = %s AND t.status IN ('A','U')
              AND NOT (t.id = ANY(%s::bigint[]))
              AND NOT EXISTS (SELECT 1 FROM target_task j WHERE j.target_id = t.id
                  AND j.state = 'running' AND j.lease_until > NOW())
        """ + exclusive_filter + " ORDER BY t.id FOR UPDATE OF t SKIP LOCKED LIMIT 1", (scanner["site_id"], body.excluded_target_ids)).fetchone()
        if target is None:
            return {"task": None}
        connection.execute("UPDATE target_task SET state = 'expired' WHERE target_id = %s AND state = 'running' AND lease_until <= NOW()", (target["id"],))
        task = connection.execute("""
            INSERT INTO target_task (id, scanner_id, target_id, site_id, fqdn, worker_id, lease_until)
            VALUES (%s, %s, %s, %s, %s, %s, NOW() + %s * INTERVAL '1 second')
            RETURNING id, target_id, fqdn, worker_id, lease_until
        """, (uuid4(), scanner["id"], target["id"], scanner["site_id"], target["fqdn"], body.worker_id, max(60, int(os.getenv("DISCOVERY_LEASE_SECONDS", "90"))))).fetchone()
        snapshot = target_snapshot(connection, target, body.scope)
        connection.execute("UPDATE target_task SET config_snapshot = %s::jsonb WHERE id = %s", (json.dumps(snapshot), task["id"]))
        task.update(snapshot)
    return {"task": task}


@app.post("/v1/private/targets/{task_id}/heartbeat")
def target_heartbeat(task_id: UUID, body: Claim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        task = connection.execute("""
            UPDATE target_task SET lease_until = NOW() + %s * INTERVAL '1 second'
            WHERE id = %s AND scanner_id = %s AND site_id = %s AND worker_id = %s
              AND state = 'running' AND lease_until > NOW() RETURNING lease_until
        """, (max(60, int(os.getenv("DISCOVERY_LEASE_SECONDS", "90"))), task_id, scanner["id"], scanner["site_id"], body.worker_id)).fetchone()
        if task is None:
            raise HTTPException(409, "Target task is unavailable or expired")
    return task


@app.post("/v1/private/targets/{task_id}/cancel")
def cancel_target(task_id: UUID, body: Claim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        task = connection.execute("""
            UPDATE target_task SET state = 'expired', lease_until = NOW()
            WHERE id = %s AND scanner_id = %s AND site_id = %s AND worker_id = %s
              AND state = 'running' RETURNING id
        """, (task_id, scanner["id"], scanner["site_id"], body.worker_id)).fetchone()
    return {"task_id": task_id, "released": task is not None}


class TargetCertificate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_cn: str | None = None
    issuer_cn: str | None = None
    serial_number: str
    sans: list[str]
    valid_from: datetime
    valid_until: datetime
    fingerprint_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    tls_version: str = Field(max_length=30)
    cipher: str = Field(max_length=150)

    @model_validator(mode="after")
    def validity(self):
        if self.valid_from.tzinfo is None or self.valid_until.tzinfo is None or self.valid_until < self.valid_from:
            raise ValueError("Certificate dates require timezone and ordered validity")
        return self


class TargetPortResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    check_protocol: Literal["http", "https"]
    certificate: TargetCertificate | None = None
    state: Literal["O", "C", "U"]
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_time_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TargetResult(Claim):
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    status: Literal["A", "I", "U"]
    ports: list[TargetPortResult]


@app.post("/v1/private/targets/{task_id}/result")
def finish_target(task_id: UUID, body: TargetResult, scanner: Scanner):
    digest = token_digest(json.dumps(body.model_dump(mode="json"), sort_keys=True))
    with get_connection(read_only=False) as connection:
        target = connection.execute("""
            SELECT t.id, t.status FROM target t JOIN target_task j ON j.target_id = t.id
            WHERE j.id = %s AND j.scanner_id = %s AND j.site_id = %s
              AND t.site_id = j.site_id AND t.fqdn = j.fqdn FOR UPDATE OF t
        """, (task_id, scanner["id"], scanner["site_id"])).fetchone()
        if target is None:
            raise HTTPException(404, "Authorized target task not found or target changed")
        task = connection.execute("SELECT *, lease_until > NOW() AS valid_lease FROM target_task WHERE id = %s FOR UPDATE", (task_id,)).fetchone()
        if task["worker_id"] != body.worker_id:
            raise HTTPException(409, "Target task belongs to another worker")
        if task["state"] == "completed":
            if task["result_hash"] != digest:
                raise HTTPException(409, "Target task already completed with another result")
            return {"state": "completed", "replayed": True}
        if task["state"] != "running" or not task["valid_lease"]:
            raise HTTPException(409, "Target task expired")
        scope = task["config_snapshot"].get("scope", "full")
        snapshot = target_snapshot(connection, target, scope, lock=True)
        current_ports = snapshot["ports"]
        if task["config_snapshot"] != snapshot:
            raise HTTPException(409, "Target configuration changed; retry with a new task")
        if scope in ("ports", "exclusive", "certificates") and (body.status != target["status"] or body.latency_ms is not None):
            raise HTTPException(422, "Partial port/certificate checks cannot change target reachability")
        if target["status"] == "I":
            raise HTTPException(409, "Inactive targets are excluded from scanning")
        if target["status"] == "A" and body.status == "U":
            raise HTTPException(422, "An Active target without a ping reply becomes Inactive")
        if target["status"] == "U" and body.status == "I":
            raise HTTPException(422, "Unknown without a ping reply must remain Unknown")
        if body.status != "A" and body.latency_ms is not None:
            raise HTTPException(422, "Latency requires a successful ping")
        expected = set() if body.status == "I" or scope == "targets" else {p["id"] for p in current_ports}
        if len(body.ports) != len({p.id for p in body.ports}) or {p.id for p in body.ports} != expected:
            raise HTTPException(422, "Result must contain exactly the configured ports, or none for Inactive")
        metadata = {p["id"]: p for p in current_ports}
        for port in body.ports:
            if scope == "certificates":
                if port.check_protocol != "https" or port.http_status is not None or port.response_time_ms is not None:
                    raise HTTPException(422, "Certificate scans must not include HTTP measurements")
                if port.state != ("O" if port.certificate is not None else "U"):
                    raise HTTPException(422, "Certificate scan state must match certificate evidence")
                if port.certificate is not None:
                    save_discovered_certificate(connection, port.id, port.certificate)
                continue
            is_http = True
            is_udp = metadata[port.id]["protocol"] == "U"
            if is_udp and (port.check_protocol != "https" or port.state == "C"):
                raise HTTPException(422,"UDP targets require HTTP/3; no reply must preserve inventory")
            if is_udp and port.state == "O" and port.certificate is None:
                raise HTTPException(422,"Open HTTP/3 requires the discovered certificate")
            if port.state != "U" and (port.check_protocol == "https") != (port.certificate is not None):
                raise HTTPException(422, "HTTPS requires the discovered certificate; HTTP must not include one")
            if port.state != "O" and (port.http_status is not None or port.response_time_ms is not None):
                raise HTTPException(422, "Unavailable ports cannot include HTTP measurements")
            if port.state == "O" and is_http and (port.http_status is None or port.response_time_ms is None):
                raise HTTPException(422, "Open HTTP ports require HTTP measurements")
            if not is_http and port.http_status is not None:
                raise HTTPException(422, "TCP results cannot include HTTP status")
            # Observations can be inconclusive; inventory only stores confirmed states.
            if port.state == "U":
                continue
            if port.state == "C":
                connection.execute("""
                    UPDATE port SET state='C', last_http_status=NULL, response_time_ms=NULL,
                        check_protocol=%s, last_seen=NOW()
                    WHERE target_id=%s AND port=%s AND protocol=%s
                      AND (last_seen_open IS NOT NULL OR state='O' OR %s)
                """, (port.check_protocol, target["id"], metadata[port.id]["port"], metadata[port.id]["protocol"], scope == "exclusive"))
                continue
            if scope == "exclusive":
                connection.execute("""
                    UPDATE port SET state='O', last_http_status=%s, response_time_ms=%s,
                        check_protocol=%s, first_seen=COALESCE(first_seen,NOW()),
                        last_seen=NOW(), last_seen_open=NOW()
                    WHERE id=%s AND target_id=%s AND port=%s AND protocol=%s
                """, (port.http_status, port.response_time_ms, port.check_protocol,
                      port.id, target['id'], metadata[port.id]['port'], metadata[port.id]['protocol']))
                if port.certificate is not None:
                    save_discovered_certificate(connection, port.id, port.certificate)
                continue
            inventory_port = connection.execute("""
                INSERT INTO port (target_id, port, protocol, service, state, last_http_status, response_time_ms,
                    check_protocol, first_seen, last_seen, last_seen_open)
                SELECT c.target_id, pc.port, pc.protocol, pc.service, %s, %s, %s, %s,
                    NOW(), NOW(), CASE WHEN %s = 'O' THEN NOW() END
                FROM target_port_catalog c JOIN port_catalog pc ON pc.id = c.port_catalog_id
                WHERE c.id = %s AND c.target_id = %s
                ON CONFLICT (target_id, port, protocol) DO UPDATE SET
                    state = EXCLUDED.state, service = EXCLUDED.service, last_http_status = EXCLUDED.last_http_status,
                    response_time_ms = EXCLUDED.response_time_ms, check_protocol = EXCLUDED.check_protocol,
                    first_seen = COALESCE(port.first_seen, EXCLUDED.first_seen), last_seen = NOW(),
                    last_seen_open = COALESCE(EXCLUDED.last_seen_open, port.last_seen_open)
                RETURNING id
            """, (port.state, port.http_status, port.response_time_ms, port.check_protocol, port.state, port.id, target["id"])).fetchone()
            if port.certificate is not None and scope == "full":
                cert = port.certificate
                save_discovered_certificate(connection, inventory_port["id"], cert)
        if scope in ("full", "targets"):
            connection.execute("UPDATE target SET status = %s, latency_ms = %s, last_seen = NOW() WHERE id = %s", (body.status, body.latency_ms, target["id"]))
        connection.execute("UPDATE target_task SET state = 'completed', completed_at = NOW(), result_hash = %s WHERE id = %s", (digest, task_id))
    return {"state": "completed", "replayed": False}


def save_discovered_certificate(connection, port_id, cert):
    connection.execute("""
                    INSERT INTO certificate (port_id, subject_cn, issuer_cn, serial_number, sans,
                        valid_from, valid_until, fingerprint_sha256, tls_version, cipher, last_seen)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (port_id) DO UPDATE SET subject_cn = EXCLUDED.subject_cn,
                        issuer_cn = EXCLUDED.issuer_cn, serial_number = EXCLUDED.serial_number, sans = EXCLUDED.sans,
                        valid_from = EXCLUDED.valid_from, valid_until = EXCLUDED.valid_until,
                        fingerprint_sha256 = EXCLUDED.fingerprint_sha256, tls_version = EXCLUDED.tls_version,
                        cipher = EXCLUDED.cipher, last_seen = NOW(),
                        first_seen = CASE WHEN certificate.fingerprint_sha256 IS DISTINCT FROM EXCLUDED.fingerprint_sha256
                            THEN NOW() ELSE certificate.first_seen END
                """, (port_id, cert.subject_cn, cert.issuer_cn, cert.serial_number, json.dumps(cert.sans),
                      cert.valid_from, cert.valid_until, cert.fingerprint_sha256, cert.tls_version, cert.cipher))


def asset_scan_config(connection, asset_id, scanner):
    asset = connection.execute("""
        SELECT a.id, host(a.ip) AS ip, a.status, a.discovered_hostname,
            n.id AS network_id, n.port_mode
        FROM asset a JOIN LATERAL (
            SELECT n.id, n.port_mode FROM network n JOIN scanner_network sn
                ON sn.network_id = n.id AND sn.site_id = n.site_id
            WHERE sn.scanner_id = %s AND n.site_id = a.site_id AND n.enabled = 'Y'
                AND a.ip <<= n.cidr ORDER BY masklen(n.cidr) DESC, n.id LIMIT 1
        ) n ON TRUE WHERE a.id = %s AND a.site_id = %s AND a.status IN ('A','U')
    """, (scanner["id"], asset_id, scanner["site_id"])).fetchone()
    if asset is None:
        return None
    ports = connection.execute("""
        SELECT p.id, p.port, p.protocol, p.service FROM port_catalog p
        WHERE (
            (%s = 'I' AND EXISTS (SELECT 1 FROM site_port_catalog c WHERE c.site_id = %s AND c.port_catalog_id = p.id))
            OR (%s = 'C' AND EXISTS (SELECT 1 FROM network_port_catalog c WHERE c.network_id = %s AND c.port_catalog_id = p.id)))
        ORDER BY p.id
    """, (asset["port_mode"], scanner["site_id"], asset["port_mode"], asset["network_id"])).fetchall()
    return {**asset, "ports": ports}


class AssetPortClaim(Claim):
    scope: Literal["full", "ports", "exclusive", "certificates", "ptr"] = "full"
    dns_server: str | None = None
    dns_port: int = Field(default=53, strict=True, ge=1, le=65535)

    @field_validator("dns_server")
    @classmethod
    def server_address(cls, value):
        return normalize_server(value) if value is not None else None

    @model_validator(mode="after")
    def ptr_server(self):
        if self.scope == "ptr" and self.dns_server is None:
            raise ValueError("PTR checks require a DNS server")
        if self.scope != "ptr" and self.dns_server is not None:
            raise ValueError("DNS server is only valid for PTR checks")
        return self
    excluded_asset_ids: list[int] = Field(default_factory=list, max_length=10000)

    @field_validator("excluded_asset_ids")
    @classmethod
    def positive_ids(cls, values):
        if any(value <= 0 or value > 9223372036854775807 for value in values):
            raise ValueError("Asset IDs must be positive")
        return values


@app.post("/v1/private/asset-ports/claim")
def claim_asset_ports(body: AssetPortClaim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        if body.scope == "exclusive":
            asset = connection.execute("""
                SELECT a.id FROM asset a JOIN LATERAL (
            """ + ASSET_EXCLUSIVE_NETWORK + """) n ON TRUE
                WHERE a.site_id=%s AND a.status IN ('A','U')
                  AND NOT (a.id=ANY(%s::bigint[]))
                  AND NOT EXISTS (SELECT 1 FROM asset_port_task j WHERE j.asset_id=a.id
                    AND j.state='running' AND j.lease_until>NOW())
                  AND EXISTS (
            """ + ASSET_EXCLUSIVE_PORTS + """)
                ORDER BY a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1
            """, (scanner['id'], scanner['site_id'], body.excluded_asset_ids)).fetchone()
        elif body.scope == "ptr":
            asset = connection.execute("""
                SELECT a.id FROM asset a WHERE a.site_id=%s
                  AND NOT (a.id=ANY(%s::bigint[]))
                  AND EXISTS (SELECT 1 FROM network n JOIN scanner_network sn ON sn.network_id = n.id
                      AND sn.site_id = n.site_id WHERE sn.scanner_id = %s AND n.site_id = a.site_id
                      AND n.enabled = 'Y' AND n.ptr_enabled = 'Y' AND a.ip <<= n.cidr)
                  AND NOT EXISTS (SELECT 1 FROM asset_port_task j WHERE j.asset_id=a.id
                    AND j.state='running' AND j.lease_until>NOW())
                ORDER BY a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1
            """, (scanner["site_id"],body.excluded_asset_ids,scanner["id"])).fetchone()
        else:
            asset = connection.execute("""
                SELECT a.id FROM asset a WHERE a.site_id = %s AND a.status IN ('A','U')
                  AND NOT (a.id = ANY(%s::bigint[]))
                  AND EXISTS (SELECT 1 FROM network n JOIN scanner_network sn ON sn.network_id = n.id
                      AND sn.site_id = n.site_id WHERE sn.scanner_id = %s AND n.site_id = a.site_id
                      AND n.enabled = 'Y' AND a.ip <<= n.cidr)
                  AND NOT EXISTS (SELECT 1 FROM asset_port_task j WHERE j.asset_id = a.id
                      AND j.state = 'running' AND j.lease_until > NOW())
                ORDER BY a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1
            """, (scanner["site_id"], body.excluded_asset_ids, scanner["id"])).fetchone()
        if asset is None:
            return {"task": None}
        snapshot = asset_snapshot(connection, asset["id"], scanner, body.scope, asset_scan_config, body.dns_server, body.dns_port)
        if snapshot is None:
            return {"task": None}
        connection.execute("UPDATE asset_port_task SET state = 'expired' WHERE asset_id = %s AND state = 'running' AND lease_until <= NOW()", (asset["id"],))
        task = connection.execute("""
            INSERT INTO asset_port_task (id, scanner_id, asset_id, site_id, worker_id, config_snapshot, lease_until)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,NOW() + %s * INTERVAL '1 second')
            RETURNING id, asset_id, worker_id, lease_until
        """, (uuid4(), scanner["id"], asset["id"], scanner["site_id"], body.worker_id,
              json.dumps(snapshot), max(60,int(os.getenv("DISCOVERY_LEASE_SECONDS","90"))))).fetchone()
        task.update({key: value for key,value in snapshot.items() if key != "id"})
    return {"task": task}


@app.post("/v1/private/asset-ports/{task_id}/heartbeat")
def asset_port_heartbeat(task_id: UUID, body: Claim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        task = connection.execute("""
            UPDATE asset_port_task SET lease_until = NOW() + %s * INTERVAL '1 second'
            WHERE id = %s AND scanner_id = %s AND site_id = %s AND worker_id = %s
              AND state = 'running' AND lease_until > NOW() RETURNING lease_until
        """, (max(60,int(os.getenv("DISCOVERY_LEASE_SECONDS","90"))),task_id,scanner["id"],scanner["site_id"],body.worker_id)).fetchone()
        if task is None:
            raise HTTPException(409,"Asset port task expired or unavailable")
    return task


@app.post("/v1/private/asset-ports/{task_id}/cancel")
def cancel_asset_ports(task_id: UUID, body: Claim, scanner: Scanner):
    with get_connection(read_only=False) as connection:
        task = connection.execute("""
            UPDATE asset_port_task SET state='expired', lease_until=NOW()
            WHERE id=%s AND scanner_id=%s AND site_id=%s AND worker_id=%s AND state='running' RETURNING id
        """, (task_id,scanner["id"],scanner["site_id"],body.worker_id)).fetchone()
    return {"released":task is not None}


class AssetPortObservation(BaseModel):
    response_time_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    certificate_protocol: Literal["quic"] | None = None
    model_config = ConfigDict(extra="forbid")
    udp_scan_state: Literal["open","closed","filtered","open|filtered","closed|filtered","unfiltered","unknown","not-scanned"] | None = None
    checked: bool = True
    id: int = Field(gt=0)
    state: Literal["O","C","U"]
    certificate: TargetCertificate | None = None


class AssetPortResult(Claim):
    reverse: ReverseResult | None = None
    ports: list[AssetPortObservation]


@app.post("/v1/private/asset-ports/{task_id}/result")
def finish_asset_ports(task_id: UUID, body: AssetPortResult, scanner: Scanner):
    digest = token_digest(json.dumps(body.model_dump(mode="json"),sort_keys=True))
    with get_connection(read_only=False) as connection:
        asset = connection.execute("""
            SELECT a.id FROM asset a JOIN asset_port_task j ON j.asset_id=a.id
            WHERE j.id=%s AND j.scanner_id=%s AND j.site_id=%s AND a.site_id=j.site_id FOR UPDATE OF a
        """, (task_id,scanner["id"],scanner["site_id"])).fetchone()
        if asset is None:
            raise HTTPException(404,"Authorized asset task not found")
        task = connection.execute("SELECT *, lease_until > NOW() AS valid_lease FROM asset_port_task WHERE id=%s FOR UPDATE",(task_id,)).fetchone()
        if task["worker_id"] != body.worker_id:
            raise HTTPException(409,"Task belongs to another worker")
        if task["state"] == "completed":
            if task["result_hash"] != digest:
                raise HTTPException(409,"Task completed with a different result")
            return {"state":"completed","replayed":True}
        if task["state"] != "running" or not task["valid_lease"]:
            raise HTTPException(409,"Task expired")
        scope = task["config_snapshot"].get("scope", "full")
        snapshot = asset_snapshot(connection, asset["id"], scanner, scope, asset_scan_config,
                                  task["config_snapshot"].get("dns_server"),
                                  task["config_snapshot"].get("dns_port", 53))
        if snapshot != task["config_snapshot"]:
            raise HTTPException(409,"Asset configuration or status changed")
        if scope == "ptr":
            if body.ports or body.reverse is None:
                raise HTTPException(422, "PTR tasks require only a reverse result")
            save_reverse(connection, scanner, snapshot, body.reverse)
            connection.execute("UPDATE asset_port_task SET state='completed',completed_at=NOW(),result_hash=%s WHERE id=%s",
                               (digest,task_id))
            return {"state":"completed","replayed":False}
        if body.reverse is not None:
            raise HTTPException(422, "Reverse observations require a PTR task")
        metadata={p["id"]:p for p in snapshot["ports"]}
        if len(body.ports)!=len({p.id for p in body.ports}) or {p.id for p in body.ports}!=set(metadata):
            raise HTTPException(422,"Results must match configured ports")
        for result in body.ports:
            if scope == "certificates":
                protocol = metadata[result.id]["protocol"]
                if result.state != ("O" if result.certificate is not None else "U") or not result.checked or result.udp_scan_state is not None or result.response_time_ms is not None:
                    raise HTTPException(422, "Certificate tasks cannot report port availability")
                if result.certificate is not None:
                    if (protocol == "U") != (result.certificate_protocol == "quic"):
                        raise HTTPException(422, "UDP certificates require QUIC evidence")
                    save_discovered_certificate(connection, result.id, result.certificate)
                continue
            if (result.state != 'O' or (metadata[result.id]['protocol'] != 'T' and result.certificate_protocol != 'quic')) and result.certificate is not None:
                raise HTTPException(422,"Closed ports cannot include a certificate")
            port=metadata[result.id]
            if port["protocol"]=="U" and result.udp_scan_state is not None:
                expected_state={"open":"O","closed":"C"}.get(result.udp_scan_state,"U")
                if result.certificate_protocol == "quic" and result.certificate is not None and result.checked and expected_state != "C":
                    expected_state="O"
                if result.state!=expected_state or result.checked==(result.udp_scan_state=="not-scanned"):
                    raise HTTPException(422,"UDP result does not match Nmap evidence")
            if port["protocol"]=="T" and (not result.checked or result.udp_scan_state is not None):
                raise HTTPException(422,"TCP result cannot contain Nmap UDP evidence")
            if result.certificate_protocol is not None and (port["protocol"] != "U" or result.certificate is None or result.state != "O" or not result.checked):
                raise HTTPException(422,"QUIC evidence requires an open UDP port with a certificate")
            if result.response_time_ms is not None and (result.state != "O" or not result.checked):
                raise HTTPException(422, "Response time requires a confirmed open port")
            if not result.checked:
                if result.state != "U":
                    raise HTTPException(422,"Unsupported services cannot report Open or Closed")
                continue
            if result.state == "U":
                # Keep the last confirmed state; retain raw UDP evidence on known ports.
                if result.udp_scan_state is not None:
                    connection.execute("""
                        UPDATE port SET udp_scan_state=%s WHERE asset_id=%s AND port=%s AND protocol=%s
                    """, (result.udp_scan_state, asset["id"], port["port"], port["protocol"]))
                continue
            if result.state != "O":
                # Closed only updates services previously confirmed Open.
                # The catalog is a probe list, not discovered inventory.
                connection.execute("""
                    UPDATE port SET state=%s, service=%s, last_seen=NOW(), udp_scan_state=%s, response_time_ms=NULL
                    WHERE asset_id=%s AND port=%s AND protocol=%s
                      AND (last_seen_open IS NOT NULL OR state='O' OR %s)
                """, (result.state,port["service"],result.udp_scan_state,
                      asset["id"],port["port"],port["protocol"],scope == "exclusive"))
                continue
            inventory=connection.execute("""
                INSERT INTO port (asset_id,port,protocol,service,state,first_seen,last_seen,last_seen_open,response_time_ms)
                VALUES (%s,%s,%s,%s,%s,NOW(),NOW(),CASE WHEN %s='O' THEN NOW() END,%s)
                ON CONFLICT (asset_id,port,protocol) DO UPDATE SET state=EXCLUDED.state,service=EXCLUDED.service,
                    response_time_ms=EXCLUDED.response_time_ms,
                    first_seen=COALESCE(port.first_seen,EXCLUDED.first_seen),last_seen=NOW(),
                    last_seen_open=COALESCE(EXCLUDED.last_seen_open,port.last_seen_open) RETURNING id
            """, (asset["id"],port["port"],port["protocol"],port["service"],result.state,result.state,result.response_time_ms)).fetchone()
            if port["protocol"]=="U":
                connection.execute("UPDATE port SET udp_scan_state=%s WHERE id=%s",(result.udp_scan_state,inventory["id"]))
            if result.certificate is not None and scope in ("full", "exclusive"):
                save_discovered_certificate(connection,inventory["id"],result.certificate)
        connection.execute("UPDATE asset_port_task SET state='completed',completed_at=NOW(),result_hash=%s WHERE id=%s",(digest,task_id))
    return {"state":"completed","replayed":False}


from app.scanner_config import create_configuration_router
app.include_router(create_configuration_router(scanner_identity))


# DNS collectors authenticate as scanners and inherit their Site scope.
from app.dns_inventory import create_dns_router
app.include_router(create_dns_router(scanner_identity))

from app.public import router as public_router
app.include_router(public_router)
