"""Recent-inventory totals and browser-local daily counts over one rolling window."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import get_connection

router = APIRouter(prefix="/api/newers", tags=["dashboard"])
RESOURCES = ("assets", "targets", "ports", "certificates", "dns", "ptr")
# Keep the original joins and timestamp filters for each category. Aggregate in
# PostgreSQL before returning rows, so totals and daily series share one snapshot.
NEWERS_DAILY_QUERY = """WITH recent AS (
    SELECT 'assets' AS category, (a.first_seen AT TIME ZONE %(timezone)s)::date AS day, count(*) AS total
    FROM asset a JOIN site s ON s.id=a.site_id
    WHERE a.first_seen >= %(since)s AND a.first_seen <= %(now)s GROUP BY 2
    UNION ALL
    SELECT 'targets', (t.created_at AT TIME ZONE %(timezone)s)::date, count(*)
    FROM target t JOIN site s ON s.id=t.site_id
    WHERE t.created_at >= %(since)s AND t.created_at <= %(now)s GROUP BY 2
    UNION ALL
    SELECT 'ports', (p.first_seen AT TIME ZONE %(timezone)s)::date, count(*)
    FROM port p LEFT JOIN asset a ON a.id=p.asset_id
    LEFT JOIN target t ON t.id=p.target_id
    JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)
    WHERE p.first_seen >= %(since)s AND p.first_seen <= %(now)s GROUP BY 2
    UNION ALL
    SELECT 'certificates', (c.first_seen AT TIME ZONE %(timezone)s)::date, count(*)
    FROM certificate c LEFT JOIN port p ON p.id=c.port_id
    LEFT JOIN asset a ON a.id=COALESCE(c.asset_id,p.asset_id)
    LEFT JOIN target t ON t.id=p.target_id
    JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)
    WHERE c.first_seen >= %(since)s AND c.first_seen <= %(now)s GROUP BY 2
    UNION ALL
    SELECT 'dns', (d.first_seen AT TIME ZONE %(timezone)s)::date, count(*)
    FROM dns_record d JOIN dns_zone z ON z.id=d.zone_id
    JOIN site s ON s.id=z.site_id
    WHERE d.first_seen >= %(since)s AND d.first_seen <= %(now)s GROUP BY 2
    UNION ALL
    SELECT 'ptr', (p.first_seen AT TIME ZONE %(timezone)s)::date, count(*)
    FROM dns_ptr_record p JOIN dns_reverse_check r ON r.id=p.reverse_check_id
    JOIN site s ON s.id=r.site_id
    WHERE p.first_seen >= %(since)s AND p.first_seen <= %(now)s AND r.result='answered'
    GROUP BY 2
)
SELECT category, day, total FROM recent ORDER BY day, category
"""


def daily_inventory(rows, since, now, zone=timezone.utc):
    """Include empty calendar days without extending the rolling query window."""
    totals = dict.fromkeys(RESOURCES, 0)
    daily = {}
    day = since.astimezone(zone).date()
    while day <= now.astimezone(zone).date():
        daily[day.isoformat()] = dict.fromkeys(RESOURCES, 0)
        day += timedelta(days=1)
    for row in rows:
        category, total = row["category"], int(row["total"])
        day = row["day"].isoformat()
        daily[day][category] += total
        totals[category] += total
    return totals, [{"date": day, "totals": counts} for day, counts in daily.items()]


def inventory_timezone(name):
    """Use UTC when the client cannot supply a supported IANA timezone."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.utc


@router.get("/totals")
def newers_totals(tz: str = "UTC"):
    from app.main import config
    days = config.get("newers", {}).get("days", 0)
    zone = inventory_timezone(tz)
    zone_name = zone.key if isinstance(zone, ZoneInfo) else "UTC"
    now = datetime.now(timezone.utc)
    enabled = days > 0
    since = now - timedelta(days=days) if enabled else None
    totals = dict.fromkeys(RESOURCES, 0)
    daily = []
    if enabled:
        # One statement gives every category the same PostgreSQL snapshot.
        with get_connection() as connection:
            rows = connection.execute(NEWERS_DAILY_QUERY,
                                      {"since": since, "now": now, "timezone": zone_name}).fetchall()
        totals, daily = daily_inventory(rows, since, now, zone)
    return JSONResponse({"enabled": enabled, "days": days, "generated_at": now.isoformat(),
                         "since": since.isoformat() if since else None, "totals": totals,
                         "timezone": zone_name, "daily": daily,
                         "chart": config.get("newers", {}).get("chart", "bars")},
                        headers={"Cache-Control": "no-store"})
