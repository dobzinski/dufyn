"""Read-only view of successfully imported DNS zone records."""
from fastapi import APIRouter

from app.database import get_connection

router = APIRouter(prefix="/api/dns", tags=["DNS inventory"])


@router.get("/records")
def records():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT r.id, z.site_id, s.name AS site_name, r.zone_id,
                z.name AS zone_name, host(z.dns_server) AS dns_server, z.dns_port,
                z.serial, z.last_synced_at, z.last_scanner_id,
                sc.name AS scanner_name, r.name, r.record_type,
                host(r.address) AS address, r.target_name, r.ttl,
                r.first_seen, r.last_seen,
                EXISTS (SELECT 1 FROM target t WHERE t.site_id=z.site_id
                    AND t.fqdn=lower(rtrim(r.name, '.'))) AS is_target,
                reverse_check.result AS ptr_result, reverse_check.checked_at AS ptr_checked_at,
                ARRAY(SELECT p.hostname FROM dns_ptr_record p
                      WHERE p.reverse_check_id=reverse_check.id ORDER BY p.hostname) AS ptr_names
            FROM dns_record r
            JOIN dns_zone z ON z.id=r.zone_id
            JOIN site s ON s.id=z.site_id
            LEFT JOIN scanner sc ON sc.id=z.last_scanner_id AND sc.site_id=z.site_id
            LEFT JOIN LATERAL (
                SELECT c.id, c.result, c.checked_at
                FROM dns_reverse_check c
                WHERE c.site_id=z.site_id AND c.ip=r.address
                    AND EXISTS (SELECT 1 FROM dns_ptr_record answer WHERE answer.reverse_check_id=c.id)
                ORDER BY c.checked_at DESC, c.id DESC
                LIMIT 1
            ) reverse_check ON r.record_type IN ('A', 'AAAA')
            ORDER BY r.name, s.name, z.name, r.record_type, r.address, r.target_name, r.id
        """).fetchall()
    return [dict(row) for row in rows]
