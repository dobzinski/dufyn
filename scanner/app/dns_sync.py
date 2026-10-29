"""AXFR stage of the scanner cycle; publication requires a complete transfer."""
import asyncio
import base64
from ipaddress import ip_address
import logging
import math
import os

import dns.asyncquery
import dns.exception
import dns.message
import dns.name
import dns.rdatatype
import dns.rcode
import dns.xfr
import dns.tsigkeyring
import dns.zone

log = logging.getLogger("scanner.dns")


def normalize_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("DNS names must be nonempty strings")
    name = dns.name.from_text(value.strip()).canonicalize()
    result = name.to_text(omit_final_dot=True)
    if name == dns.name.root or len(result) > 253:
        raise ValueError("Invalid DNS name")
    return result


def configure_dns(config):
    settings = config.setdefault("dns", {})
    if not isinstance(settings, dict):
        raise ValueError("dns must be a mapping")
    settings.setdefault("enabled", False)
    settings.setdefault("server", None)
    settings.setdefault("port", 53)
    settings.setdefault("zones", [])
    if "interval_seconds" in settings:
        settings.pop("interval_seconds")
        log.warning("dns.interval_seconds is obsolete; use execution.interval_seconds for the whole cycle")
    settings.setdefault("timeout_seconds", 5)
    settings.setdefault("transfer_lifetime_seconds", 60)
    settings.setdefault("api_timeout_seconds", 60)
    settings.setdefault("max_records", 100000)
    if not isinstance(settings["enabled"], bool):
        raise ValueError("dns.enabled must be boolean")
    if not isinstance(settings["zones"], list) or len(settings["zones"]) > 100:
        raise ValueError("dns.zones must be a list with at most 100 zone names")
    try:
        settings["zones"] = [normalize_name(name) for name in settings["zones"]]
    except (dns.exception.DNSException, UnicodeError) as error:
        raise ValueError("Invalid DNS zone name") from error
    if len(set(settings["zones"])) != len(settings["zones"]):
        raise ValueError("dns.zones must not contain duplicates")
    for key in ("port", "max_records"):
        value = settings[key]
        maximum = 65535 if key == "port" else 100000
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError("Invalid dns." + key)
    for key in ("timeout_seconds", "transfer_lifetime_seconds", "api_timeout_seconds"):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("dns." + key + " must be positive and finite")
    if settings["transfer_lifetime_seconds"] > 300 or settings["api_timeout_seconds"] > 300:
        raise ValueError("DNS transfer/API timeouts must not exceed 300 seconds")
    if settings["server"] is not None:
        if not isinstance(settings["server"], str):
            raise ValueError("dns.server must be an IPv4 or IPv6 address")
        address = ip_address(settings["server"])
        if getattr(address, "scope_id", None):
            raise ValueError("dns.server must not contain an IPv6 scope identifier")
        settings["server"] = str(address)
    if settings["enabled"] and (not settings["server"] or not settings["zones"]):
        raise ValueError("Enabled DNS requires dns.server and at least one zone")
    tsig = settings.get("tsig")
    if tsig is not None:
        if not isinstance(tsig, dict):
            raise ValueError("dns.tsig must be a mapping")
        tsig["name"] = normalize_name(tsig.get("name"))
        tsig.setdefault("algorithm", "hmac-sha256")
        if tsig["algorithm"] not in ("hmac-sha256", "hmac-sha384", "hmac-sha512"):
            raise ValueError("Unsupported dns.tsig.algorithm")
        if tsig.get("secret_env") and tsig.get("secret"):
            raise ValueError("Use either dns.tsig.secret_env or secret")
        if tsig.get("secret_env"):
            if not isinstance(tsig["secret_env"], str):
                raise ValueError("dns.tsig.secret_env must name an environment variable")
            secret = os.getenv(tsig["secret_env"])
        else:
            secret = tsig.get("secret")
        try:
            if not isinstance(secret, str) or not base64.b64decode(secret, validate=True):
                raise ValueError()
        except (ValueError, TypeError) as error:
            raise ValueError("DNS TSIG requires a valid base64 secret") from error
        tsig["_secret"] = secret
    return settings


async def _receive_zone(settings, zone_name, stop):
    origin = dns.name.from_text(zone_name)
    zone = dns.zone.Zone(origin, relativize=False)
    query = dns.message.make_query(origin, dns.rdatatype.AXFR)
    tsig = settings.get("tsig")
    if tsig:
        query.use_tsig(
            dns.tsigkeyring.from_text({tsig["name"]: tsig["_secret"]}),
            keyname=tsig["name"], algorithm=tsig["algorithm"],
        )
    transfer = asyncio.create_task(dns.asyncquery.inbound_xfr(
        settings["server"], zone, query=query, port=settings["port"],
        timeout=settings["timeout_seconds"], lifetime=settings["transfer_lifetime_seconds"],
    ))
    try:
        while not transfer.done():
            if stop.is_set():
                raise RuntimeError("DNS transfer interrupted")
            await asyncio.wait({transfer}, timeout=0.1)
        await transfer
        if stop.is_set():
            raise RuntimeError("DNS transfer interrupted")
        zone.check_origin()
        return zone
    finally:
        if not transfer.done():
            transfer.cancel()
        await asyncio.gather(transfer, return_exceptions=True)


def collect_zone(settings, zone_name, stop):
    if stop.is_set():
        raise RuntimeError("DNS transfer interrupted")
    zone = asyncio.run(_receive_zone(settings, zone_name, stop))
    serial = zone.get_rdataset(zone.origin, dns.rdatatype.SOA)[0].serial
    records = []
    owner_types = {}
    for owner, node in zone.nodes.items():
        if stop.is_set():
            raise RuntimeError("DNS transfer interrupted")
        name = normalize_name(owner.to_text())
        if not owner.is_subdomain(zone.origin):
            raise ValueError("AXFR contains a record outside the requested zone")
        for rdataset in node.rdatasets:
            kind = dns.rdatatype.to_text(rdataset.rdtype)
            if kind not in ("A", "AAAA", "CNAME"):
                continue
            for rdata in rdataset:
                record = {"name": name, "record_type": kind, "ttl": rdataset.ttl,
                          "address": None, "target_name": None}
                if kind == "CNAME":
                    record["target_name"] = normalize_name(rdata.target.to_text())
                else:
                    record["address"] = str(ip_address(rdata.address))
                records.append(record)
                if len(records) > settings["max_records"]:
                    raise ValueError("DNS zone exceeds dns.max_records; snapshot not published")
                types = owner_types.setdefault(name, set())
                if (kind == "CNAME" and types) or "CNAME" in types:
                    raise ValueError("CNAME cannot coexist with other address records")
                types.add(kind)
    records.sort(key=lambda record: (record["name"], record["record_type"],
                                    record["address"] or record["target_name"]))
    return {"serial": serial, "complete": True, "records": records}


def run_dns_cycle(client, config, stop):
    settings = config["dns"]
    success = True
    for name in settings["zones"]:
        if stop.is_set():
            return False
        try:
            registered = client.post("dns/zones/register", {
                "name": name, "dns_server": settings["server"], "dns_port": settings["port"],
            })
            snapshot = collect_zone(settings, name, stop)
            if stop.is_set():
                return False
            snapshot["expected_last_synced_at"] = registered["last_synced_at"]
            result = client.post("dns/zones/" + str(registered["id"]) + "/records", snapshot)
            if result.get("state") != "completed":
                raise RuntimeError("API did not acknowledge DNS publication")
            log.info("DNS zone synced: zone=%s server=%s serial=%s records=%s",
                     name, settings["server"], snapshot["serial"], len(snapshot["records"]))
        except Exception as error:
            if stop.is_set():
                return False
            # Never log credentials, TSIG material or response payloads.
            if isinstance(error, dns.xfr.TransferError):
                reason = "DNS " + dns.rcode.to_text(error.rcode)
            else:
                reason = ("API HTTP " + str(error.status)) if hasattr(error, "status") else type(error).__name__
            log.error("DNS zone sync failed: zone=%s server=%s reason=%s; previous snapshot retained",
                      name, settings["server"], reason)
            success = False
    return success
