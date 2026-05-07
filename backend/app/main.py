from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.auth import LoginRequest, clear_session_cookie, get_current_session, login_user, logout_user, set_session_cookie
from app.config import get_settings
from app.errors import AppError
from app.middleware import request_id_middleware
from app.responses import app_error_response, error_response, success_response
from app.schemas import ApiResponse


settings = get_settings()

app = FastAPI(title="Webmail MVP API", version="0.1.0")
app.middleware("http")(request_id_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    return app_error_response(request, exc)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return error_response(
        request,
        code="VALIDATION_ERROR",
        message="请求参数错误",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        details={"errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    return error_response(
        request,
        code="INTERNAL_ERROR",
        message="服务内部错误",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@app.get("/api/health", tags=["health"], response_model=ApiResponse)
def health(request: Request, verbose: bool = False) -> dict[str, object]:
    data = {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }
    if verbose:
        data["version"] = app.version
    return success_response(
        request,
        data,
    )


@app.get("/api/ready", tags=["health"], response_model=ApiResponse)
def ready(request: Request) -> dict[str, object]:
    return success_response(
        request,
        {
            "status": "ready",
            "dependencies": {
                "postgres": "configured",
                "redis": "configured",
            },
        },
    )


@app.post(
    "/api/auth/login",
    tags=["auth"],
    response_model=ApiResponse,
    summary="邮箱登录",
    response_description="登录成功后的当前用户信息",
)
def login(request: Request, response: Response, payload: LoginRequest) -> dict[str, object]:
    session_id, user_data = login_user(request, payload)
    set_session_cookie(response, session_id)
    return success_response(request, user_data)


@app.post(
    "/api/auth/logout",
    tags=["auth"],
    response_model=ApiResponse,
    summary="退出登录",
    response_description="退出结果",
)
def logout(request: Request, response: Response) -> dict[str, object]:
    logout_user(request)
    clear_session_cookie(response)
    return success_response(request, {"logged_out": True})


@app.get(
    "/api/auth/me",
    tags=["auth"],
    response_model=ApiResponse,
    summary="获取当前用户",
    response_description="当前会话绑定的邮箱账号",
)
def me(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, {"email": session.email})
