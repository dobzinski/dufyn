"""Immutable private CA hierarchy and HTTPS certificate issuance."""
import base64
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
import math
import os
import re
from typing import Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from psycopg.errors import CheckViolation, ForeignKeyViolation, RestrictViolation, UniqueViolation

from app.config import load_config
from app.database import get_connection
from app.inventory import certificate_expiration

router = APIRouter(tags=["private PKI"])
SUBJECT_FIELDS = {
    "country": NameOID.COUNTRY_NAME, "state": NameOID.STATE_OR_PROVINCE_NAME,
    "locality": NameOID.LOCALITY_NAME, "organization": NameOID.ORGANIZATION_NAME,
    "organizational_unit": NameOID.ORGANIZATIONAL_UNIT_NAME, "common_name": NameOID.COMMON_NAME,
}
PUBLIC_COLUMNS = """c.id, c.parent_id, c.kind, c.name, c.source, c.subject,
    c.subject->>'common_name' AS common_name, c.alt_names, c.validity_days,
    c.not_before, c.not_after, c.key_algorithm, c.rsa_key_size,
    c.signature_hash, c.serial, c.fingerprint_sha256, c.created_at, p.name AS parent_name"""


def normalize_dns(value):
    value = value.strip().removesuffix(".").lower()
    wildcard = value.startswith("*.")
    tail = value[2:] if wildcard else value
    try:
        tail = tail.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Invalid DNS alternative name") from None
    labels = tail.split(".")
    if not tail or len(value) > 253 or len(tail) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    ):
        raise ValueError("Invalid DNS alternative name; wildcards must be the complete first label")
    result = ("*." if wildcard else "") + tail
    if len(result) > 253:
        raise ValueError("DNS alternative name is too long")
    return result


def parse_alt_names(value):
    result = []
    seen = set()
    for item in re.split(r"[,\r\n]+", value or ""):
        item = item.strip()
        if not item:
            continue
        prefix, separator, suffix = item.partition(":")
        explicit = prefix.upper() if separator and prefix.upper() in ("DNS", "IP") else None
        content = suffix.strip() if explicit else item
        try:
            address = ip_address(content)
        except ValueError:
            address = None
        if explicit == "IP" and address is None:
            raise ValueError("Invalid IP alternative name")
        if address is not None and explicit != "DNS":
            if getattr(address, "scope_id", None):
                raise ValueError("Scoped IP alternative names are not supported")
            entry = {"type": "IP", "value": str(address)}
        else:
            entry = {"type": "DNS", "value": normalize_dns(content)}
        identity = (entry["type"], entry["value"])
        if identity not in seen:
            result.append(entry)
            seen.add(identity)
    if len(result) > 100:
        raise ValueError("Use at most 100 alternative names")
    return result


class CertificateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["root", "intermediate", "server"]
    parent_id: int | None = Field(default=None, gt=0)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    state: str | None = Field(default=None, max_length=128)
    locality: str | None = Field(default=None, max_length=128)
    organization: str | None = Field(default=None, max_length=128)
    organizational_unit: str | None = Field(default=None, max_length=128)
    common_name: str = Field(min_length=1, max_length=64)
    validity_days: int = Field(ge=1, le=36500)
    alt_names: str | None = Field(default=None, max_length=30000)

    @field_validator("name", "country", "state", "locality", "organization", "organizational_unit", "common_name", mode="before")
    @classmethod
    def trim_text(cls, value, info):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(character) < 32 for character in value):
                raise ValueError("Control characters are not allowed")
            if not value and info.field_name in ("country", "state", "locality", "organization", "organizational_unit"):
                return None
        return value

    @field_validator("country")
    @classmethod
    def country_code(cls, value):
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z]{2}", value):
            raise ValueError("Country must be a two-letter code")
        return value.upper()

    @model_validator(mode="after")
    def hierarchy_and_names(self):
        if (self.kind == "root") != (self.parent_id is None):
            raise ValueError("Only a root CA can have no parent")
        names = parse_alt_names(self.alt_names)
        if self.kind != "server" and names:
            raise ValueError("Alternative names belong to HTTPS certificates")
        # cryptography enforces the encoded length for attributes such as CN.
        try:
            self.subject_name()
        except ValueError:
            raise ValueError("Invalid subject attribute length") from None
        return self

    def subject_values(self):
        return {field: getattr(self, field) for field in SUBJECT_FIELDS if getattr(self, field)}

    def subject_name(self):
        return x509.Name([x509.NameAttribute(oid, getattr(self, field))
                          for field, oid in SUBJECT_FIELDS.items() if getattr(self, field)])


def key_password():
    value = os.getenv("PKI_MASTER_KEY", "")
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
        if len(decoded) != 32:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(503, "Configure a persistent PKI_MASTER_KEY before issuing or downloading private keys") from None
    return decoded


def load_lineage(connection, parent_id):
    lineage = []
    seen = set()
    while parent_id is not None:
        if parent_id in seen:
            raise HTTPException(409, "Invalid CA hierarchy")
        seen.add(parent_id)
        row = connection.execute("SELECT * FROM managed_certificate WHERE id=%s FOR SHARE", (parent_id,)).fetchone()
        if row is None:
            raise HTTPException(422, "Parent CA not found")
        if row["kind"] not in ("root", "intermediate"):
            raise HTTPException(422, "Parent must be a CA")
        lineage.append(row)
        parent_id = row["parent_id"]
    return lineage


def read_private_key(pem, password):
    try:
        return serialization.load_pem_private_key(bytes(pem), password=password)
    except (ValueError, TypeError):
        raise HTTPException(503, "Stored private key cannot be opened with the configured PKI_MASTER_KEY") from None


def issue_material(data, settings, lineage, password, *, now=None):
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    not_after = now + timedelta(days=data.validity_days)
    ca = data.kind != "server"
    if data.kind == "root" and lineage or data.kind != "root" and not lineage:
        raise HTTPException(422, "Invalid issuer hierarchy")
    # Every ancestor must remain valid, not only the immediate signing CA.
    for ancestor in lineage:
        if ancestor["kind"] not in ("root", "intermediate"):
            raise HTTPException(422, "Parent must be a CA")
        if ancestor["not_before"] > now or ancestor["not_after"] <= now:
            raise HTTPException(422, "An issuer CA is not currently valid")
        if not_after > ancestor["not_after"]:
            raise HTTPException(422, "Requested validity exceeds an issuer CA expiration; reduce validity days")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=settings["rsa_key_size"])
    subject = data.subject_name()
    issuer_key = private_key
    issuer_name = subject
    if lineage:
        issuer = x509.load_pem_x509_certificate(bytes(lineage[0]["certificate_pem"]))
        issuer_key = read_private_key(lineage[0]["private_key_pem"], password)
        if issuer_key.public_key().public_numbers() != issuer.public_key().public_numbers():
            raise HTTPException(409, "Issuer certificate and private key do not match")
        issuer_name = issuer.subject
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name)
        .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
            key_encipherment=not ca, data_encipherment=False, key_agreement=False,
            key_cert_sign=ca, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False))
    names = parse_alt_names(data.alt_names)
    if not ca:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        if names:
            builder = builder.add_extension(x509.SubjectAlternativeName([
                x509.DNSName(entry["value"]) if entry["type"] == "DNS" else x509.IPAddress(ip_address(entry["value"]))
                for entry in names]), critical=False)
    certificate = builder.sign(issuer_key, {"SHA256": hashes.SHA256, "SHA384": hashes.SHA384, "SHA512": hashes.SHA512}[settings["signature_hash"]]())
    validate_certificate_chain(certificate, data.kind, lineage, now=now)
    return {
        "certificate_pem": certificate.public_bytes(serialization.Encoding.PEM),
        "private_key_pem": private_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password)),
        "serial": str(certificate.serial_number),
        "fingerprint_sha256": certificate.fingerprint(hashes.SHA256()).hex(),
        "not_before": now, "not_after": not_after, "alt_names": names,
    }


def list_certificates(authorities):
    with get_connection() as connection:
        rows = connection.execute("SELECT " + PUBLIC_COLUMNS + """,
            (SELECT count(*) FROM managed_certificate child WHERE child.parent_id=c.id) AS children_count
            FROM managed_certificate c LEFT JOIN managed_certificate p ON p.id=c.parent_id
            WHERE """ + ("TRUE" if authorities is None else "c.kind IN ('root', 'intermediate')" if authorities else "c.kind='server'") +
            " ORDER BY c.name,c.id").fetchall()

    now = datetime.now(timezone.utc)
    return [{**dict(row), **certificate_expiration(row["not_after"], now)} for row in rows]


@router.get("/api/cacerts")
def list_ca():
    return list_certificates(True)


@router.get("/api/selfsignedcerts")
def list_server_certificates():
    return list_certificates(None)


@router.get("/api/selfsignedcerts/issuers")
def unified_issuers():
    with get_connection() as connection:
        return connection.execute("""SELECT c.id,c.name,c.kind,
            NOT EXISTS (SELECT 1 FROM managed_certificate child
                WHERE child.parent_id=c.id AND child.kind IN ('root','intermediate')) AS server_eligible
            FROM managed_certificate c WHERE c.kind IN ('root','intermediate')
            AND c.not_before <= NOW() AND c.not_after > NOW() ORDER BY c.name,c.id""").fetchall()


@router.get("/api/cacerts/issuers")
def available_issuers(purpose: Literal["ca", "server"] = "ca"):
    condition = """c.kind IN ('root','intermediate') AND NOT EXISTS (
        SELECT 1 FROM managed_certificate child WHERE child.parent_id=c.id AND child.kind IN ('root','intermediate')
    )""" if purpose == "server" else "c.kind IN ('root','intermediate')"
    with get_connection() as connection:
        return connection.execute("""SELECT c.id,c.name,c.kind,c.not_after FROM managed_certificate c
            WHERE """ + condition + """ AND c.not_before <= NOW() AND c.not_after > NOW()
            ORDER BY c.name,c.id""").fetchall()


def store_certificate(connection, data, subject, validity_days, settings, material, *, source):
    return connection.execute("""INSERT INTO managed_certificate
        (parent_id,kind,name,subject,alt_names,validity_days,not_before,not_after,
         key_algorithm,rsa_key_size,signature_hash,serial,fingerprint_sha256,source,certificate_pem,private_key_pem)
        VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id,name,kind,parent_id,source""", (
            data.parent_id,data.kind,data.name,json.dumps(subject),json.dumps(material["alt_names"]),
            validity_days,material["not_before"],material["not_after"],
            settings["key_algorithm"],settings["rsa_key_size"],settings["signature_hash"],
            material["serial"],material["fingerprint_sha256"],source,material["certificate_pem"],material["private_key_pem"],
        )).fetchone()


def prepare_issuer(connection, data):
    # Serialize imports and new issuance against changes to the CA hierarchy.
    if data.parent_id is not None:
        connection.execute("SELECT id FROM managed_certificate WHERE id=%s FOR UPDATE", (data.parent_id,)).fetchone()
    lineage = load_lineage(connection, data.parent_id)
    if data.kind == "server":
        if not lineage or lineage[0]["kind"] not in ("root", "intermediate"):
            raise HTTPException(422, "Server certificates must be signed by a CA")
        children = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM managed_certificate WHERE parent_id=%s AND kind IN ('root','intermediate')) AS has_children",
            (data.parent_id,),
        ).fetchone()
        if children["has_children"]:
            raise HTTPException(409, "This CA has subordinate CAs; select a CA without intermediate CAs")
    return lineage


def create_certificate(data, *, authorities):
    if authorities is not None and (data.kind != "server") != authorities:
        raise HTTPException(422, "Certificate type does not match this page")
    password = key_password()
    settings = load_config()["self-signed"]
    try:
        with get_connection(read_only=False) as connection:
            lineage = prepare_issuer(connection, data)
            material = issue_material(data, settings, lineage, password)
            record = store_certificate(connection, data, data.subject_values(), data.validity_days, settings, material, source="generated")
        return record
    except UniqueViolation:
        raise HTTPException(409, "A certificate with this name, fingerprint or issuer/serial already exists") from None
    except (ForeignKeyViolation, CheckViolation):
        raise HTTPException(409, "Certificate hierarchy changed or violates issuer constraints; reload and retry") from None


@router.post("/api/cacerts", status_code=201)
def create_ca(data: CertificateCreate):
    return create_certificate(data, authorities=True)


@router.post("/api/selfsignedcerts", status_code=201)
def create_server_certificate(data: CertificateCreate):
    return create_certificate(data, authorities=None)


def delete_certificate(record_id, *, authorities):
    try:
        with get_connection(read_only=False) as connection:
            row = connection.execute("DELETE FROM managed_certificate WHERE id=%s AND " +
                ("TRUE" if authorities is None else "kind IN ('root','intermediate')" if authorities else "kind='server'") + " RETURNING id", (record_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Certificate not found")
        return {"id": row["id"], "deleted": True}
    except (ForeignKeyViolation, RestrictViolation):
        raise HTTPException(409, "Cannot delete a CA with intermediate CAs or issued certificates") from None


@router.delete("/api/cacerts/{record_id}")
def delete_ca(record_id: int):
    return delete_certificate(record_id, authorities=True)


@router.delete("/api/selfsignedcerts/{record_id}")
def delete_server_certificate(record_id: int):
    return delete_certificate(record_id, authorities=None)


def download_certificate(record_id, artifact, *, authorities):
    with get_connection() as connection:
        # A stable snapshot keeps chain downloads consistent with concurrent deletions.
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        row = connection.execute("SELECT id,parent_id,name,kind,certificate_pem,private_key_pem FROM managed_certificate WHERE id=%s AND " +
            ("TRUE" if authorities is None else "kind IN ('root','intermediate')" if authorities else "kind='server'"), (record_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Certificate not found")
        if artifact == "key":
            key = read_private_key(row["private_key_pem"], key_password())
            content = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            extension = ".key"
        else:
            content = bytes(row["certificate_pem"])
            extension = ".crt"
            if artifact == "chain":
                parent_id = row["parent_id"]
                seen = {row["id"]}
                while parent_id is not None:
                    if parent_id in seen:
                        raise HTTPException(409, "Invalid CA hierarchy")
                    seen.add(parent_id)
                    parent = connection.execute("SELECT id,parent_id,certificate_pem FROM managed_certificate WHERE id=%s", (parent_id,)).fetchone()
                    if parent is None:
                        raise HTTPException(409, "Incomplete CA chain")
                    content += bytes(parent["certificate_pem"])
                    parent_id = parent["parent_id"]
                extension = ".chain.pem"
    filename = (re.sub(r"[^A-Za-z0-9._-]+", "-", row["name"]).strip(".-") or "certificate") + "-" + str(row["id"]) + extension
    return Response(content=content, media_type="application/octet-stream", headers={
        "Content-Disposition": 'attachment; filename="' + filename + '"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
    })


@router.get("/api/cacerts/{record_id}/download/{artifact}")
def download_ca(record_id: int, artifact: Literal["certificate", "key", "chain"]):
    return download_certificate(record_id, artifact, authorities=True)


@router.get("/api/selfsignedcerts/{record_id}/download/{artifact}")
def download_server_certificate(record_id: int, artifact: Literal["certificate", "key", "chain"]):
    return download_certificate(record_id, artifact, authorities=None)


MAX_IMPORT_FILE_BYTES = 256 * 1024
MAX_IMPORT_BODY_BYTES = 1024 * 1024


class CertificateImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["root", "intermediate", "server"]
    parent_id: int | None = Field(default=None, gt=0)
    certificate: SecretStr
    private_key: SecretStr
    key_password: SecretStr | None = None

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(character) < 32 for character in value):
                raise ValueError("Control characters are not allowed in Name")
        return value

    @field_validator("certificate", "private_key", mode="before")
    @classmethod
    def encoded_file_limit(cls, value):
        if not isinstance(value, str) or not value or len(value) > 4 * ((MAX_IMPORT_FILE_BYTES + 2) // 3):
            raise ValueError("Choose certificate and key files of at most 256 KB each")
        return value

    @field_validator("key_password", mode="before")
    @classmethod
    def password_limit(cls, value):
        if value is not None and (not isinstance(value, str) or len(value.encode("utf-8")) > 1024):
            raise ValueError("Key password is too long")
        return value

    @model_validator(mode="after")
    def parent_for_type(self):
        if (self.kind == "root") != (self.parent_id is None):
            raise ValueError("Only a root CA can have no parent")
        return self


def extension_value(certificate, extension):
    try:
        return certificate.extensions.get_extension_for_class(extension).value
    except x509.ExtensionNotFound:
        return None


def certificate_profile(certificate, *, ca):
    constraints = extension_value(certificate, x509.BasicConstraints)
    if ca and (constraints is None or not constraints.ca):
        raise HTTPException(422, "The uploaded certificate is not a certificate authority")
    if not ca and constraints is not None and constraints.ca:
        raise HTTPException(422, "Choose a server certificate, not a CA certificate")
    usage = extension_value(certificate, x509.KeyUsage)
    if usage is not None:
        if ca and not usage.key_cert_sign:
            raise HTTPException(422, "CA key usage does not permit certificate signing")
        if not ca and (usage.key_cert_sign or not (usage.digital_signature or usage.key_encipherment)):
            raise HTTPException(422, "Certificate key usage is not suitable for a TLS server")
    extended = extension_value(certificate, x509.ExtendedKeyUsage)
    if extended is not None and not any(oid in extended for oid in (
        ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE
    )):
        raise HTTPException(422, "Certificate extended key usage does not permit server authentication")
    if extension_value(certificate, x509.NameConstraints) is not None:
        raise HTTPException(422, "Certificates with name constraints are not supported for import or issuance")
    if extension_value(certificate, x509.PolicyConstraints) is not None or extension_value(certificate, x509.InhibitAnyPolicy) is not None:
        raise HTTPException(422, "Certificates with policy constraints are not supported")
    supported_critical = {
        ExtensionOID.BASIC_CONSTRAINTS, ExtensionOID.KEY_USAGE, ExtensionOID.EXTENDED_KEY_USAGE,
        ExtensionOID.SUBJECT_ALTERNATIVE_NAME, ExtensionOID.SUBJECT_KEY_IDENTIFIER, ExtensionOID.AUTHORITY_KEY_IDENTIFIER,
    }
    for extension in certificate.extensions:
        if extension.critical and extension.oid not in supported_critical:
            raise HTTPException(422, "Unsupported critical certificate extension: " + extension.oid.dotted_string)
    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size not in (2048, 3072, 4096):
        raise HTTPException(422, "Supported certificates use RSA 2048, 3072 or 4096 bits")
    signature_hash = certificate.signature_hash_algorithm
    if signature_hash is None or signature_hash.name.upper() not in ("SHA256", "SHA384", "SHA512"):
        raise HTTPException(422, "Supported certificate signature hashes are SHA256, SHA384 and SHA512")
    return constraints


def validate_certificate_chain(certificate, kind, lineage, *, now=None):
    now = now or datetime.now(timezone.utc)
    if (kind == "root") != (not lineage):
        raise HTTPException(422, "Invalid issuer hierarchy")
    chain = [certificate] + [x509.load_pem_x509_certificate(bytes(row["certificate_pem"])) for row in lineage]
    constraints = []
    for index, item in enumerate(chain):
        constraints.append(certificate_profile(item, ca=kind != "server" or index > 0))
        if not item.not_valid_before_utc <= now < item.not_valid_after_utc:
            raise HTTPException(422, "The certificate and all issuing CAs must be currently valid")
    for index, issuer in enumerate(chain):
        constraint = constraints[index]
        if constraint is not None and constraint.ca and constraint.path_length is not None:
            subordinate_cas = sum(1 for child_index in range(index)
                if constraints[child_index] is not None and constraints[child_index].ca
                and chain[child_index].subject != chain[child_index].issuer)
            if subordinate_cas > constraint.path_length:
                raise HTTPException(422, "The issuing CA certificate does not allow this intermediate depth")
    for child, issuer in zip(chain, chain[1:]):
        if child.not_valid_before_utc < issuer.not_valid_before_utc or child.not_valid_after_utc > issuer.not_valid_after_utc:
            raise HTTPException(422, "Certificate validity must fit within the selected issuer chain")
        try:
            child.verify_directly_issued_by(issuer)
        except (InvalidSignature, ValueError, TypeError, UnsupportedAlgorithm):
            raise HTTPException(422, "Certificate was not signed by the selected parent CA") from None
        authority = extension_value(child, x509.AuthorityKeyIdentifier)
        subject_key = extension_value(issuer, x509.SubjectKeyIdentifier)
        if authority is not None:
            if authority.key_identifier is not None and subject_key is not None and authority.key_identifier != subject_key.digest:
                raise HTTPException(422, "Certificate authority key identifier does not match the selected parent")
            if authority.authority_cert_serial_number is not None and authority.authority_cert_serial_number != issuer.serial_number:
                raise HTTPException(422, "Certificate authority serial does not match the selected parent")
    try:
        chain[-1].verify_directly_issued_by(chain[-1])
    except (InvalidSignature, ValueError, TypeError, UnsupportedAlgorithm):
        raise HTTPException(422, "Root CA must have a valid self-signature") from None


def decode_import_file(value):
    try:
        content = base64.b64decode(value.get_secret_value(), validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid uploaded file encoding") from None
    if not content or len(content) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(422, "Choose certificate and key files of at most 256 KB each")
    return content


def read_import_files(data):
    certificate_bytes = decode_import_file(data.certificate)
    key_bytes = decode_import_file(data.private_key)
    try:
        if certificate_bytes.lstrip().startswith(b"-----BEGIN"):
            certificates = x509.load_pem_x509_certificates(certificate_bytes)
            if len(certificates) != 1 or b"PRIVATE KEY" in certificate_bytes:
                raise HTTPException(422, "Upload one public certificate; register each CA in the chain separately")
            certificate = certificates[0]
        else:
            certificate = x509.load_der_x509_certificate(certificate_bytes)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise HTTPException(422, "Invalid certificate file; use an X.509 certificate in PEM or DER format") from None
    password = data.key_password.get_secret_value().encode("utf-8") if data.key_password and data.key_password.get_secret_value() else None
    try:
        loader = serialization.load_pem_private_key if key_bytes.lstrip().startswith(b"-----BEGIN") else serialization.load_der_private_key
        private_key = loader(key_bytes, password=password)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise HTTPException(422, "Cannot open the private key; check the key file and its password") from None
    if not isinstance(private_key, rsa.RSAPrivateKey) or not isinstance(certificate.public_key(), rsa.RSAPublicKey):
        raise HTTPException(422, "Only RSA certificate/key pairs are currently supported")
    if private_key.public_key().public_numbers() != certificate.public_key().public_numbers():
        raise HTTPException(422, "The private key does not match the uploaded certificate")
    return certificate, private_key


def imported_material(certificate, private_key, master_password):
    subject = {}
    for field, oid in SUBJECT_FIELDS.items():
        attributes = certificate.subject.get_attributes_for_oid(oid)
        if attributes:
            subject[field] = ", ".join(attribute.value for attribute in attributes)
    names = []
    sans = extension_value(certificate, x509.SubjectAlternativeName)
    for value in sans or []:
        if isinstance(value, x509.DNSName):
            names.append({"type":"DNS", "value":value.value})
        elif isinstance(value, x509.IPAddress):
            names.append({"type":"IP", "value":str(value.value)})
        elif isinstance(value, x509.UniformResourceIdentifier):
            names.append({"type":"URI", "value":value.value})
        elif isinstance(value, x509.RFC822Name):
            names.append({"type":"email", "value":value.value})
        else:
            raise HTTPException(422, "Unsupported alternative name type in the uploaded certificate")
    serial = str(certificate.serial_number)
    if certificate.serial_number <= 0 or len(serial) > 48:
        raise HTTPException(422, "Certificate serial is outside the supported range")
    days = math.ceil((certificate.not_valid_after_utc - certificate.not_valid_before_utc).total_seconds() / 86400)
    if not 1 <= days <= 36500:
        raise HTTPException(422, "Certificate validity must be between one day and 100 years")
    settings = {"key_algorithm":"RSA", "rsa_key_size":private_key.key_size,
                "signature_hash":certificate.signature_hash_algorithm.name.upper()}
    material = {
        "certificate_pem":certificate.public_bytes(serialization.Encoding.PEM),
        "private_key_pem":private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(master_password)),
        "serial":serial, "fingerprint_sha256":certificate.fingerprint(hashes.SHA256()).hex(),
        "not_before":certificate.not_valid_before_utc, "not_after":certificate.not_valid_after_utc,
        "alt_names":names,
    }
    return subject, days, settings, material


def import_certificate(data, *, authorities):
    if authorities is not None and (data.kind != "server") != authorities:
        raise HTTPException(422, "Certificate type does not match this page")
    master_password = key_password()
    try:
        certificate, private_key = read_import_files(data)
        with get_connection(read_only=False) as connection:
            lineage = prepare_issuer(connection, data)
            validate_certificate_chain(certificate, data.kind, lineage)
            subject, days, settings, material = imported_material(certificate, private_key, master_password)
            if data.kind == "server" and not any(name["type"] in ("DNS","IP") for name in material["alt_names"]) and not subject.get("common_name", "").strip():
                raise HTTPException(422, "Server certificates need a Common Name when no DNS or IP SAN is present")
            result = store_certificate(connection, data, subject, days, settings, material, source="imported")
        return result
    except UniqueViolation:
        raise HTTPException(409, "A certificate with this name, fingerprint or issuer/serial already exists") from None
    except (ForeignKeyViolation, CheckViolation):
        raise HTTPException(409, "Certificate hierarchy changed or violates issuer constraints; reload and retry") from None
    except (ValueError, TypeError, UnsupportedAlgorithm, x509.DuplicateExtension):
        raise HTTPException(422, "Invalid or unsupported certificate/key data") from None


async def receive_import(request, *, authorities):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_IMPORT_BODY_BYTES:
            raise HTTPException(413, "Certificate import request is too large")
        body.extend(chunk)
    try:
        data = CertificateImport.model_validate(json.loads(body))
    except ValidationError as error:
        # Never echo uploaded keys/passwords in validation responses.
        messages = [".".join(map(str, entry["loc"])) + ": " + entry["msg"] for entry in error.errors()]
        raise HTTPException(422, "; ".join(messages)) from None
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, "Invalid certificate import request") from None
    return await run_in_threadpool(import_certificate, data, authorities=authorities)


@router.post("/api/cacerts/import", status_code=201)
async def import_ca(request: Request):
    return await receive_import(request, authorities=True)


@router.post("/api/selfsignedcerts/import", status_code=201)
async def import_server_certificate(request: Request):
    return await receive_import(request, authorities=None)
