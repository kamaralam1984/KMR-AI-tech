import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.request_context import get_request_id

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, request_id=get_request_id(), details=details)
    )
    # Responses built by the outermost error middleware bypass RequestContextMiddleware,
    # so the request ID header is set here as well.
    merged = {"X-Request-ID": get_request_id(), **(headers or {})}
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=merged,
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "error")
    # Only an unexpected 500 is masked; deliberate 502/503 answers carry a message for users.
    message = "Internal server error." if exc.status_code == 500 else str(exc.detail)
    headers = dict(exc.headers) if exc.headers else None
    return error_response(exc.status_code, code, message, headers=headers)


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Report where and why a field failed, never the submitted value.
    details = [
        {"location": [str(p) for p in err["loc"]], "message": err["msg"], "type": err["type"]}
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed.", details)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={"method": request.method, "path": request.url.path},
    )
    return error_response(500, "internal_error", "Internal server error.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
