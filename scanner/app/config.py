import math
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit
import yaml

from app.controller_tls import validate_ca_file
from app.dns_sync import configure_dns
from app.scan_selection import normalize_scans

BASE = Path(__file__).resolve().parent.parent
DEFAULT_QUIC_PORTS = (443, 8080, 8443)
INTERNAL_DEFAULTS = {
    "execution": {
        "interval_seconds": 3600,
        "heartbeat_interval_seconds": 20,
        "request_timeout_seconds": 10,
        "result_attempts": 5,
        "retry_interval_seconds": 5,
    },
    "discovery": {"ping_timeout_seconds": 1, "parallel_networks": 4},
}


def load_config(*, scans_override=None, mode_override=None):
    with Path(os.getenv("SCANNER_CONFIG", str(BASE / "config.yaml"))).open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("Scanner config must be a mapping")
    for section in ("controller", "execution", "discovery"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Missing {section} mapping")
    # Accept legacy execution polling configuration without retaining two intervals.
    execution = config["execution"]
    if "poll_interval_seconds" in execution:
        legacy_interval = execution.pop("poll_interval_seconds")
        execution.setdefault("interval_seconds", legacy_interval)
    for section, defaults in INTERNAL_DEFAULTS.items():
        for field, value in defaults.items():
            config[section].setdefault(field, value)
    recheck = config["discovery"].setdefault("recheck", {})
    if not isinstance(recheck, dict):
        raise ValueError("discovery.recheck must be a mapping")
    recheck.setdefault("enabled", True)
    recheck.setdefault("timeout_seconds", 1)
    if not isinstance(recheck["enabled"], bool):
        raise ValueError("discovery.recheck.enabled must be true or false")
    timeout = recheck["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("discovery.recheck.timeout_seconds must be positive and finite")
    legacy_ptr = config["discovery"].pop("ptr", None)
    ptr = config.setdefault("ptr", legacy_ptr if legacy_ptr is not None else {})
    if not isinstance(ptr, dict):
        raise ValueError("ptr must be a mapping")
    ptr.setdefault("enabled", False)
    if not isinstance(ptr["enabled"], bool):
        raise ValueError("ptr.enabled must be true or false")
    targets = config.setdefault("targets", {})
    if not isinstance(targets, dict):
        raise ValueError("targets must be a mapping")
    targets.setdefault("enabled", True)
    if not isinstance(targets["enabled"], bool):
        raise ValueError("targets.enabled must be true or false")
    udp=config.setdefault("udp",{})
    if not isinstance(udp,dict):raise ValueError("udp must be a mapping")
    udp.setdefault("version_detection",False)
    udp.setdefault("host_timeout_seconds",120)
    if not isinstance(udp["version_detection"],bool):raise ValueError("udp.version_detection must be boolean")
    timeout=udp["host_timeout_seconds"]
    if isinstance(timeout,bool) or not isinstance(timeout,int) or timeout<=0:raise ValueError("udp.host_timeout_seconds must be a positive integer")
    quic=udp.setdefault("quic",{})
    if not isinstance(quic,dict):raise ValueError("udp.quic must be a mapping")
    quic.setdefault("enabled",True)
    quic.setdefault("timeout_seconds",5)
    quic.setdefault("ports", list(DEFAULT_QUIC_PORTS))
    ports = quic["ports"]
    if (not isinstance(ports, list) or any(type(port) is not int or not 1 <= port <= 65535 for port in ports)
            or len(ports) != len(set(ports))):
        raise ValueError("udp.quic.ports must be a list of unique integer ports between 1 and 65535")
    if not isinstance(quic["enabled"],bool):raise ValueError("udp.quic.enabled must be boolean")
    timeout=quic["timeout_seconds"]
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise ValueError("udp.quic.timeout_seconds must be positive and finite")
    # Selection is per invocation, never persisted in YAML or environment.
    selection = scans_override if scans_override is not None else ["all"]
    execution["scans"], execution["_scans_explicit"] = normalize_scans(selection)
    if execution["_scans_explicit"] and "dns" in execution["scans"]:
        settings = config.setdefault("dns", {})
        if not isinstance(settings, dict):
            raise ValueError("dns must be a mapping")
        if not settings.get("server") or not settings.get("zones"):
            raise ValueError("--scans dns requires dns.server and at least one dns.zones entry")
        settings["enabled"] = True
    configure_dns(config)
    controller = config["controller"]
    validate_ca_file(controller)
    controller["token"] = os.getenv("SCANNER_TOKEN") or controller.get("token")
    controller["url"] = os.getenv("SCANNER_CONTROLLER_URL") or controller.get("url")
    if not isinstance(controller["token"], str) or not controller["token"].strip():
        raise ValueError("Set controller.token or SCANNER_TOKEN to an issued scanner token")
    if not isinstance(controller["url"], str):
        raise ValueError("Set controller.url")
    url = urlsplit(controller["url"])
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("controller.url must be an HTTP(S) URL without credentials, query or fragment")
    config["execution"]["mode"] = mode_override or os.getenv("SCANNER_MODE") or config["execution"].get("mode")
    if config["execution"]["mode"] not in ("once", "continuous"):
        raise ValueError("execution.mode must be once or continuous")
    for section, fields in {
        "execution": ("interval_seconds", "heartbeat_interval_seconds", "request_timeout_seconds", "result_attempts", "retry_interval_seconds"),
        "discovery": ("concurrency", "parallel_networks", "packets_per_second", "ping_timeout_seconds"),
    }.items():
        for field in fields:
            value = config[section].get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{section}.{field} must be positive and finite")
    for section, field in (("execution", "result_attempts"), ("discovery", "concurrency"), ("discovery", "parallel_networks")):
        if not isinstance(config[section][field], int):
            raise ValueError(f"{section}.{field} must be an integer")
    if config["execution"]["heartbeat_interval_seconds"] >= 30:
        raise ValueError("Heartbeat interval must be below 30 seconds (API lease minimum is 60)")
    config["worker_id"] = os.getenv("SCANNER_WORKER_ID") or socket.gethostname()
    if not 1 <= len(config["worker_id"].strip()) <= 100:
        raise ValueError("Worker ID must be 1..100 characters")
    return config


def configuration_snapshot(config):
    """Export effective settings without authentication tokens or runtime objects."""
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if not key.startswith("_")}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    result = clean(config)
    result.get("controller", {}).pop("token", None)
    # Resolve TSIG environment credentials into a restorable configuration.
    tsig = config.get("dns", {}).get("tsig")
    if tsig and tsig.get("_secret"):
        result["dns"]["tsig"].pop("secret_env", None)
        result["dns"]["tsig"]["secret"] = tsig["_secret"]
    return result


def quic_port_enabled(config, port):
    """The allowlist narrows assigned task ports; it never adds probe targets."""
    options = config.get("udp", {}).get("quic", {})
    return options.get("enabled", True) and port in options.get("ports", DEFAULT_QUIC_PORTS)


def load_version():
    with (BASE / "version.yaml").open(encoding="utf-8") as file:
        return yaml.safe_load(file)["version"]
