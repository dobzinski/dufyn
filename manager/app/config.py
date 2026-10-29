from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from app.controller_tls import validate_ca_file


TLS_VERSION_DEFAULTS = {
    "SSLv2": "critical", "SSLv3": "critical", "TLSv1": "critical",
    "TLSv1.1": "critical", "TLSv1.2": "warning", "TLSv1.3": "valid",
}
TLS_STATUSES = {"critical", "warning", "valid", "unknown"}


TIMEZONE_DEFAULTS = [
    {"value": "UTC", "label": "UTC"},
    {"value": "America/Sao_Paulo", "label": "Sao_Paulo"},
    {"value": "America/New_York", "label": "America/New_York"},
    {"value": "Europe/London", "label": "Europe/London"},
    {"value": "Asia/Tokyo", "label": "Asia/Tokyo"},
]


CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config():
    with CONFIG_FILE.open(encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a mapping")

    access = config.setdefault("access", {})
    if not isinstance(access, dict):
        raise ValueError("access must contain a mapping")
    if not isinstance(access.setdefault("login", False), bool):
        raise ValueError("access.login must be true or false")
    timeout = access.setdefault("session", 3600)
    if type(timeout) is not int or not 1 <= timeout <= 604800:
        raise ValueError("access.session must be an integer between 1 and 604800 seconds")

    password = config.setdefault("password", {})
    if not isinstance(password, dict) or not isinstance(password.setdefault("manager", False), bool):
        raise ValueError("password.manager must be true or false")

    time = config.setdefault("time", {})
    if not isinstance(time, dict):
        raise ValueError("time must contain a mapping")
    zones = time.setdefault("timezones", [item.copy() for item in TIMEZONE_DEFAULTS])
    if not isinstance(zones, list) or not 1 <= len(zones) <= 100:
        raise ValueError("time.timezones must contain between 1 and 100 entries")
    seen = set()
    for zone in zones:
        if (not isinstance(zone, dict) or set(zone) != {"value", "label"}
                or any(not isinstance(zone[key], str) or not zone[key].strip() or len(zone[key]) > 100 for key in ("value", "label"))):
            raise ValueError("Each time.timezones entry must contain a nonempty value and label (up to 100 characters)")
        if zone["value"] in seen:
            raise ValueError("time.timezones values must be unique")
        try:
            ZoneInfo(zone["value"])
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("time.timezones values must be available IANA time zone identifiers") from None
        seen.add(zone["value"])

    controller = config.setdefault("controller", {})
    if not isinstance(controller, dict):
        raise ValueError("controller must contain a mapping")
    validate_ca_file(controller)

    logo = config.get("logo", {})
    if not isinstance(logo, dict):
        raise ValueError("logo must contain a mapping")
    if not isinstance(logo.get("animate", True), bool):
        raise ValueError("logo.animate must be true or false")

    tls = config.setdefault("tls", {})
    if not isinstance(tls, dict):
        raise ValueError("tls must contain a mapping")
    expiration = tls.setdefault("expiration", {})
    if not isinstance(expiration, dict):
        raise ValueError("tls.expiration must contain a mapping")
    warning_days = expiration.setdefault("warning_days", 90)
    critical_days = expiration.setdefault("critical_days", 30)
    if (type(warning_days) is not int or type(critical_days) is not int
            or not 0 <= critical_days <= warning_days):
        raise ValueError("tls.expiration must satisfy 0 <= critical_days <= warning_days using integers")
    versions = tls.setdefault("versions", TLS_VERSION_DEFAULTS.copy())
    if not isinstance(versions, dict) or any(not isinstance(version, str) or not isinstance(status, str) or status not in TLS_STATUSES for version, status in versions.items()):
        raise ValueError("tls.versions must map version names to critical, warning, valid or unknown")
    tls.setdefault("default", "unknown")
    if not isinstance(tls["default"], str) or tls["default"] not in TLS_STATUSES:
        raise ValueError("tls.default must be critical, warning, valid or unknown")

    newers = config.setdefault("newers", {})
    if not isinstance(newers, dict):
        raise ValueError("newers must contain a mapping")
    days = newers.setdefault("days", 0)
    if type(days) is not int or not 0 <= days <= 36500:
        raise ValueError("newers.days must be an integer between 0 and 36500")

    signing = config.setdefault("self-signed", {})
    if not isinstance(signing, dict):
        raise ValueError("self-signed must contain a mapping")
    signing.setdefault("key_algorithm", "RSA")
    signing.setdefault("rsa_key_size", 2048)
    signing.setdefault("signature_hash", "SHA256")
    if signing["key_algorithm"] != "RSA":
        raise ValueError("self-signed.key_algorithm must be RSA")
    if type(signing["rsa_key_size"]) is not int or signing["rsa_key_size"] not in (2048, 3072, 4096):
        raise ValueError("self-signed.rsa_key_size must be 2048, 3072 or 4096")
    if signing["signature_hash"] not in ("SHA256", "SHA384", "SHA512"):
        raise ValueError("self-signed.signature_hash must be SHA256, SHA384 or SHA512")

    return config


def load_version():
    path = CONFIG_FILE.parent / "version.yaml"
    with path.open(encoding="utf-8") as file:
        versions = yaml.safe_load(file)
    if not isinstance(versions, dict) or not isinstance(versions.get("version"), str) or not versions["version"].strip():
        raise ValueError("version.yaml must contain a nonempty version string")
    return versions["version"]
