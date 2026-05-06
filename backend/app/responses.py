from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.errors import AppError


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def success_response(request: Request, data: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "error": None,
        "request_id": get_request_id(request),
    }


def error_response(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
            "request_id": get_request_id(request),
        },
    )


def app_error_response(request: Request, exc: AppError) -> JSONResponse:
    return error_response(
        request,
        code=exc.code,
        message=exc.message,
        status_code=exc.http_status,
        details=exc.details,
    )
