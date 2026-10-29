"""Token-scoped, encrypted snapshots of the configuration used by remote scanners."""
import base64
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from app.database import get_connection

MAX_CONFIG_BYTES = 262144


def canonical_config(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


class ConfigurationSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, JsonValue]

    @field_validator("config")
    @classmethod
    def validate_config(cls, value):
        if not value:
            raise ValueError("Configuration must not be empty")
        try:
            payload = canonical_config(value)
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("Configuration must contain valid JSON values") from None
        if len(payload) > MAX_CONFIG_BYTES:
            raise ValueError("Configuration exceeds 256 KiB")
        controller = value.get("controller")
        if isinstance(controller, dict) and "token" in controller:
            raise ValueError("Send the scanner token only in the Authorization header")
        return value


def configuration_cipher():
    # Same 32-byte persistent key and Fernet format as manager password records.
    try:
        key = base64.b64decode(os.getenv("PKI_MASTER_KEY", ""), altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError()
        return Fernet(base64.urlsafe_b64encode(key))
    except (ValueError, TypeError):
        raise HTTPException(503, "Configure a persistent PKI_MASTER_KEY before synchronizing scanner configurations") from None


def create_configuration_router(scanner_identity):
    router = APIRouter(prefix="/v1/private/scanner", tags=["scanner configuration"])

    @router.post("/config")
    def sync_configuration(body: ConfigurationSync, response: Response, scanner: dict = Depends(scanner_identity)):
        response.headers["Cache-Control"] = "no-store"
        encoder = configuration_cipher()
        payload = canonical_config(body.config)
        with get_connection(read_only=False) as connection:
            stored = connection.execute("""
                SELECT config_encrypted FROM scanner
                WHERE id = %s AND site_id = %s AND enabled = 'Y' FOR UPDATE
            """, (scanner["id"], scanner["site_id"])).fetchone()
            if stored is None:
                raise HTTPException(401, "Invalid or disabled scanner token")
            previous = stored["config_encrypted"]
            changed = previous is None
            if previous is not None:
                try:
                    changed = encoder.decrypt(bytes(previous)) != payload
                except InvalidToken:
                    # A changed key must not silently destroy the previous backup.
                    raise HTTPException(503, "Stored scanner configuration cannot be opened with the configured PKI_MASTER_KEY") from None
            if changed:
                dates = connection.execute("""
                    UPDATE scanner SET config_encrypted = %s, config_updated_at = NOW(),
                        config_received_at = NOW(), last_seen = NOW()
                    WHERE id = %s AND site_id = %s
                    RETURNING config_updated_at, config_received_at
                """, (encoder.encrypt(payload), scanner["id"], scanner["site_id"])).fetchone()
            else:
                dates = connection.execute("""
                    UPDATE scanner SET config_received_at = NOW(), last_seen = NOW()
                    WHERE id = %s AND site_id = %s
                    RETURNING config_updated_at, config_received_at
                """, (scanner["id"], scanner["site_id"])).fetchone()
        return {"changed": changed, **dates}

    return router
