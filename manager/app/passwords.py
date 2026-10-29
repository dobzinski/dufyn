"""Optional password records. List and write responses never expose secrets."""
import base64
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import UniqueViolation

from app.database import get_connection
from app.pki import key_password


def require_recording(response: Response):
    from app.main import config
    response.headers["Cache-Control"] = "no-store"
    if not config.get("password", {}).get("manager", False):
        raise HTTPException(404, "Password records are disabled")


router = APIRouter(prefix="/api/passwords", tags=["passwords"], dependencies=[Depends(require_recording)])
COLUMNS = "id, name, account, url, note, created_at, updated_at, character_count, use_uppercase, use_lowercase, use_numbers, use_symbols"


class PasswordWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    account: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=100)
    password: str = Field(min_length=1, max_length=999)
    character_count: int = Field(default=24, ge=1, le=999, strict=True)
    use_uppercase: Literal["Y", "N"] = "Y"
    use_lowercase: Literal["Y", "N"] = "Y"
    use_numbers: Literal["Y", "N"] = "Y"
    use_symbols: Literal["Y", "N"] = "Y"

    @field_validator("name", "account", "url", "note", mode="before")
    @classmethod
    def trim_metadata(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("account", "url", "note")
    @classmethod
    def optional_metadata(cls, value):
        return value or None


def cipher():
    try:
        return Fernet(base64.urlsafe_b64encode(key_password()))
    except (HTTPException, ValueError, UnicodeError):
        raise HTTPException(503, "Configure a persistent PKI_MASTER_KEY before storing or opening passwords") from None


@router.get("")
def list_passwords():
    with get_connection() as connection:
        return connection.execute(f"SELECT {COLUMNS} FROM password_record ORDER BY name, id").fetchall()


@router.get("/{record_id}")
def get_password(record_id: int):
    decoder = cipher()
    with get_connection() as connection:
        row = connection.execute(f"SELECT {COLUMNS}, password_encrypted FROM password_record WHERE id = %s", (record_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Password record not found")
    result = dict(row)
    encrypted = result.pop("password_encrypted")
    try:
        result["password"] = decoder.decrypt(bytes(encrypted)).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        raise HTTPException(503, "Stored password cannot be opened with the configured PKI_MASTER_KEY") from None
    return result


@router.post("", status_code=201)
def create_password(data: PasswordWrite):
    encrypted = cipher().encrypt(data.password.encode("utf-8"))
    try:
        with get_connection(read_only=False) as connection:
            return connection.execute(
                f"INSERT INTO password_record (name, account, url, note, password_encrypted, character_count, use_uppercase, use_lowercase, use_numbers, use_symbols) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {COLUMNS}",
                (data.name, data.account, data.url, data.note, encrypted, data.character_count, data.use_uppercase, data.use_lowercase, data.use_numbers, data.use_symbols),
            ).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "A password record with this name already exists") from None


@router.put("/{record_id}")
def update_password(record_id: int, data: PasswordWrite):
    encoder = cipher()
    try:
        with get_connection(read_only=False) as connection:
            # Verify the existing ciphertext before replacing it, protecting against
            # an accidentally changed master key and a partially unreadable vault.
            existing = connection.execute("SELECT password_encrypted FROM password_record WHERE id = %s FOR UPDATE", (record_id,)).fetchone()
            if existing is None:
                raise HTTPException(404, "Password record not found")
            try:
                encoder.decrypt(bytes(existing["password_encrypted"]))
            except (InvalidToken, ValueError):
                raise HTTPException(503, "Stored password cannot be opened with the configured PKI_MASTER_KEY") from None
            return connection.execute(
                f"UPDATE password_record SET name = %s, account = %s, url = %s, note = %s, password_encrypted = %s, character_count = %s, use_uppercase = %s, use_lowercase = %s, use_numbers = %s, use_symbols = %s, updated_at = now() WHERE id = %s RETURNING {COLUMNS}",
                (data.name, data.account, data.url, data.note, encoder.encrypt(data.password.encode("utf-8")), data.character_count, data.use_uppercase, data.use_lowercase, data.use_numbers, data.use_symbols, record_id),
            ).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "A password record with this name already exists") from None


@router.delete("/{record_id}")
def delete_password(record_id: int):
    with get_connection(read_only=False) as connection:
        result = connection.execute("DELETE FROM password_record WHERE id = %s RETURNING id", (record_id,)).fetchone()
        if result is None:
            raise HTTPException(404, "Password record not found")
    return {"id": result["id"], "deleted": True}
