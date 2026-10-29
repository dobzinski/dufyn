"""Purpose-specific, anonymous, read-only monitoring endpoints."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

import yaml
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.database import get_connection

def without_internal_ids(value):
    if isinstance(value, dict):
        return {key: without_internal_ids(item) for key, item in value.items()
                if key != "id" and not key.endswith(("_id", "_ids"))}
    if isinstance(value, (list, tuple)):
        return [without_internal_ids(item) for item in value]
    return value


class PublicJSONResponse(JSONResponse):
    def render(self, content):
        return super().render(without_internal_ids(content))


router = APIRouter(prefix="/v1/public", tags=["public monitoring"],
                   default_response_class=PublicJSONResponse)


def load_tls_policy():
    configured_path = os.getenv("TLS_POLICY_FILE")
    path = Path(configured_path) if configured_path else Path(__file__).resolve().parent.parent / "config.yaml"
    if configured_path or path.exists():
        with path.open(encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
    else:
        config = {}
    if not isinstance(config, dict) or not isinstance(config.get("tls", {}), dict):
        raise ValueError("TLS configuration must contain a mapping")
    tls = config.get("tls", {})
    expiration = tls.get("expiration", {})
    if not isinstance(expiration, dict):
        raise ValueError("tls.expiration must contain a mapping")
    warning = expiration.get("warning_days", 90)
    critical = expiration.get("critical_days", 30)
    if type(warning) is not int or type(critical) is not int or not 0 <= critical <= warning:
        raise ValueError("tls.expiration must satisfy 0 <= critical_days <= warning_days using integers")
    versions = tls.get("versions", {
        "SSLv2": "critical", "SSLv3": "critical", "TLSv1": "critical",
        "TLSv1.1": "critical", "TLSv1.2": "warning", "TLSv1.3": "valid",
    })
    statuses = {"critical", "warning", "valid", "unknown"}
    if not isinstance(versions, dict) or any(
        not isinstance(version, str) or not isinstance(status, str) or status not in statuses
        for version, status in versions.items()
    ):
        raise ValueError("tls.versions contains an invalid classification")
    default = tls.get("default", "unknown")
    if not isinstance(default, str) or default not in statuses:
        raise ValueError("tls.default contains an invalid classification")
    return {"warning_days": warning, "critical_days": critical, "versions": versions, "default": default}


def load_newers_days():
    # Use the Controller's shared config, independent of an optional TLS-only file.
    path = Path(__file__).resolve().parent.parent / "config.yaml"
    if path.exists():
        with path.open(encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
    else:
        config = {}
    if not isinstance(config, dict) or not isinstance(config.get("newers", {}), dict):
        raise ValueError("newers must contain a mapping")
    days = config.get("newers", {}).get("days", 0)
    if type(days) is not int or not 0 <= days <= 36500:
        raise ValueError("newers.days must be an integer between 0 and 36500")
    return days


TLS_POLICY = load_tls_policy()
NEWERS_DAYS = load_newers_days()

# One canonical expression drives both summary counts and attention filtering.
EXPIRATION_STATUS = """CASE
    WHEN c.valid_until <= %(now)s THEN 'expired'
    WHEN floor(extract(epoch FROM (c.valid_until - %(now)s::timestamptz))/86400) <= %(critical_days)s THEN 'critical'
    WHEN floor(extract(epoch FROM (c.valid_until - %(now)s::timestamptz))/86400) <= %(warning_days)s THEN 'warning'
    ELSE 'valid' END"""

CERTIFICATE_SELECT = """SELECT s.name AS site_name,
    CASE WHEN a.id IS NULL THEN NULL ELSE jsonb_build_object(
        'name',a.display_name,'hostname',a.discovered_hostname,'ip',host(a.ip),
        'port',p.port,'protocol',CASE p.protocol WHEN 'T' THEN 'TCP' WHEN 'U' THEN 'UDP' END)
        END AS assets,
    CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object(
        'name',t.fqdn,'port',p.port,
        'protocol',CASE p.protocol WHEN 'T' THEN 'TCP' WHEN 'U' THEN 'UDP' END)
        END AS targets,
    c.note AS notes,c.last_seen,c.subject_cn AS cn,c.issuer_cn AS issuer,
    c.valid_from,c.valid_until,c.tls_version
    FROM certificate c LEFT JOIN port p ON p.id=c.port_id
    LEFT JOIN asset a ON a.id=COALESCE(c.asset_id,p.asset_id)
    LEFT JOIN target t ON t.id=p.target_id
    JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)"""
WEB_SELECT = """SELECT s.name AS site_name,t.fqdn,
    CASE t.status WHEN 'A' THEN 'Active' WHEN 'I' THEN 'Inactive' ELSE 'Unknown' END AS status,
    t.last_seen,t.created_at,t.latency_ms,latest.last_http_status
    FROM target t JOIN site s ON s.id=t.site_id
    JOIN LATERAL (
        SELECT p.last_http_status FROM port p WHERE p.target_id=t.id
            AND p.last_seen IS NOT NULL AND p.check_protocol IS NOT NULL
        ORDER BY p.last_seen DESC,p.id DESC LIMIT 1
    ) latest ON true"""

MANAGEMENT_QUERY = """SELECT
    (SELECT count(*) FROM site) AS sites,
    (SELECT count(*) FROM network) AS networks,
    (SELECT count(*) FROM scanner) AS scanners,
    (SELECT count(*) FROM port_catalog) AS services,
    (SELECT count(*) FROM asset_type) AS types,
    (SELECT count(*) FROM operating_system) AS os,
    (SELECT count(*) FROM asset) AS assets,
    (SELECT count(*) FROM target) AS targets,
    (SELECT count(*) FROM port) AS ports,
    (SELECT count(*) FROM certificate) AS certificates,
    (SELECT count(*) FROM dns_zone) AS dns_zones,
    (SELECT count(*) FROM dns_record) AS dns_records,
    (SELECT count(*) FROM topology_diagram) AS diagrams,
    (SELECT count(*) FROM topology_view WHERE type='physical') AS physical,
    (SELECT count(*) FROM topology_view WHERE type='logical') AS logical,
    (SELECT count(*) FROM topology_interconnection) AS site_links"""
DNS_ZONE_COUNTS_QUERY = """SELECT s.name AS site_name, z.name,
    host(z.dns_server) AS dns_server, z.dns_port, z.last_synced_at,
    count(r.id) AS total,
    count(r.id) FILTER (WHERE r.record_type='A') AS a,
    count(r.id) FILTER (WHERE r.record_type='AAAA') AS aaaa,
    count(r.id) FILTER (WHERE r.record_type='CNAME') AS cname
    FROM dns_zone z JOIN site s ON s.id=z.site_id
    LEFT JOIN dns_record r ON r.zone_id=z.id
    GROUP BY z.id,s.name ORDER BY z.site_id,z.name,z.id"""


def classification_query(table, foreign_key):
    # Callers use fixed internal identifiers, never request values.
    return f"""SELECT name,total,assigned FROM (SELECT c.id,c.name,count(a.id) AS total,true AS assigned
        FROM {table} c LEFT JOIN asset a ON a.{foreign_key}=c.id
        GROUP BY c.id,c.name
        UNION ALL SELECT NULL,'Unassigned',count(*),false FROM asset WHERE {foreign_key} IS NULL
        HAVING count(*) > 0) distribution
        ORDER BY total DESC,name ASC NULLS LAST,id ASC NULLS LAST"""


OS_COUNTS_QUERY = classification_query("operating_system", "operating_system_id")
TYPE_COUNTS_QUERY = classification_query("asset_type", "asset_type_id")
PORT_COUNTS_QUERY = """SELECT port,
    CASE protocol WHEN 'T' THEN 'TCP' ELSE 'UDP' END AS protocol,count(*) AS total
    FROM port WHERE last_seen_open IS NOT NULL GROUP BY port,protocol
    ORDER BY total DESC,port,protocol"""


def expiration_params(now):
    return {"now": now, "critical_days": TLS_POLICY["critical_days"], "warning_days": TLS_POLICY["warning_days"]}


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def build_certificate(row, now):
    result = {key: row.get(key) for key in (
        "site_name", "notes", "last_seen", "cn", "issuer", "valid_from", "valid_until")}
    for field, keys in (("assets", ("name", "hostname", "ip", "port", "protocol")),
                        ("targets", ("name", "port", "protocol"))):
        entity = row.get(field)
        result[field] = {key: entity.get(key) for key in keys} if entity is not None else None
    seconds = (timestamp(result["valid_until"]) - now).total_seconds()
    days = int(seconds // 86400)
    result.update(days_remaining=days, expired=seconds <= 0, status=(
        "expired" if seconds <= 0 else "critical" if days <= TLS_POLICY["critical_days"]
        else "warning" if days <= TLS_POLICY["warning_days"] else "valid"))
    version = row.get("tls_version")
    result["tls_status"] = TLS_POLICY["versions"].get(version, TLS_POLICY["default"]) if version else "unknown"
    return result


def build_web_check(row):
    result = {key: row.get(key) for key in (
        "site_name", "fqdn", "status", "last_seen", "created_at", "latency_ms", "last_http_status")}
    result["status"] = {"A": "Active", "I": "Inactive", "U": "Unknown"}.get(
        result["status"], result["status"] if result["status"] in ("Active", "Inactive", "Unknown") else "Unknown")
    return result


def summary(connection, now):
    result = {}
    for table, key in (("asset", "assets"), ("target", "targets")):
        result[key] = connection.execute(f"""SELECT count(*) AS total,
            count(*) FILTER (WHERE status='A') AS active,
            count(*) FILTER (WHERE status='I') AS inactive,
            count(*) FILTER (WHERE status='U') AS unknown FROM {table}""").fetchone()
    result["ports"] = connection.execute("""SELECT count(*) AS total,
        count(*) FILTER (WHERE state='O') AS open,
        count(*) FILTER (WHERE state='C') AS closed,
        count(*) FILTER (WHERE protocol='T') AS tcp,
        count(*) FILTER (WHERE protocol='U') AS udp FROM port""").fetchone()
    result["targets"]["http_status"] = connection.execute("""SELECT
        count(*) FILTER (WHERE last_http_status BETWEEN 200 AND 299) AS success,
        count(*) FILTER (WHERE last_http_status BETWEEN 300 AND 399) AS redirect,
        count(*) FILTER (WHERE last_http_status BETWEEN 400 AND 499) AS client_error,
        count(*) FILTER (WHERE last_http_status BETWEEN 500 AND 599) AS server_error,
        count(*) FILTER (WHERE last_http_status IS NOT NULL AND
            (last_http_status<200 OR last_http_status>=600)) AS other,
        count(*) FILTER (WHERE last_http_status IS NULL) AS unchecked
        FROM port WHERE target_id IS NOT NULL AND last_seen IS NOT NULL AND check_protocol IS NOT NULL""").fetchone()
    result["certificates"] = connection.execute(f"""SELECT count(*) AS total,
        count(*) FILTER (WHERE status='valid') AS valid,
        count(*) FILTER (WHERE status='warning') AS warning,
        count(*) FILTER (WHERE status='critical') AS critical,
        count(*) FILTER (WHERE status='expired') AS expired
        FROM (SELECT {EXPIRATION_STATUS} AS status FROM certificate c) classified""", expiration_params(now)).fetchone()
    certs = result["certificates"]
    certs["attention"] = certs["warning"] + certs["critical"] + certs["expired"]
    certs["connections"] = connection.execute("""SELECT
        count(*) FILTER (WHERE p.protocol='T') AS tcp,
        count(*) FILTER (WHERE p.protocol='U') AS udp,
        count(*) FILTER (WHERE c.asset_id IS NOT NULL) AS internal
        FROM certificate c LEFT JOIN port p ON p.id=c.port_id""").fetchone()
    return result


@router.get("/summary")
def public_summary():
    """All Dashboard quantities and complete distributions, from one snapshot."""
    with get_connection() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        now = datetime.now(timezone.utc)
        result = summary(connection, now)
        result["generated_at"] = now
        result["management"] = connection.execute(MANAGEMENT_QUERY).fetchone()
        result["distributions"] = {
            "operating_systems": connection.execute(OS_COUNTS_QUERY).fetchall(),
            "types": connection.execute(TYPE_COUNTS_QUERY).fetchall(),
            "ports": connection.execute(PORT_COUNTS_QUERY).fetchall(),
        }
        result["dns_zones"] = connection.execute(DNS_ZONE_COUNTS_QUERY).fetchall()
        result["expiration_policy"] = {key: TLS_POLICY[key] for key in ("warning_days", "critical_days")}
        result["dns_ptr"] = ptr_summary_counts(connection)
    return result


@router.get("/management")
def management():
    with get_connection() as connection:
        return connection.execute(MANAGEMENT_QUERY).fetchone()


@router.get("/distributions/operating-systems")
def operating_system_counts():
    with get_connection() as connection:
        return connection.execute(OS_COUNTS_QUERY).fetchall()


@router.get("/distributions/types")
def type_counts():
    with get_connection() as connection:
        return connection.execute(TYPE_COUNTS_QUERY).fetchall()


@router.get("/distributions/ports")
def port_counts():
    with get_connection() as connection:
        return connection.execute(PORT_COUNTS_QUERY).fetchall()


@router.get("/dns/zones/counts")
def dns_zone_counts():
    with get_connection() as connection:
        return connection.execute(DNS_ZONE_COUNTS_QUERY).fetchall()


Limit = Query(default=100, ge=1, le=1000)
Offset = Query(default=0, ge=0)


@router.get("/certificates")
def certificates(limit: int = Limit, offset: int = Offset):
    """Minimal certificate inventory and expiration information."""
    with get_connection() as connection:
        rows = connection.execute(CERTIFICATE_SELECT + " ORDER BY c.valid_until,c.id LIMIT %s OFFSET %s", (limit, offset)).fetchall()
    now = datetime.now(timezone.utc)
    return [build_certificate(row, now) for row in rows]


@router.get("/attention")
def attention(limit: int = Limit, offset: int = Offset):
    """Expired certificates and those in the warning/critical expiration window."""
    now = datetime.now(timezone.utc)
    params = {**expiration_params(now), "limit": limit, "offset": offset}
    with get_connection() as connection:
        rows = connection.execute(CERTIFICATE_SELECT + f" WHERE ({EXPIRATION_STATUS}) <> 'valid'"
            + " ORDER BY c.valid_until,c.id LIMIT %(limit)s OFFSET %(offset)s", params).fetchall()
    return [build_certificate(row, now) for row in rows]


@router.get("/web-checks")
def web_checks(limit: int = Limit, offset: int = Offset):
    with get_connection() as connection:
        rows = connection.execute(WEB_SELECT + " ORDER BY t.fqdn,t.id LIMIT %s OFFSET %s", (limit, offset)).fetchall()
    return [build_web_check(row) for row in rows]

# Recent inventory uses first discovery/creation dates, never recurring last_seen.
NEWERS_QUERIES = {
    "assets": """SELECT s.name AS site_name,a.display_name AS name,
        a.discovered_hostname AS hostname,host(a.ip) AS ip,
        CASE a.status WHEN 'A' THEN 'Active' WHEN 'I' THEN 'Inactive' ELSE 'Unknown' END AS status,
        a.first_seen FROM asset a JOIN site s ON s.id=a.site_id
        WHERE a.first_seen >= %(since)s AND a.first_seen <= %(now)s""",
    "targets": """SELECT s.name AS site_name,t.fqdn,
        CASE t.status WHEN 'A' THEN 'Active' WHEN 'I' THEN 'Inactive' ELSE 'Unknown' END AS status,
        t.created_at FROM target t JOIN site s ON s.id=t.site_id
        WHERE t.created_at >= %(since)s AND t.created_at <= %(now)s""",
    "ports": """SELECT s.name AS site_name,a.display_name,a.discovered_hostname,
        host(a.ip) AS ip,t.fqdn,p.port,
        CASE p.protocol WHEN 'T' THEN 'TCP' WHEN 'U' THEN 'UDP' END AS protocol,
        CASE p.state WHEN 'O' THEN 'open' WHEN 'C' THEN 'closed' END AS state,
        p.response_time_ms,p.first_seen
        FROM port p LEFT JOIN asset a ON a.id=p.asset_id
        LEFT JOIN target t ON t.id=p.target_id
        JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)
        WHERE p.first_seen >= %(since)s AND p.first_seen <= %(now)s""",
    "certificates": CERTIFICATE_SELECT.replace("SELECT s.name AS site_name,", "SELECT c.first_seen,s.name AS site_name,", 1)
        + " WHERE c.first_seen >= %(since)s AND c.first_seen <= %(now)s",
    "dns": """SELECT s.name AS site_name,z.name AS zone_name,
        host(z.dns_server) AS dns_server,z.dns_port,d.name,d.record_type,
        host(d.address) AS ip,d.target_name,d.first_seen
        FROM dns_record d JOIN dns_zone z ON z.id=d.zone_id JOIN site s ON s.id=z.site_id
        WHERE d.first_seen >= %(since)s AND d.first_seen <= %(now)s""",
    "ptr": """SELECT s.name AS site_name,host(r.ip) AS ip,
        host(r.dns_server) AS dns_server,r.dns_port,p.hostname,p.first_seen
        FROM dns_ptr_record p JOIN dns_reverse_check r ON r.id=p.reverse_check_id
        JOIN site s ON s.id=r.site_id WHERE p.first_seen <= %(now)s AND r.result='answered'
        AND p.first_seen >= %(since)s""",
}
NEWERS_ORDER = {
    "assets": "a.first_seen DESC,a.id DESC",
    "targets": "t.created_at DESC,t.id DESC",
    "ports": "p.first_seen DESC,p.id DESC",
    "certificates": "c.first_seen DESC,c.id DESC",
    "dns": "d.first_seen DESC,d.id DESC",
    "ptr": "first_seen DESC,s.name,r.ip,r.dns_server,r.dns_port,p.hostname",
}


@router.get("/newers")
def newers(limit: int = Limit, offset: int = Offset):
    now = datetime.now(timezone.utc)
    enabled = NEWERS_DAYS > 0
    since = now - timedelta(days=NEWERS_DAYS) if enabled else None
    result = {"enabled": enabled, "days": NEWERS_DAYS, "generated_at": now, "since": since,
              "limit": limit, "offset": offset,
              "totals": {key: 0 for key in NEWERS_QUERIES},
              "has_more": {key: False for key in NEWERS_QUERIES},
              "records": {key: [] for key in NEWERS_QUERIES}}
    if not enabled:
        return result
    params = {"now": now, "since": since, "limit": limit, "offset": offset}
    with get_connection() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        for resource, sql in NEWERS_QUERIES.items():
            total = connection.execute("SELECT count(*) AS total FROM (" + sql + ") recent", params).fetchone()["total"]
            rows = connection.execute(sql + " ORDER BY " + NEWERS_ORDER[resource]
                + " LIMIT %(limit)s OFFSET %(offset)s", params).fetchall()
            if resource == "certificates":
                rows = [{**build_certificate(row, now), "first_seen": row["first_seen"]} for row in rows]
            result["totals"][resource] = total
            result["records"][resource] = rows
            result["has_more"][resource] = offset + len(rows) < total
    return result

PTR_QUERY = """SELECT s.name AS site_name,host(latest.ip) AS ip,
    host(latest.dns_server) AS dns_server,latest.dns_port,latest.result,latest.checked_at,
    (a.id IS NOT NULL) AS registered,(a.discovered_by_icmp='Y') IS TRUE AS discovered,
    a.display_name AS registered_name,
    a.discovered_hostname AS hostname,
    CASE a.status WHEN 'A' THEN 'Active' WHEN 'I' THEN 'Inactive' WHEN 'U' THEN 'Unknown' END AS asset_status,
    icmp.icmp_responded,icmp.checked_at AS icmp_checked_at,
    ARRAY(SELECT p.hostname FROM dns_ptr_record p WHERE p.reverse_check_id=latest.id ORDER BY p.hostname) AS ptr_names,
    ARRAY(SELECT DISTINCT d.name FROM dns_record d JOIN dns_zone z ON z.id=d.zone_id
          WHERE z.site_id=latest.site_id AND d.address=latest.ip AND d.record_type IN ('A','AAAA')
          ORDER BY d.name) AS dns_names
    FROM (SELECT DISTINCT ON (site_id,ip,dns_server,dns_port) * FROM dns_reverse_check c
          WHERE EXISTS (SELECT 1 FROM dns_ptr_record p WHERE p.reverse_check_id=c.id)
          ORDER BY site_id,ip,dns_server,dns_port,checked_at DESC,id DESC) latest
    JOIN site s ON s.id=latest.site_id
    LEFT JOIN asset a ON a.site_id=latest.site_id AND a.ip=latest.ip
    LEFT JOIN LATERAL (
        SELECT c.icmp_responded,c.checked_at FROM dns_reverse_check c
        WHERE c.site_id=latest.site_id AND c.ip=latest.ip AND c.icmp_responded IS NOT NULL
        ORDER BY c.checked_at DESC,c.id DESC LIMIT 1
    ) icmp ON true
    WHERE latest.result='answered' AND EXISTS (
        SELECT 1 FROM dns_ptr_record p WHERE p.reverse_check_id=latest.id)"""


def normalized_hostname(value):
    import re
    from ipaddress import ip_address
    if not isinstance(value, str):
        return None
    name = value.strip().rstrip('.').lower()
    try:
        name = name.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    if not name or len(name) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                         for label in name.split('.')):
        return None
    try:
        ip_address(name)
        return None
    except ValueError:
        return name


def build_ptr(row):
    expected = sorted({name for value in [row.get('registered_name'), row.get('hostname'), *row.get('dns_names', [])]
                       if (name := normalized_hostname(value)) is not None})
    ptr_names = row.get('ptr_names') or []
    answered = row['result'] == 'answered' and bool(ptr_names)
    matches = all(normalized_hostname(name) in expected for name in ptr_names) if answered and expected else None
    if answered:
        comparison = ('Match' if matches else 'Mismatch' if expected else
                      'Unnamed' if row['registered'] else 'Unregistered')
    else:
        comparison = 'Missing' if row['result'] in ('no_ptr','nxdomain') else 'Failed'
    reasons = (["Undiscovered"] if answered and not row.get('discovered', False) else [])
    if row.get('asset_status') == 'Inactive':
        reasons.append('Inactive')
    if matches is False:
        reasons.append('Mismatch')
    return {
        'attention': bool(reasons), 'attention_reasons': reasons,
        'site_name': row['site_name'], 'ip': row['ip'], 'dns_server': row['dns_server'],
        'dns_port': row['dns_port'], 'registered': row['registered'],
        'discovered': row.get('discovered', False),
        'undiscovered': answered and not row.get('discovered', False),
        'asset_status': row.get('asset_status'), 'expected_names': expected, 'ptr_names': ptr_names,
        'name_matches': matches, 'comparison': comparison,
        'result': {'answered':'Answered','no_ptr':'No PTR','nxdomain':'NXDOMAIN','timeout':'Timeout',
                   'servfail':'Server failure','refused':'Refused','error':'Error'}[row['result']],
        'icmp_responded': row.get('icmp_responded'), 'icmp_checked_at': row.get('icmp_checked_at'),
        'suspected_stale': answered and row.get('icmp_responded') is not True
            and (not row['registered'] or row.get('asset_status') == 'Inactive'
                 or (row.get('asset_status') == 'Unknown' and row.get('icmp_responded') is False)),
        'checked_at': row['checked_at'],
    }


@router.get('/dns/ptr')
def dns_ptr(limit: int = Limit, offset: int = Offset, undiscovered: bool = False):
    condition = " AND a.discovered_by_icmp IS DISTINCT FROM 'Y'" if undiscovered else ""
    with get_connection() as connection:
        rows = connection.execute(PTR_QUERY + condition +
            ' ORDER BY s.name,latest.ip,latest.dns_server,latest.dns_port LIMIT %s OFFSET %s',
            (limit,offset)).fetchall()
    return [build_ptr(row) for row in rows]


PTR_SUMMARY_QUERY = """WITH observations AS (""" + PTR_QUERY + """), answers AS (
    SELECT site_name,ip,unnest(ptr_names) AS hostname,discovered,asset_status
    FROM observations WHERE result='answered'
)
SELECT
    (SELECT count(DISTINCT (site_name,ip)) FROM answers) AS ips_with_ptr,
    (SELECT count(DISTINCT (site_name,ip,hostname)) FROM answers) AS ptr_records,
    (SELECT count(DISTINCT (site_name,ip)) FROM answers WHERE NOT discovered) AS undiscovered,
    (SELECT count(DISTINCT (site_name,ip,hostname)) FROM answers WHERE NOT discovered) AS ptr_records_undiscovered,
    (SELECT count(DISTINCT (site_name,ip)) FROM answers WHERE discovered AND asset_status='Inactive') AS inactive
"""


def ptr_summary_counts(connection):
    result = dict(connection.execute(PTR_SUMMARY_QUERY).fetchone())
    records = [build_ptr(row) for row in connection.execute(PTR_QUERY).fetchall()]
    result['mismatch'] = len({(row['site_name'], row['ip']) for row in records if row['comparison'] == 'Mismatch'})
    result['attention'] = len({(row['site_name'], row['ip']) for row in records if row['attention']})
    result['has_alert'] = result['attention'] > 0
    return result

