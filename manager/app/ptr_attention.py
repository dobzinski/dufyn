"""Dashboard PTR alerts use the Controller's canonical inventory and rules."""
import json
from urllib.request import urlopen

from fastapi import HTTPException
from app.controller_tls import request_tls_options


def group_ptr_alerts(rows):
    grouped = {}
    for row in rows:
        key = (row["site_name"], row["ip"])
        reasons = row.get("attention_reasons") or [
            label for flag, label in ((row.get("undiscovered"), "Undiscovered"),
                                     (row.get("asset_status") == "Inactive", "Inactive"),
                                     (row.get("comparison") == "Mismatch", "Mismatch")) if flag]
        if key not in grouped:
            grouped[key] = dict(row, attention_reasons=list(reasons))
            continue
        item = grouped[key]
        item["attention_reasons"] = sorted(set(item["attention_reasons"]) | set(reasons))
        for field in ("ptr_names", "expected_names"):
            item[field] = sorted(set(item.get(field, [])) | set(row.get(field, [])))
        if row.get("comparison") == "Mismatch":
            item["comparison"] = "Mismatch"
        if (row.get("checked_at") or "") > (item.get("checked_at") or ""):
            item["checked_at"] = row["checked_at"]
    return list(grouped.values())


def load_ptr_attention(url, ca_file=None):
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(status_code=503, detail="Controller is not configured")
    alerts = []
    offset = 0
    try:
        tls_options = request_tls_options(url, ca_file)
        while True:
            endpoint = f"{url.rstrip('/')}/v1/public/dns/ptr?limit=1000&offset={offset}"
            with urlopen(endpoint, timeout=10, **tls_options) as response:
                rows = json.load(response)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Invalid PTR response")
            alerts.extend(row for row in rows
                          if row.get("attention") or row.get("undiscovered")
                          or row.get("asset_status") == "Inactive" or row.get("comparison") == "Mismatch")
            if len(rows) < 1000:
                return group_ptr_alerts(alerts)
            offset += len(rows)
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Unable to load PTR alerts") from None
