"""Unified error handling and exception handlers returning consistent JSON envelopes."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from health_api.logging_config import request_id_ctx

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Application-level domain error with explicit code, message, and status."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def make_error_response(
    code: str,
    message: str,
    status_code: int,
    request_id: str | None = None,
) -> JSONResponse:
    """Build standardized error envelope."""
    rid = request_id or request_id_ctx.get() or "-"
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": rid,
        }
    }
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"X-Request-ID": rid},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI application."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or request_id_ctx.get() or "-"
        return make_error_response(exc.code, exc.message, exc.status_code, request_id=rid)

    async def http_exception_handler(
        request: Request, exc: HTTPException | StarletteHTTPException
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or request_id_ctx.get() or "-"
        code_map = {
            status.HTTP_400_BAD_REQUEST: "bad_request",
            status.HTTP_401_UNAUTHORIZED: "unauthorized",
            status.HTTP_403_FORBIDDEN: "forbidden",
            status.HTTP_404_NOT_FOUND: "not_found",
            status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
            status.HTTP_409_CONFLICT: "conflict",
            status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
        }
        detail = str(exc.detail) if hasattr(exc, "detail") else "HTTP error"
        code = code_map.get(exc.status_code, "http_error")

        if ":" in detail:
            parts = detail.split(":", 1)
            candidate_code = parts[0].strip()
            if " " not in candidate_code:
                code = candidate_code
                detail = parts[1].strip()
        elif detail == "writes_disabled":
            code = "writes_disabled"
        elif detail in {"missing API key", "invalid API key"}:
            code = "unauthorized"
        elif detail.startswith("server '") and detail.endswith("not found"):
            code = "server_not_found"
        elif detail.startswith("fault '") and detail.endswith("not found"):
            code = "fault_not_found"
        elif detail.startswith("server") and "not found" in detail:
            code = "server_not_found"

        return make_error_response(code, detail, exc.status_code, request_id=rid)

    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or request_id_ctx.get() or "-"
        errors = exc.errors()
        message = errors[0]["msg"] if errors else "Validation failed"
        return make_error_response(
            "validation_error",
            message,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            request_id=rid,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or request_id_ctx.get() or "-"
        logger.exception("unhandled internal error [request_id=%s]: %s", rid, exc)
        return make_error_response(
            "internal_error",
            "An unexpected internal server error occurred",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=rid,
        )
