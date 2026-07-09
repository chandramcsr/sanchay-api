"""
FastAPI/Pydantic's default validation error handler echoes the entire
submitted request body back in the error response (the 'input' field,
meant for debugging) — including a plaintext password if some other
field in the same request fails validation. This handler redacts it
before the response ever leaves the server, so a malformed signup/
login request can't put a plaintext password into a response body,
and from there into logs, error trackers, or a browser's network tab.
"""

import logging

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("sanchay.errors")

REDACTED = "[redacted]"
SENSITIVE_FIELDS = {"password"}


def _redact(value):
    if isinstance(value, dict):
        return {k: (REDACTED if k in SENSITIVE_FIELDS else v) for k, v in value.items()}
    return value


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        err = dict(err)
        # Pydantic's "ctx" can carry the raised exception object itself
        # (e.g. the ValueError from a @field_validator) — not JSON
        # serializable and not useful to the client either way.
        err.pop("ctx", None)
        loc = err.get("loc", ())
        if loc and loc[-1] in SENSITIVE_FIELDS:
            # The error is ABOUT the password field itself (e.g. "too
            # short") — the offending value would be the raw password.
            err["input"] = REDACTED
        elif "input" in err:
            # The error is about some OTHER field, but Pydantic's
            # "input" for a whole-model validation failure (e.g. a
            # required field missing entirely) is the full submitted
            # body — which may itself contain a password.
            err["input"] = _redact(err["input"])
        errors.append(err)

    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=jsonable_encoder({"detail": errors}))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all for anything not already handled (a genuine bug, an
    unexpected third-party failure, etc.). Two jobs, kept strictly
    separate: log the REAL error, with a stack trace, server-side
    where whoever's on call can actually see it — and return a
    generic, safe message to the client, which never sees internals
    (a file path, a raw exception message that might contain request
    data, a stack trace) that could otherwise leak through an
    unhandled-error response.
    """
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred. Please try again."},
    )
