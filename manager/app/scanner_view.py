"""Allowlisted scanner settings for the read-only View dialog; never export raw JSON."""
import json
import math
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from app.passwords import cipher

# Unknown fields are excluded by default, including future credential settings.
FIELDS = [
    ("worker_id", "Worker", "text"),
    ("controller.url", "Controller URL", "url"),
    ("execution.mode", "Execution / Mode", "text"),
    ("execution.scans", "Execution / Scans", "list"),
    ("execution.interval_seconds", "Execution / Interval (seconds)", "number"),
    ("execution.heartbeat_interval_seconds", "Execution / Heartbeat (seconds)", "number"),
    ("execution.request_timeout_seconds", "Execution / Request timeout (seconds)", "number"),
    ("execution.result_attempts", "Execution / Result attempts", "number"),
    ("execution.retry_interval_seconds", "Execution / Retry interval (seconds)", "number"),
    ("discovery.concurrency", "Discovery / Concurrency", "number"),
    ("discovery.parallel_networks", "Discovery / Parallel networks", "number"),
    ("discovery.packets_per_second", "Discovery / Packets per second", "number"),
    ("discovery.ping_timeout_seconds", "Discovery / Ping timeout (seconds)", "number"),
    ("discovery.recheck.enabled", "Discovery / Recheck", "boolean"),
    ("discovery.recheck.timeout_seconds", "Discovery / Recheck timeout (seconds)", "number"),
    ("targets.enabled", "Targets / Enabled", "boolean"),
    ("ptr.enabled", "PTR / Enabled", "boolean"),
    ("dns.enabled", "DNS / Enabled", "boolean"),
    ("dns.server", "DNS / Server", "text"),
    ("dns.port", "DNS / Port", "number"),
    ("dns.zones", "DNS / Zones", "list"),
    ("dns.timeout_seconds", "DNS / Timeout (seconds)", "number"),
    ("dns.transfer_lifetime_seconds", "DNS / Transfer lifetime (seconds)", "number"),
    ("dns.api_timeout_seconds", "DNS / API timeout (seconds)", "number"),
    ("dns.max_records", "DNS / Maximum records", "number"),
    ("udp.version_detection", "UDP / Version detection", "boolean"),
    ("udp.host_timeout_seconds", "UDP / Host timeout (seconds)", "number"),
    ("udp.quic.enabled", "QUIC / Enabled", "boolean"),
    ("udp.quic.timeout_seconds", "QUIC / Timeout (seconds)", "number"),
    ("udp.quic.ports", "QUIC / Ports", "list"),
]


def configuration_rows(encrypted):
    if encrypted is None:
        return None
    try:
        config = json.loads(cipher().decrypt(bytes(encrypted)).decode("utf-8"))
        if not isinstance(config, dict):
            raise ValueError()
    except (HTTPException, InvalidToken, ValueError, UnicodeError, TypeError, RecursionError):
        raise HTTPException(503, "Scanner configuration cannot be opened with the configured master key") from None
    rows = []
    for path, label, kind in FIELDS:
        value = config
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if kind in ("text", "url"):
            valid = isinstance(value, str)
        elif kind == "boolean":
            valid = type(value) is bool
        elif kind == "number":
            valid = type(value) in (int, float) and math.isfinite(value)
        else:
            valid = isinstance(value, list) and all(type(item) in (str, int) for item in value)
        if not valid:
            continue
        if kind == "url":
            try:
                url = urlsplit(value)
                if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query or url.fragment:
                    continue
            except ValueError:
                continue
        rows.append({"label": label, "value": value})
    return rows
