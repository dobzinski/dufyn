"""Read-only PTR inventory; repeated observations are not repeated table rows."""
from fastapi import APIRouter
from app.database import get_connection

router = APIRouter(prefix="/api/ptr", tags=["PTR inventory"])

PTR_RECORDS_QUERY = """
    SELECT p.id,c.site_id,s.name AS site_name,host(c.ip) AS ip,p.hostname,p.ttl,
        host(c.dns_server) AS dns_server,c.dns_port,c.scanner_id,sc.name AS scanner_name,
        a.id AS asset_id,COALESCE(a.display_name,a.discovered_hostname) AS asset_name,
        CASE a.status WHEN 'A' THEN 'Active' WHEN 'I' THEN 'Inactive' WHEN 'U' THEN 'Unknown' END AS asset_status,
        p.first_seen,p.last_seen
    FROM dns_ptr_record p JOIN dns_reverse_check c ON c.id=p.reverse_check_id
    JOIN site s ON s.id=c.site_id
    LEFT JOIN asset a ON a.site_id=c.site_id AND a.ip=c.ip
    LEFT JOIN scanner sc ON sc.id=c.scanner_id AND sc.site_id=c.site_id
    WHERE c.result='answered'
    ORDER BY s.name,c.ip,p.hostname,c.dns_server,c.dns_port,p.id
"""


@router.get("/records")
def records():
    with get_connection() as connection:
        rows = connection.execute(PTR_RECORDS_QUERY).fetchall()
    return [dict(row) for row in rows]
