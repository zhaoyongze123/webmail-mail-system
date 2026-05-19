"""接口统一响应结构与错误响应构造。"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.errors import AppError


def get_request_id(request: Request) -> str:
    """从请求上下文中读取 request_id。"""
    return getattr(request.state, "request_id", "req_unknown")


def success_response(request: Request, data: Any) -> dict[str, Any]:
    """构造统一成功响应。"""
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
    """构造统一错误响应并回填 request_id 响应头。"""
    response = JSONResponse(
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
    response.headers["X-Request-ID"] = get_request_id(request)
    return response


def app_error_response(request: Request, exc: AppError) -> JSONResponse:
    """将应用异常转换为标准错误响应。"""
    return error_response(
        request,
        code=exc.code,
        message=exc.message,
        status_code=exc.http_status,
        details=exc.details,
    )
