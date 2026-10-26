"""Error responses.

Every 4xx body names the offending field and never repeats a submitted value:
the recipient and template options are PII. Messages here are fixed strings
chosen by error type, not taken from the validation library.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core import ErrorDetails

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
MAX_IDEMPOTENCY_KEY_LENGTH = 255

_LOCATION_PREFIXES = {"body", "path", "query", "header", "cookie"}
_CHANNEL_TAGS = {"email", "sms"}

_MESSAGES = {
    "missing": "field is required",
    "extra_forbidden": "field is not permitted",
    "string_type": "must be a string",
    "string_too_short": "must not be empty",
    "int_type": "must be an integer",
    "int_parsing": "must be an integer",
    "int_from_float": "must be an integer",
    "float_type": "must be a number",
    "float_parsing": "must be a number",
    "bool_type": "must be a boolean",
    "bool_parsing": "must be a boolean",
    "dict_type": "must be an object",
    "model_attributes_type": "must be an object",
    "union_tag_invalid": "channel must be 'email' or 'sms'",
    "union_tag_not_found": "channel must be 'email' or 'sms'",
    "json_invalid": "request body is not valid JSON",
    "uuid_parsing": "must be a valid UUID",
    "uuid_type": "must be a valid UUID",
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        errors: list[dict[str, str]] | None = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.errors = errors or []
        super().__init__(message)

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "errors": self.errors}


def sanitize_errors(
    errors: list[ErrorDetails] | tuple[ErrorDetails, ...], prefix: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """Field path and fixed message only; drops ``input`` and ``ctx``."""
    cleaned = []
    for error in errors:
        loc = list(error["loc"])
        if loc and loc[0] in _LOCATION_PREFIXES:
            loc = loc[1:]
        if loc and loc[0] in _CHANNEL_TAGS and error["type"] not in {"union_tag_invalid"}:
            loc = loc[1:]  # the discriminated-union branch name
        field = ".".join(str(part) for part in (*prefix, *loc))
        if error["type"] == "value_error":
            message = str(error["ctx"]["error"])  # our own fixed validator messages
        else:
            message = _MESSAGES.get(error["type"], "invalid value")
        cleaned.append({"field": field, "message": message})
    return cleaned


def check_idempotency_key(request: Request) -> str:
    key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    if key is None or not key.strip():
        raise ApiError(400, "invalid_idempotency_key", f"{IDEMPOTENCY_KEY_HEADER} header is required")
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiError(
            400,
            "invalid_idempotency_key",
            f"{IDEMPOTENCY_KEY_HEADER} must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters",
        )
    return key


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.method == "POST" and request.url.path == "/notifications":
            # A malformed body can fail before the key dependency runs; the
            # key check still comes first.
            try:
                check_idempotency_key(request)
            except ApiError as key_error:
                return JSONResponse(key_error.body(), status_code=key_error.status_code)
        error = ApiError(
            422, "validation_error", "request validation failed", sanitize_errors(exc.errors())
        )
        return JSONResponse(error.body(), status_code=422)
