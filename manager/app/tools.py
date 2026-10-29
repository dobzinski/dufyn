"""Stateless sysadmin tools; bounded inputs and no network access or persistence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/tools", tags=["tools"])
MAX_BODY_BYTES = 2 * 1024 * 1024
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubnetInput(ToolInput):
    network: str = Field(min_length=1, max_length=100)


class TimeInput(ToolInput):
    conversion: Literal["to_date", "to_timestamp", "between_zones"]
    unit: Literal["seconds", "milliseconds"] | None = None
    time_zone: str = Field(min_length=1, max_length=100)
    source_time_zone: str | None = Field(default=None, min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_options(self):
        from app.main import config
        allowed = {zone["value"] for zone in config["time"]["timezones"]}
        if self.time_zone not in allowed:
            raise ValueError("Choose a configured time zone")
        if self.conversion == "between_zones":
            if self.source_time_zone not in allowed:
                raise ValueError("Choose a configured source time zone")
        elif self.unit is None:
            raise ValueError("Choose the timestamp unit")
        return self


class CertificateInput(ToolInput):
    pem: str = Field(min_length=1, max_length=262144)


def display(result):
    def value(item):
        if isinstance(item, list):
            return ", ".join(map(str, item)) or "None"
        return str(item)
    return {"text": "\n".join(f"{key}: {value(item)}" for key, item in result.items()), "result": result}


def calculate_subnet(data):
    source = data.network.strip()
    if "/" not in source or "%" in source:
        raise ValueError("Enter an IPv4 or IPv6 address with its CIDR prefix, for example 192.168.1.10/24.")
    try:
        interface = ipaddress.ip_interface(source)
    except ValueError:
        raise ValueError("Enter a valid IPv4 or IPv6 address and CIDR prefix.") from None
    network = interface.network
    total = network.num_addresses
    if network.version == 4:
        usable = total if network.prefixlen >= 31 else total - 2
        first = network.network_address if network.prefixlen >= 31 else network.network_address + 1
        last = network.broadcast_address if network.prefixlen >= 31 else network.broadcast_address - 1
    else:
        # Match IPv6 hosts(): exclude the subnet-router anycast address, except /127 and /128.
        usable = total if network.prefixlen >= 127 else total - 1
        first = network.network_address if network.prefixlen >= 127 else network.network_address + 1
        last = network.broadcast_address
    result = {"IP version": f"IPv{network.version}", "Input address": str(interface.ip),
              "Network": str(network), "Prefix length": network.prefixlen,
              "Netmask": str(network.netmask), "Total addresses": str(total),
              "Usable host addresses": str(usable), "First host": str(first), "Last host": str(last)}
    if network.version == 4:
        result["Wildcard mask"] = str(network.hostmask)
        result["Broadcast"] = str(network.broadcast_address)
        if network.prefixlen == 31:
            result["Host convention"] = "/31 point-to-point: both addresses are usable."
    else:
        result["Broadcast"] = "Not applicable to IPv6"
        result["Host convention"] = "Subnet-router anycast excluded, except /127 and /128."
    return display(result)


def local_instant(value, zone):
    if value.tzinfo is not None and value.utcoffset() is not None:
        actual = value.astimezone(zone)
        if actual.replace(tzinfo=None) != value.replace(tzinfo=None) or actual.utcoffset() != value.utcoffset():
            raise ValueError("The explicit date offset does not match the selected source time zone.")
        return value.astimezone(timezone.utc)
    candidates = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(candidate)
    if not candidates:
        raise ValueError("This local date does not exist in the source time zone because of a clock change.")
    if len(candidates) > 1:
        raise ValueError("This local date is ambiguous in the source time zone. Include its UTC offset to select the intended occurrence.")
    return candidates.pop()


def convert_time(data):
    text = data.text.strip()
    if not text:
        raise ValueError("From is required.")
    try:
        zone = ZoneInfo(data.time_zone)
    except ZoneInfoNotFoundError:
        raise ValueError("Time zone data is unavailable. Install the Manager requirements.") from None
    try:
        if data.conversion == "to_date":
            if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", text):
                raise ValueError()
            if "." in text and len(text.split(".", 1)[1]) > (6 if data.unit == "seconds" else 3):
                raise ValueError()
            numeric = Decimal(text)
            if not numeric.is_finite():
                raise ValueError()
            # Bound before multiplication/int conversion, including enormous exponents.
            if abs(numeric) > Decimal("300000000000000"):
                raise ValueError()
            microseconds = numeric * (1000000 if data.unit == "seconds" else 1000)
            if microseconds != microseconds.to_integral_value():
                raise ValueError()
            instant = EPOCH + timedelta(microseconds=int(microseconds))
        else:
            if re.search(r"[.,][0-9]{7}", text):
                raise ValueError()
            instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if data.conversion == "between_zones":
                instant = local_instant(instant, ZoneInfo(data.source_time_zone))
            else:
                if instant.tzinfo is None or instant.utcoffset() is None:
                    raise ValueError("Use an ISO 8601 date with an explicit UTC offset, for example 2026-10-26T10:30:00-03:00.")
                instant = instant.astimezone(timezone.utc)
    except (InvalidOperation, OverflowError):
        raise ValueError("Timestamp is outside the supported date range (years 1-9999) or has invalid precision.") from None
    except ValueError as error:
        if str(error).startswith(("Use an ISO", "This local date", "The explicit date")):
            raise
        raise ValueError("Enter a valid Unix timestamp or ISO 8601 date (years 1-9999, up to microsecond precision).") from None
    delta = instant - EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    def number(divisor):
        return format(Decimal(micros) / divisor, "f")
    try:
        local = instant.astimezone(zone)
    except (ValueError, OverflowError):
        raise ValueError("Date cannot be represented in the selected time zone.") from None
    from app.main import config
    labels = {item["value"]: item["label"] for item in config["time"]["timezones"]}
    result = {"UTC": instant.isoformat(), "Time zone": data.time_zone,
              "Time zone label": labels[data.time_zone], "Local date": local.isoformat(),
              "Unix seconds": number(1000000), "Unix milliseconds": number(1000)}
    if data.conversion == "between_zones":
        result["Source time zone"] = data.source_time_zone
        result["Source time zone label"] = labels[data.source_time_zone]
        result["Source local date"] = instant.astimezone(ZoneInfo(data.source_time_zone)).isoformat()
    else:
        result = {"Unix timestamp": number(1000000 if data.unit == "seconds" else 1000), "Unit": data.unit, **result}
    return display(result)


def inspect_certificate(data):
    pem = data.pem.strip()
    if not pem or len(pem.encode("utf-8")) > 262144:
        raise ValueError("Paste PEM certificates, up to 256 KiB.")
    if "PRIVATE KEY-----" in pem or pem.count("-----BEGIN CERTIFICATE-----") > 16:
        raise ValueError("Paste only public PEM certificates, at most 16 certificates.")
    try:
        certificates = x509.load_pem_x509_certificates(pem.encode("ascii"))
    except (ValueError, UnicodeError, UnsupportedAlgorithm):
        raise ValueError("Enter valid PEM certificate content, including BEGIN and END CERTIFICATE lines.") from None
    if not certificates:
        raise ValueError("No PEM certificates found.")
    now = datetime.now(timezone.utc)
    results = []
    for certificate in certificates:
        try:
            key = certificate.public_key()
            algorithm = getattr(certificate.signature_hash_algorithm, "name", "None (intrinsic)")
            public_key = type(key).__name__.lstrip("_")
            if hasattr(key, "key_size"):
                public_key += f" ({key.key_size} bits)"
            if hasattr(key, "curve"):
                public_key += f" / {key.curve.name}"
            try:
                sans = [f"{type(name).__name__}: {name.value.hex() if isinstance(name.value, bytes) else name.value}" for name in certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value]
            except x509.ExtensionNotFound:
                sans = []
            try:
                ca = "Yes" if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca else "No"
            except x509.ExtensionNotFound:
                ca = "Not specified"
            start, end = certificate.not_valid_before_utc, certificate.not_valid_after_utc
            validity = "Not yet valid" if now < start else "Expired" if now > end else "Current"
            fingerprint = certificate.fingerprint(hashes.SHA256()).hex().upper()
            results.append({"Subject": certificate.subject.rfc4514_string(), "Issuer": certificate.issuer.rfc4514_string(),
                            "Serial number (hex)": format(certificate.serial_number, "X"),
                            "Not before (UTC)": start.isoformat(), "Not after (UTC)": end.isoformat(),
                            "Date status": validity, "Remaining whole days": (end - now).days,
                            "Public key": public_key, "Signature algorithm": certificate.signature_algorithm_oid.dotted_string,
                            "Signature hash": algorithm, "Certificate authority": ca, "Subject alternative names": sans,
                            "SHA-256 fingerprint": ":".join(fingerprint[i:i+2] for i in range(0, len(fingerprint), 2)),
                            "Verification": "Dates and metadata only; signature, trust chain and revocation are not verified."})
        except (ValueError, UnsupportedAlgorithm, x509.DuplicateExtension, x509.UnsupportedGeneralNameType):
            raise ValueError("Certificate contains unsupported or malformed key, signature or extension data.") from None
    return {"text": "\n\n".join(f"Certificate {index}\n{display(item)['text']}" for index, item in enumerate(results, 1)),
            "result": results}


async def execute(request, model, operation):
    def respond(data, status=200):
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return respond({"detail": "Send the tool fields as JSON."}, 415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            return respond({"detail": "Tool request is too large."}, 413)
        body.extend(chunk)
    try:
        data = model.model_validate(json.loads(body))
        for value in data.model_dump().values():
            if isinstance(value, str):
                value.encode("utf-8")
    except (ValidationError, ValueError, UnicodeError):
        return respond({"detail": "Complete the required fields using valid values within the indicated limits."}, 422)
    try:
        return respond(await run_in_threadpool(operation, data))
    except ValueError as error:
        return respond({"detail": str(error)}, 422)


@router.post("/subnet")
async def subnet(request: Request):
    return await execute(request, SubnetInput, calculate_subnet)


@router.post("/time")
@router.post("/timestamp", include_in_schema=False)
async def time_converter(request: Request):
    return await execute(request, TimeInput, convert_time)


@router.post("/certificate")
async def certificate(request: Request):
    return await execute(request, CertificateInput, inspect_certificate)
