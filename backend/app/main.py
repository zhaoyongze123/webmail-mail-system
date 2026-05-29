"""FastAPI 应用入口。

负责组装用户态 Webmail API、后台管理 API、中间件、异常处理和健康检查。
"""

import base64
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.attachments import store_temp_attachment_chunk, upload_temp_attachments
from app.admin_api import router as admin_router
from app.auth import (
    LoginRequest,
    RegisterRequest,
    clear_session_cookie,
    get_current_session,
    has_local_mailbox_password,
    login_user,
    logout_user,
    register_user,
    set_session_cookie,
    update_local_mailbox_password,
    update_session_password,
    verify_mailbox_password,
)
from app.compose import SendMailRequest, send_mail
from app.contacts import (
    create_contact,
    delete_contact,
    get_contact,
    list_blacklisted_contacts,
    search_contacts,
    search_contacts_for_autocomplete,
    update_contact,
)
from app.config import get_settings
from app.drafts import DraftPayload, delete_draft, get_draft, save_draft, update_draft
from app.errors import AppError
from app.mail_preferences import get_user_preferences, update_user_preferences
from app.notifications import (
    delete_push_subscription_record,
    get_notification_status,
    get_push_subscription_status,
    save_push_subscription_record,
    sync_notification_mailbox_secret,
    start_notification_worker,
    stop_notification_worker,
    update_notification_preferences,
)
from app.mailbox import (
    MessageOperationRequest,
    create_folder,
    delete_folder,
    get_message_attachment,
    get_message_attachment_preview,
    get_message_attachment_preview_thumbnail,
    get_message_attachment_preview_status,
    get_message_detail,
    list_folders,
    list_messages,
    operate_messages,
    rename_folder,
    search_messages,
)
from app.middleware import request_id_middleware
from app.observability import metrics_store, record_audit_event
from app.signatures import router as signatures_router
from app.responses import app_error_response, error_response, success_response
from app.schemas import (
    ApiResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    ContactCreateRequest,
    ContactUpdateRequest,
    FolderCreateRequest,
    FolderDeleteRequest,
    FolderOperationResponse,
    FolderRenameRequest,
)
from app.security import add_security_headers, log_sanitized_event, validate_attachment_id, validate_csrf_request


settings = get_settings()


def _system_preferences(preferences: dict[str, object]) -> dict[str, object]:
    """从完整设置对象中提取 system 配置。"""
    system_preferences = preferences.get("system")
    if isinstance(system_preferences, dict):
        return system_preferences
    return {}

@asynccontextmanager
async def lifespan(_: FastAPI):
    start_notification_worker()
    try:
        yield
    finally:
        stop_notification_worker()


app = FastAPI(title="Webmail MVP API", version="0.1.0", lifespan=lifespan)
app.middleware("http")(request_id_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)
app.include_router(signatures_router)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """在所有请求前后执行安全校验与响应头注入。"""
    log_sanitized_event(
        "request",
        method=request.method,
        path=request.url.path,
        query=dict(request.query_params),
        cookie_count=len(request.cookies),
    )
    try:
        validate_csrf_request(request)
    except AppError as exc:
        return add_security_headers(
            app_error_response(request, exc),
            allow_same_origin_frame=request.url.path.endswith("/preview") and "/attachments/" in request.url.path,
        )
    response = await call_next(request)
    return add_security_headers(
        response,
        allow_same_origin_frame=request.url.path.endswith("/preview") and "/attachments/" in request.url.path,
    )


class BulkMessageRequest(BaseModel):
    """用于单封或多封邮件批量操作的轻量请求体。"""

    folder: str = "INBOX"
    uids: list[str] = Field(default_factory=list)
    target_folder: str | None = None


class SettingsUpdateRequest(BaseModel):
    """用户设置更新请求体。"""

    system: dict[str, object] | None = None
    user: dict[str, object] | None = None
    theme: dict[str, object] | None = None

    @field_validator("system")
    @classmethod
    def validate_system(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        """校验系统设置中的时区、引用位置、分页和语言。"""
        if value is None:
            return value
        timezone = value.get("timezone")
        if isinstance(timezone, str):
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("无效的时区") from exc
        reply_quote_position = value.get("reply_quote_position")
        if reply_quote_position is not None and reply_quote_position not in {"top", "bottom"}:
            raise ValueError("引用位置无效")
        page_size = value.get("page_size")
        if page_size is not None and (not isinstance(page_size, int) or page_size < 1 or page_size > 100):
            raise ValueError("每页显示数量无效")
        language = value.get("language")
        if language is not None and (not isinstance(language, str) or len(language) < 2 or len(language) > 32):
            raise ValueError("语言配置无效")
        return value

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        """校验主题配置。"""
        if value is None:
            return value
        mode = value.get("mode")
        if mode is not None and mode not in {"light", "dark"}:
            raise ValueError("主题模式无效")
        return value


class NotificationPreferenceUpdateRequest(BaseModel):
    """通知偏好更新请求体。"""

    enabled: bool
    permission_state: str | None = None


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    """统一处理业务异常。"""
    return app_error_response(request, exc)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    """统一处理请求参数校验失败。"""
    if request.url.path == "/api/messages/send":
        record_audit_event(
            request,
            "compose.send_mail",
            success=False,
            metadata={"validation_error": True, "path": request.url.path},
        )
    return error_response(
        request,
        code="VALIDATION_ERROR",
        message="请求参数错误",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        details={"errors": str(exc)},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    """兜底捕获未预期异常。"""
    return error_response(
        request,
        code="INTERNAL_ERROR",
        message="服务内部错误",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@app.get("/api/health", tags=["health"], response_model=ApiResponse)
def health(request: Request, verbose: bool = False) -> dict[str, object]:
    """返回应用存活状态，并可按需附带版本信息。"""
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
    """返回服务就绪状态和核心依赖配置概览。"""
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


@app.get("/api/metrics", tags=["health"], response_model=ApiResponse)
def metrics(request: Request) -> dict[str, object]:
    """输出应用内存中的轻量指标快照。"""
    return success_response(
        request,
        {
            "status": "ok",
            "metrics": metrics_store.snapshot(),
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
    """校验邮箱账号密码并写入会话 Cookie。"""
    session_id, user_data, csrf_token = login_user(request, payload)
    set_session_cookie(request, response, session_id, csrf_token)
    return success_response(request, user_data)


@app.post(
    "/api/auth/register",
    tags=["auth"],
    response_model=ApiResponse,
    summary="注册邮箱账号",
    response_description="注册成功后的当前用户信息",
)
def register(request: Request, response: Response, payload: RegisterRequest) -> dict[str, object]:
    """注册邮箱账号并在成功后直接建立登录会话。"""
    session_id, user_data, csrf_token = register_user(request, payload)
    set_session_cookie(request, response, session_id, csrf_token)
    return success_response(request, user_data)


@app.post(
    "/api/auth/logout",
    tags=["auth"],
    response_model=ApiResponse,
    summary="退出登录",
    response_description="退出结果",
)
def logout(request: Request, response: Response) -> dict[str, object]:
    """销毁当前登录会话并清空浏览器端 Cookie。"""
    logout_user(request)
    clear_session_cookie(request, response)
    return success_response(request, {"logged_out": True})


@app.get(
    "/api/auth/me",
    tags=["auth"],
    response_model=ApiResponse,
    summary="获取当前用户",
    response_description="当前会话绑定的邮箱账号",
)
def me(request: Request) -> dict[str, object]:
    """返回当前会话绑定的邮箱地址。"""
    session = get_current_session(request)
    return success_response(request, {"email": session.email})


@app.get(
    "/api/settings",
    tags=["settings"],
    response_model=ApiResponse,
    summary="获取当前账号设置",
    response_description="当前账号和设置偏好",
)
def get_settings_api(request: Request) -> dict[str, object]:
    """读取当前账号的系统、用户和主题偏好。"""
    session = get_current_session(request)
    preferences = get_user_preferences(session.email)
    return success_response(
        request,
        {
            "account": {"email": session.email},
            "preferences": preferences,
        },
    )


@app.get(
    "/api/notifications/push-subscription",
    tags=["notifications"],
    response_model=ApiResponse,
    summary="读取当前推送订阅状态",
)
def fetch_push_subscription(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, get_push_subscription_status(session))


@app.post(
    "/api/notifications/push-subscription",
    tags=["notifications"],
    response_model=ApiResponse,
    summary="保存当前浏览器推送订阅",
)
async def save_push_subscription(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    payload = await request.json()
    return success_response(request, save_push_subscription_record(session, payload, request))


@app.delete(
    "/api/notifications/push-subscription",
    tags=["notifications"],
    response_model=ApiResponse,
    summary="删除当前账号推送订阅",
)
def remove_push_subscription(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, delete_push_subscription_record(session))


@app.get(
    "/api/notifications/status",
    tags=["notifications"],
    response_model=ApiResponse,
    summary="读取新邮件系统通知状态",
)
def fetch_notification_status(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, get_notification_status(session))


@app.put(
    "/api/notifications/preferences",
    tags=["notifications"],
    response_model=ApiResponse,
    summary="更新新邮件系统通知偏好",
)
def save_notification_preferences_api(
    request: Request,
    payload: NotificationPreferenceUpdateRequest,
) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, update_notification_preferences(session, payload.model_dump()))


@app.put(
    "/api/settings",
    tags=["settings"],
    response_model=ApiResponse,
    summary="更新当前账号设置",
    response_description="更新后的账号和设置偏好",
)
def update_settings_api(request: Request, payload: SettingsUpdateRequest) -> dict[str, object]:
    """更新当前账号的设置偏好。"""
    session = get_current_session(request)
    preferences = update_user_preferences(
        session.email,
        {
            "system": payload.system or {},
            "user": payload.user or {},
            "theme": payload.theme or {},
        },
    )
    return success_response(
        request,
        {
            "account": {"email": session.email},
            "preferences": preferences,
        },
    )


@app.post(
    "/api/settings/avatar",
    tags=["settings"],
    response_model=ApiResponse,
    summary="上传当前用户头像",
    response_description="更新后的账号和设置偏好",
)
async def upload_settings_avatar(request: Request, file: UploadFile = File(...)) -> dict[str, object]:
    """上传头像图片并以内联 data URL 形式写入用户偏好。"""
    session = get_current_session(request)
    content_type = (file.content_type or "").strip().lower()
    if not content_type.startswith("image/"):
        raise AppError(
            "SETTINGS_AVATAR_INVALID_TYPE",
            "头像仅支持图片文件",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    content = await file.read()
    max_bytes = 2 * 1024 * 1024
    if not content:
        raise AppError(
            "SETTINGS_AVATAR_EMPTY",
            "头像文件不能为空",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    if len(content) > max_bytes:
        raise AppError(
            "SETTINGS_AVATAR_TOO_LARGE",
            "头像大小不能超过 2 MB",
            http_status=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    data_url = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
    preferences = update_user_preferences(
        session.email,
        {
            "user": {
                "avatar_url": data_url,
            },
        },
    )
    return success_response(
        request,
        {
            "account": {"email": session.email},
            "preferences": preferences,
        },
    )


@app.post(
    "/api/settings/password",
    tags=["settings"],
    response_model=ApiResponse,
    summary="修改当前账号密码",
    response_description="修改密码结果",
)
def change_password_api(request: Request, payload: ChangePasswordRequest) -> dict[str, object]:
    """校验旧密码并更新当前会话缓存中的邮箱密码。"""
    session = get_current_session(request)
    if payload.current_password == payload.new_password:
        raise AppError(
            "PASSWORD_SAME_AS_CURRENT",
            "新密码不能与旧密码相同",
            http_status=status.HTTP_400_BAD_REQUEST,
        )

    verify_mailbox_password(session.email, payload.current_password)
    if has_local_mailbox_password(session.email):
        update_local_mailbox_password(session.email, payload.new_password)
    else:
        verify_mailbox_password(session.email, payload.new_password)
    update_session_password(session.session_id, payload.new_password)
    sync_notification_mailbox_secret(session.email, payload.new_password)
    record_audit_event(
        request,
        "settings.change_password",
        success=True,
        metadata={"email": session.email},
    )
    return success_response(request, ChangePasswordResponse(password_updated=True).model_dump())


@app.get(
    "/api/folders",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="获取邮箱文件夹",
    response_description="系统文件夹与未读数量",
)
def folders(request: Request) -> dict[str, object]:
    """同步并返回当前邮箱可见文件夹列表。"""
    session = get_current_session(request)
    return success_response(request, {"folders": list_folders(session)})


@app.post(
    "/api/folders",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="创建文件夹",
    response_description="创建后的文件夹信息",
)
def create_folder_api(request: Request, payload: FolderCreateRequest) -> dict[str, object]:
    """为当前邮箱创建自定义文件夹。"""
    session = get_current_session(request)
    return success_response(request, create_folder(session, payload.name))


@app.patch(
    "/api/folders/{folder}",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="重命名文件夹",
    response_description="重命名后的文件夹信息",
)
def rename_folder_api(request: Request, folder: str, payload: FolderRenameRequest) -> dict[str, object]:
    """重命名指定的自定义文件夹。"""
    session = get_current_session(request)
    if folder != payload.name:
        return success_response(request, rename_folder(session, folder, payload.new_name))
    return success_response(request, rename_folder(session, payload.name, payload.new_name))


@app.delete(
    "/api/folders/{folder}",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="删除文件夹",
    response_description="删除结果",
)
def delete_folder_api(request: Request, folder: str) -> dict[str, object]:
    """删除指定的自定义文件夹。"""
    session = get_current_session(request)
    return success_response(request, delete_folder(session, folder))


@app.get(
    "/api/folders/{folder}/messages",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="获取邮件列表",
    response_description="当前文件夹的分页邮件摘要",
)
def messages(
    request: Request,
    folder: str,
    page: int = 1,
    page_size: int | None = Query(default=None, ge=1, le=100),
    refresh: bool = False,
) -> dict[str, object]:
    """返回某个文件夹的分页邮件摘要列表。"""
    session = get_current_session(request)
    effective_page_size = page_size or int(_system_preferences(session.preferences).get("page_size", 30) or 30)
    page_data = list_messages(
        session,
        folder,
        page=max(page, 1),
        page_size=min(max(effective_page_size, 1), 100),
        refresh=refresh,
    )
    return success_response(
        request,
        {
            "folder": page_data.folder,
            "page": page_data.page,
            "page_size": page_data.page_size,
            "total": page_data.total,
            "messages": page_data.messages,
            "cached": page_data.cached,
        },
    )


@app.get(
    "/api/folders/{folder}/messages/search",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="搜索当前文件夹邮件",
    response_description="当前文件夹关键词搜索结果",
)
def search_folder_messages(
    request: Request,
    folder: str,
    q: str = Query(..., min_length=1),
    sender: str | None = Query(default=None, min_length=1),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    has_attachments: bool | None = Query(default=None),
    page: int = 1,
    page_size: int | None = Query(default=None, ge=1, le=100),
    refresh: bool = False,
) -> dict[str, object]:
    """在指定文件夹内执行关键词和条件组合搜索。"""
    session = get_current_session(request)
    normalized_sender = sender.strip() if sender else None
    if date_from and date_to and date_from > date_to:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数错误",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )
    effective_page_size = page_size or int(_system_preferences(session.preferences).get("page_size", 30) or 30)
    page_data = search_messages(
        session,
        folder,
        q,
        page=max(page, 1),
        page_size=min(max(effective_page_size, 1), 100),
        sender=normalized_sender,
        date_from=date_from,
        date_to=date_to,
        has_attachments=has_attachments,
        refresh=refresh,
    )
    return success_response(
        request,
        {
            "folder": page_data.folder,
            "query": q,
            "sender": normalized_sender,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "has_attachments": has_attachments,
            "page": page_data.page,
            "page_size": page_data.page_size,
            "total": page_data.total,
            "messages": page_data.messages,
            "cached": page_data.cached,
        },
    )


@app.get(
    "/api/search",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="搜索当前文件夹邮件",
    response_description="当前文件夹关键词搜索结果",
)
def search_messages_api(
    request: Request,
    folder: str = Query(..., min_length=1),
    q: str = Query(..., min_length=1),
    sender: str | None = Query(default=None, min_length=1),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    has_attachments: bool | None = Query(default=None),
    page: int = 1,
    page_size: int | None = Query(default=None, ge=1, le=100),
    refresh: bool = False,
) -> dict[str, object]:
    """兼容旧查询参数风格的全局搜索入口。"""
    return search_folder_messages(
        request,
        folder,
        q,
        sender=sender,
        date_from=date_from,
        date_to=date_to,
        has_attachments=has_attachments,
        page=page,
        page_size=page_size,
        refresh=refresh,
    )


@app.get(
    "/api/folders/{folder}/messages/{uid}",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="获取邮件详情",
    response_description="邮件头、正文和附件元数据",
)
def message_detail(request: Request, folder: str, uid: str) -> dict[str, object]:
    """返回单封邮件的完整头部、正文和附件元数据。"""
    session = get_current_session(request)
    return success_response(request, get_message_detail(session, folder, uid))


@app.post(
    "/api/folders/{folder}/messages/operations",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="批量操作邮件",
    response_description="邮件批量操作结果",
)
def message_operations(request: Request, folder: str, payload: MessageOperationRequest) -> dict[str, object]:
    """对指定文件夹中的邮件执行批量状态或移动操作。"""
    session = get_current_session(request)
    return success_response(request, operate_messages(session, folder, payload))


@app.post(
    "/api/messages/{folder}/{uid}/read",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="标记邮件已读",
    response_description="标记已读结果",
)
def mark_message_read(request: Request, folder: str, uid: str) -> dict[str, object]:
    """把单封邮件标记为已读。"""
    session = get_current_session(request)
    payload = MessageOperationRequest(action="mark_read", uids=[uid])
    return success_response(request, operate_messages(session, folder, payload))


@app.post(
    "/api/messages/{folder}/{uid}/unread",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="标记邮件未读",
    response_description="标记未读结果",
)
def mark_message_unread(request: Request, folder: str, uid: str) -> dict[str, object]:
    """把单封邮件标记为未读。"""
    session = get_current_session(request)
    payload = MessageOperationRequest(action="mark_unread", uids=[uid])
    return success_response(request, operate_messages(session, folder, payload))


@app.post(
    "/api/messages/move",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="批量移动邮件",
    response_description="批量移动结果",
)
def move_messages(request: Request, payload: BulkMessageRequest) -> dict[str, object]:
    """批量移动多封邮件到目标文件夹。"""
    session = get_current_session(request)
    folder = request.query_params.get("folder") or payload.folder
    move_payload = MessageOperationRequest(
        action="move",
        uids=payload.uids,
        target_folder=payload.target_folder,
    )
    return success_response(request, operate_messages(session, str(folder), move_payload))


@app.post(
    "/api/messages/delete",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="批量删除邮件",
    response_description="批量删除结果",
)
def delete_messages(request: Request, payload: BulkMessageRequest) -> dict[str, object]:
    """批量把多封邮件移入垃圾箱并执行删除标记。"""
    session = get_current_session(request)
    folder = request.query_params.get("folder") or payload.folder
    delete_payload = MessageOperationRequest(action="delete", uids=payload.uids)
    return success_response(request, operate_messages(session, str(folder), delete_payload))


@app.get(
    "/api/folders/{folder}/messages/{uid}/attachments/{attachment_id}",
    tags=["mailbox"],
    summary="下载邮件附件",
    response_description="附件二进制内容",
)
def download_attachment(request: Request, folder: str, uid: str, attachment_id: str) -> Response:
    """下载指定邮件中的某个附件二进制内容。"""
    session = get_current_session(request)
    validate_attachment_id(attachment_id)
    attachment = get_message_attachment(session, folder, uid, attachment_id)
    filename = str(attachment["filename"])
    return Response(
        content=attachment["content"],
        media_type=str(attachment["content_type"]),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Attachment-Id": attachment_id,
        },
    )


@app.get(
    "/api/folders/{folder}/messages/{uid}/attachments/{attachment_id}/preview/status",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="查询附件预览状态",
    response_description="附件预览的异步生成状态",
)
def preview_attachment_status(request: Request, folder: str, uid: str, attachment_id: str) -> dict[str, object]:
    """返回附件预览当前是否已准备完成，并在需要时触发后台生成。"""
    session = get_current_session(request)
    validate_attachment_id(attachment_id)
    return success_response(request, get_message_attachment_preview_status(session, folder, uid, attachment_id))


@app.get(
    "/api/folders/{folder}/messages/{uid}/attachments/{attachment_id}/preview",
    tags=["mailbox"],
    summary="预览邮件附件",
    response_description="可直接在浏览器内展示的附件预览内容",
)
def preview_attachment(request: Request, folder: str, uid: str, attachment_id: str) -> Response:
    """返回附件预览内容，支持图片、PDF、文本和 docx 文本预览。"""
    session = get_current_session(request)
    validate_attachment_id(attachment_id)
    preview = get_message_attachment_preview(session, folder, uid, attachment_id)
    filename = str(preview["filename"])
    response = Response(
        content=preview["content"],
        media_type=str(preview["content_type"]),
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Attachment-Id": attachment_id,
        },
    )
    return add_security_headers(response, allow_same_origin_frame=True)


@app.get(
    "/api/folders/{folder}/messages/{uid}/attachments/{attachment_id}/preview-thumbnail",
    tags=["mailbox"],
    summary="读取附件缩略图",
    response_description="附件首屏缩略图内容",
)
def preview_attachment_thumbnail(request: Request, folder: str, uid: str, attachment_id: str) -> Response:
    """返回附件缩略图，供列表卡片优先展示。"""
    session = get_current_session(request)
    validate_attachment_id(attachment_id)
    preview = get_message_attachment_preview_thumbnail(session, folder, uid, attachment_id)
    filename = str(preview["filename"])
    response = Response(
        content=preview["content"],
        media_type=str(preview["content_type"]),
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Attachment-Id": attachment_id,
        },
    )
    return add_security_headers(response, allow_same_origin_frame=True)


@app.post(
    "/api/attachments",
    tags=["compose"],
    response_model=ApiResponse,
    summary="上传临时附件",
    response_description="临时附件元数据",
)
async def upload_attachments(request: Request, files: list[UploadFile] = File(...)) -> dict[str, object]:
    """上传写信阶段的临时附件。"""
    session = get_current_session(request)
    attachments = await upload_temp_attachments(session, files)
    return success_response(request, {"attachments": attachments})


@app.post(
    "/api/attachments/chunks",
    tags=["compose"],
    response_model=ApiResponse,
    summary="分块上传临时附件",
    response_description="分块上传进度和附件元数据",
)
async def upload_attachment_chunk(
    request: Request,
    attachment_id: str = Form(...),
    chunk_index: int = Form(..., ge=0),
    total_chunks: int = Form(..., ge=1),
    file_size_bytes: int = Form(..., ge=0),
    filename: str = Form(...),
    content_type: str = Form("application/octet-stream"),
    chunk: UploadFile = File(...),
) -> dict[str, object]:
    """接收大附件分块并在全部完成后组装为临时附件。"""
    session = get_current_session(request)
    attachment = await store_temp_attachment_chunk(
        session,
        attachment_id=attachment_id,
        filename=filename,
        content_type=content_type or "application/octet-stream",
        file_size_bytes=file_size_bytes,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        chunk=chunk,
    )
    return success_response(request, {"attachment": attachment})


@app.post(
    "/api/messages/send",
    tags=["compose"],
    response_model=ApiResponse,
    summary="发送邮件",
    response_description="SMTP 发送和已发送归档结果",
)
def send_message(request: Request, payload: SendMailRequest) -> dict[str, object]:
    """通过 SMTP 发送邮件，并记录发送审计结果。"""
    session = get_current_session(request)
    audit_metadata = {
        "recipient_count": len(payload.to) + len(payload.cc) + len(payload.bcc),
        "attachment_count": len(payload.attachment_ids),
        "has_draft": bool(payload.draft_id),
    }
    try:
        result = send_mail(session, payload)
    except AppError as exc:
        record_audit_event(
            request,
            "compose.send_mail",
            success=False,
            metadata={**audit_metadata, "error_code": exc.code},
        )
        raise
    record_audit_event(
        request,
        "compose.send_mail",
        success=True,
        metadata=audit_metadata,
    )
    return success_response(request, result)


@app.get(
    "/api/contacts",
    tags=["compose"],
    response_model=ApiResponse,
    summary="获取最近联系人",
    response_description="联系人补全候选列表",
)
def contacts(
    request: Request,
    query: str = Query(default="", max_length=255),
    limit: int = Query(default=10, ge=1, le=10),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    group_name: str | None = Query(default=None, max_length=100),
    tag: str | None = Query(default=None, max_length=100),
) -> dict[str, object]:
    """提供联系人自动补全或分页通讯录查询。"""
    session = get_current_session(request)
    if page_size is not None or page > 1 or group_name is not None or tag is not None:
        effective_page_size = page_size or 20
        return success_response(
            request,
            search_contacts(
                session,
                query=query or None,
                page=page,
                page_size=effective_page_size,
                group_name=group_name,
                tag=tag,
            ),
        )
    return success_response(
        request,
        {
            "query": query,
            "contacts": search_contacts_for_autocomplete(session, query=query, limit=limit).contacts,
        },
    )


@app.post(
    "/api/contacts",
    tags=["compose"],
    response_model=ApiResponse,
    summary="新增联系人",
    response_description="新增后的联系人",
)
def create_contact_api(request: Request, payload: ContactCreateRequest) -> dict[str, object]:
    """新增一个手工维护的联系人。"""
    session = get_current_session(request)
    return success_response(request, create_contact(session, payload))


@app.get(
    "/api/contacts/blacklist",
    tags=["compose"],
    response_model=ApiResponse,
    summary="获取黑名单联系人",
    response_description="黑名单邮箱列表",
)
def contacts_blacklist(request: Request) -> dict[str, object]:
    """返回当前账号的黑名单联系人邮箱列表。"""
    session = get_current_session(request)
    return success_response(request, {"contacts": list_blacklisted_contacts(session)})


@app.get(
    "/api/contacts/{contact_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="获取联系人详情",
    response_description="联系人详情",
)
def get_contact_api(request: Request, contact_id: str) -> dict[str, object]:
    """读取单个联系人的完整详情。"""
    session = get_current_session(request)
    return success_response(request, get_contact(session, contact_id))


@app.patch(
    "/api/contacts/{contact_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="更新联系人",
    response_description="更新后的联系人",
)
def update_contact_api(request: Request, contact_id: str, payload: ContactUpdateRequest) -> dict[str, object]:
    """更新指定联系人字段。"""
    session = get_current_session(request)
    return success_response(request, update_contact(session, contact_id, payload))


@app.delete(
    "/api/contacts/{contact_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="删除联系人",
    response_description="删除结果",
)
def delete_contact_api(request: Request, contact_id: str) -> dict[str, object]:
    """删除指定联系人。"""
    session = get_current_session(request)
    return success_response(request, delete_contact(session, contact_id))


@app.post(
    "/api/drafts",
    tags=["compose"],
    response_model=ApiResponse,
    summary="保存草稿",
    response_description="草稿保存结果",
)
def save_mail_draft(request: Request, payload: DraftPayload) -> dict[str, object]:
    """创建一份新的邮件草稿。"""
    session = get_current_session(request)
    return success_response(request, save_draft(session, payload))


@app.patch(
    "/api/drafts/{draft_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="更新草稿",
    response_description="草稿更新结果",
)
def update_mail_draft(request: Request, draft_id: str, payload: DraftPayload) -> dict[str, object]:
    """更新已有草稿的内容和附件引用。"""
    session = get_current_session(request)
    return success_response(request, update_draft(session, draft_id, payload))


@app.get(
    "/api/drafts/{draft_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="获取草稿",
    response_description="草稿内容",
)
def fetch_mail_draft(request: Request, draft_id: str) -> dict[str, object]:
    """读取指定草稿的完整内容。"""
    session = get_current_session(request)
    return success_response(request, get_draft(session, draft_id))


@app.delete(
    "/api/drafts/{draft_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="删除草稿",
    response_description="草稿删除结果",
)
def remove_mail_draft(request: Request, draft_id: str) -> dict[str, object]:
    """删除指定草稿及其临时附件关联。"""
    session = get_current_session(request)
    return success_response(request, delete_draft(session, draft_id))
