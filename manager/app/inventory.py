from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.config import load_config


router = APIRouter(prefix="/api")
TLS_POLICY = load_config()["tls"]
WARNING_DAYS = TLS_POLICY["expiration"]["warning_days"]
CRITICAL_DAYS = TLS_POLICY["expiration"]["critical_days"]

ASSET_COLUMNS = """
    id, site_id, host(ip) AS ip, discovered_hostname, display_name, asset_type_id,
    (SELECT name FROM asset_type WHERE asset_type.id = asset.asset_type_id) AS type_name,
    operating_system_id,
    (SELECT name FROM operating_system WHERE operating_system.id = asset.operating_system_id) AS operating_system_name,
    discovered_by_icmp, latency_ms, status, first_seen, last_seen, last_seen_active
"""
PORT_COLUMNS = """
    p.id, p.asset_id, p.target_id, p.port, p.protocol, p.state, p.service,
    p.response_time_ms, p.first_seen, p.last_seen, p.last_seen_open
"""
CERTIFICATE_COLUMNS = """
    c.id, c.port_id, c.asset_id, c.subject_cn, c.issuer_cn, c.serial_number, c.sans,
    c.valid_from, c.valid_until, c.fingerprint_sha256,
    c.tls_version, c.cipher, c.first_seen, c.last_seen, c.note
"""
ASSET_JSON = """
    CASE WHEN a.id IS NULL THEN NULL ELSE json_build_object('id', a.id, 'site_id', a.site_id, 'ip', host(a.ip),
        'discovered_hostname', a.discovered_hostname,
        'display_name', a.display_name,
        'name', COALESCE(a.display_name, a.discovered_hostname), 'status', a.status) END AS asset
"""

TARGET_JSON = """
    CASE WHEN t.id IS NULL THEN NULL ELSE
        json_build_object('id', t.id, 'site_id', t.site_id, 'fqdn', t.fqdn,
            'status', t.status) END AS target
"""


def build_asset(row):
    row = dict(row)
    row["name"] = row.get("display_name") or row.get("discovered_hostname")
    row["status"] = {"A": "active", "I": "inactive", "U": "unknown"}[row["status"]]
    if "discovered_by_icmp" in row:
        row["discovered_by_icmp"] = row["discovered_by_icmp"] == "Y"
    return row


def build_port(row):
    row = dict(row)
    row["protocol"] = {"T": "TCP", "U": "UDP"}[row["protocol"]]
    row["state"] = {"O": "open", "C": "closed"}[row["state"]]
    if row.get("asset") is not None:
        row["asset"] = build_asset(row["asset"])
    return row


def certificate_expiration(valid_until, now=None):
    now = now or datetime.now(timezone.utc)
    seconds = (valid_until - now).total_seconds()
    days = int(seconds // 86400)
    status = (
        "expired" if seconds <= 0 else
        "critical" if days <= CRITICAL_DAYS else
        "warning" if days <= WARNING_DAYS else "valid"
    )
    return {"days_remaining":days, "expired":seconds <= 0, "status":status}


def build_certificate(row, now=None):
    row = dict(row)
    row.update(certificate_expiration(row["valid_until"], now))
    version = row.get("tls_version")
    tls_status = TLS_POLICY["versions"].get(version, TLS_POLICY["default"]) if version else "unknown"
    row["tls_status"] = tls_status
    row["sans"] = row["sans"] or []
    if row.get("asset") is not None:
        row["asset"] = build_asset(row["asset"])
    if row.get("port") is not None:
        row["port"] = build_port(row["port"])
    return row


@router.get("/assets")
def assets():
    with get_connection() as connection:
        rows = connection.execute(f"SELECT {ASSET_COLUMNS}, (SELECT name FROM site WHERE site.id = asset.site_id) AS site_name FROM asset ORDER BY site_id, ip").fetchall()
    return [build_asset(row) for row in rows]


@router.get("/ports")
def ports():
    with get_connection() as connection:
        rows = connection.execute(f"""
            SELECT {PORT_COLUMNS}, {ASSET_JSON}, {TARGET_JSON}, s.name AS site_name
            FROM port p LEFT JOIN asset a ON a.id = p.asset_id
            LEFT JOIN target t ON t.id = p.target_id
            JOIN site s ON s.id = COALESCE(a.site_id, t.site_id)
            ORDER BY COALESCE(a.site_id, t.site_id), a.ip, t.fqdn, p.port, p.protocol
        """).fetchall()
    return [build_port(row) for row in rows]


@router.get("/ports/top-discovered")
def top_discovered_ports():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT port, protocol, count(*) AS total
            FROM port
            WHERE last_seen_open IS NOT NULL
            GROUP BY port, protocol
            ORDER BY total DESC, port ASC, protocol ASC
            LIMIT 10
        """).fetchall()
    return [{**dict(row), "protocol": {"T": "TCP", "U": "UDP"}[row["protocol"]]} for row in rows]


@router.get("/certificates")
def certificates():
    with get_connection() as connection:
        rows = connection.execute(f"""
            SELECT {CERTIFICATE_COLUMNS}, {ASSET_JSON}, {TARGET_JSON}, s.name AS site_name,
                CASE WHEN p.id IS NULL THEN NULL ELSE json_build_object('id', p.id, 'port', p.port,
                    'protocol', p.protocol, 'state', p.state) END AS port
            FROM certificate c LEFT JOIN port p ON p.id = c.port_id
            LEFT JOIN asset a ON a.id = COALESCE(c.asset_id, p.asset_id)
            LEFT JOIN target t ON t.id = p.target_id
            JOIN site s ON s.id = COALESCE(a.site_id, t.site_id)
            ORDER BY c.valid_until, c.id
        """).fetchall()
    now = datetime.now(timezone.utc)
    return [build_certificate(row, now) for row in rows]


@router.get("/assets/{asset_id}")
def asset_detail(asset_id: int):
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT {ASSET_COLUMNS} FROM asset WHERE id = %s", (asset_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        port_rows = connection.execute(f"""
            SELECT {PORT_COLUMNS},
                CASE WHEN c.id IS NULL THEN NULL ELSE row_to_json(c) END AS certificate
            FROM port p LEFT JOIN certificate c ON c.port_id = p.id
            WHERE p.asset_id = %s ORDER BY p.port, p.protocol
        """, (asset_id,)).fetchall()
    result = build_asset(row)
    result["ports"] = []
    for port in port_rows:
        port = build_port(port)
        if port["certificate"] is not None:
            certificate = port["certificate"]
            certificate["valid_until"] = datetime.fromisoformat(certificate["valid_until"])
            port["certificate"] = build_certificate(certificate)
        result["ports"].append(port)
    return result


@router.get("/summary")
def summary():
    with get_connection() as connection:
        # All counters describe the same database snapshot.
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assets = connection.execute("""
            SELECT count(*) AS total,
                count(*) FILTER (WHERE status = 'A') AS active,
                count(*) FILTER (WHERE status = 'I') AS inactive,
                count(*) FILTER (WHERE status = 'U') AS unknown FROM asset
        """).fetchone()
        ports = connection.execute("""
            SELECT count(*) AS total,
                count(*) FILTER (WHERE state = 'O') AS open,
                count(*) FILTER (WHERE state = 'C') AS closed,
                count(*) FILTER (WHERE protocol = 'T') AS tcp,
                count(*) FILTER (WHERE protocol = 'U') AS udp FROM port
        """).fetchone()
        targets = connection.execute("""
            SELECT count(*) AS total,
                count(*) FILTER (WHERE status = 'A') AS active,
                count(*) FILTER (WHERE status = 'I') AS inactive,
                count(*) FILTER (WHERE status = 'U') AS unknown FROM target
        """).fetchone()
        http_status = connection.execute("""
            SELECT count(*) FILTER (WHERE last_http_status BETWEEN 200 AND 299) AS success,
                count(*) FILTER (WHERE last_http_status BETWEEN 300 AND 399) AS redirect,
                count(*) FILTER (WHERE last_http_status BETWEEN 400 AND 499) AS client_error,
                count(*) FILTER (WHERE last_http_status BETWEEN 500 AND 599) AS server_error,
                count(*) FILTER (WHERE last_http_status IS NOT NULL AND
                    (last_http_status < 200 OR last_http_status >= 600)) AS other,
                count(*) FILTER (WHERE last_http_status IS NULL) AS unchecked
            FROM port WHERE target_id IS NOT NULL AND last_seen IS NOT NULL AND check_protocol IS NOT NULL
        """).fetchone()
        connections = connection.execute("""
            SELECT count(*) FILTER (WHERE p.protocol = 'T') AS tcp,
                count(*) FILTER (WHERE p.protocol = 'U') AS udp,
                count(*) FILTER (WHERE c.asset_id IS NOT NULL) AS internal
            FROM certificate c LEFT JOIN port p ON p.id = c.port_id
        """).fetchone()
        rows = connection.execute("SELECT valid_until FROM certificate").fetchall()
    counts = dict(total=len(rows), valid=0, warning=0, critical=0, expired=0)
    now = datetime.now(timezone.utc)
    for row in rows:
        counts[build_certificate({**row, "sans": []}, now)["status"]] += 1
    targets["http_status"] = http_status
    counts["connections"] = connections
    return {"assets": assets, "targets": targets, "ports": ports, "certificates": counts}
