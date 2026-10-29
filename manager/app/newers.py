"""Recent-inventory counts from PostgreSQL, matching Controller /v1/public/newers."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import get_connection

router = APIRouter(prefix="/api/newers", tags=["dashboard"])
RESOURCES = ("assets", "targets", "ports", "certificates", "dns", "ptr")
NEWERS_TOTALS_QUERY = """SELECT
    (SELECT count(*) FROM asset a JOIN site s ON s.id=a.site_id
     WHERE a.first_seen >= %(since)s AND a.first_seen <= %(now)s) AS assets,
    (SELECT count(*) FROM target t JOIN site s ON s.id=t.site_id
     WHERE t.created_at >= %(since)s AND t.created_at <= %(now)s) AS targets,
    (SELECT count(*) FROM port p LEFT JOIN asset a ON a.id=p.asset_id
     LEFT JOIN target t ON t.id=p.target_id
     JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)
     WHERE p.first_seen >= %(since)s AND p.first_seen <= %(now)s) AS ports,
    (SELECT count(*) FROM certificate c
     LEFT JOIN port p ON p.id=c.port_id
     LEFT JOIN asset a ON a.id=COALESCE(c.asset_id,p.asset_id)
     LEFT JOIN target t ON t.id=p.target_id
     JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)
     WHERE c.first_seen >= %(since)s AND c.first_seen <= %(now)s) AS certificates,
    (SELECT count(*) FROM dns_record d JOIN dns_zone z ON z.id=d.zone_id
     JOIN site s ON s.id=z.site_id
     WHERE d.first_seen >= %(since)s AND d.first_seen <= %(now)s) AS dns,
    (SELECT count(*) FROM (
        SELECT s.id,r.ip,r.dns_server,r.dns_port,p.hostname
        FROM dns_ptr_record p JOIN dns_reverse_check r ON r.id=p.reverse_check_id
        JOIN site s ON s.id=r.site_id
        WHERE p.first_seen <= %(now)s AND p.first_seen >= %(since)s AND r.result='answered'
    ) recent_ptr) AS ptr
"""


@router.get("/totals")
def newers_totals():
    from app.main import config
    days = config.get("newers", {}).get("days", 0)
    now = datetime.now(timezone.utc)
    enabled = days > 0
    since = now - timedelta(days=days) if enabled else None
    totals = dict.fromkeys(RESOURCES, 0)
    if enabled:
        # One statement gives every category the same PostgreSQL snapshot.
        with get_connection() as connection:
            totals = dict(connection.execute(NEWERS_TOTALS_QUERY, {"since": since, "now": now}).fetchone())
    return JSONResponse({"enabled": enabled, "days": days, "generated_at": now.isoformat(),
                         "since": since.isoformat() if since else None, "totals": totals},
                        headers={"Cache-Control": "no-store"})
