"""Bounded UTF-8/Base64 conversion without storing submitted content."""
import base64
import binascii
import json
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/base64", tags=["tools"])
MAX_TEXT_BYTES = 1024 * 1024
MAX_BODY_BYTES = 6 * MAX_TEXT_BYTES + 1024


class Conversion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversion: Literal["encode", "decode"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)

    @field_validator("text")
    @classmethod
    def require_content(cls, value):
        if not value.strip():
            raise ValueError("From must not be empty")
        return value


def convert(data):
    raw = data.text.encode("utf-8")
    if len(raw) > MAX_TEXT_BYTES:
        return {"detail": "From must contain at most 1 MiB of UTF-8 text."}, 413
    if data.conversion == "encode":
        return {"text": base64.b64encode(raw).decode("ascii")}, 200
    try:
        decoded = base64.b64decode("".join(data.text.split()).encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeError):
        return {"detail": "From must contain valid Base64 text, including any required padding."}, 422
    try:
        return {"text": decoded.decode("utf-8")}, 200
    except UnicodeDecodeError:
        return {"detail": "Decoded content is not valid UTF-8 text."}, 422


@router.post("/convert")
async def convert_base64(request: Request):
    def respond(content, status=200):
        return JSONResponse(content, status_code=status, headers={"Cache-Control": "no-store"})
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return respond({"detail": "Send conversion and text as JSON."}, 415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            return respond({"detail": "Conversion request is too large."}, 413)
        body.extend(chunk)
    try:
        data = Conversion.model_validate(json.loads(body))
        # JSON can contain isolated UTF-16 surrogates, which are not UTF-8 text.
        data.text.encode("utf-8")
    except (ValidationError, ValueError, UnicodeError):
        return respond({"detail": "Choose a valid conversion and provide non-empty UTF-8 text (up to 1 MiB)."}, 422)
    result, status = await run_in_threadpool(convert, data)
    return respond(result, status)
