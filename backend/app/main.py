from urllib.parse import quote

from fastapi import FastAPI, File, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from app.attachments import upload_temp_attachments
from app.auth import LoginRequest, clear_session_cookie, get_current_session, login_user, logout_user, set_session_cookie
from app.compose import SendMailRequest, send_mail
from app.contacts import search_recent_contacts
from app.config import get_settings
from app.drafts import DraftPayload, delete_draft, get_draft, save_draft
from app.errors import AppError
from app.cache import UserPreferenceStore
from app.mailbox import (
    MessageOperationRequest,
    get_message_attachment,
    get_message_detail,
    list_folders,
    list_messages,
    operate_messages,
    search_messages,
)
from app.middleware import request_id_middleware
from app.observability import metrics_store, record_audit_event
from app.responses import app_error_response, error_response, success_response
from app.schemas import ApiResponse
from app.security import add_security_headers, log_sanitized_event, validate_attachment_id, validate_csrf_request


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


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
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
        return add_security_headers(app_error_response(request, exc))
    response = await call_next(request)
    return add_security_headers(response)


class BulkMessageRequest(BaseModel):
    folder: str = "INBOX"
    uids: list[str] = Field(default_factory=list)
    target_folder: str | None = None


class SettingsUpdateRequest(BaseModel):
    page_size: int | None = Field(default=None, ge=1, le=100)
    mark_read_on_open: bool | None = None


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    return app_error_response(request, exc)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
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


@app.get("/api/metrics", tags=["health"], response_model=ApiResponse)
def metrics(request: Request) -> dict[str, object]:
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
    session_id, user_data, csrf_token = login_user(request, payload)
    set_session_cookie(response, session_id, csrf_token)
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


@app.get(
    "/api/settings",
    tags=["settings"],
    response_model=ApiResponse,
    summary="获取当前账号设置",
    response_description="当前账号和设置偏好",
)
def get_settings_api(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    preferences = UserPreferenceStore().get(session.email)
    return success_response(
        request,
        {
            "account": {"email": session.email},
            "preferences": preferences,
        },
    )


@app.put(
    "/api/settings",
    tags=["settings"],
    response_model=ApiResponse,
    summary="更新当前账号设置",
    response_description="更新后的账号和设置偏好",
)
def update_settings_api(request: Request, payload: SettingsUpdateRequest) -> dict[str, object]:
    session = get_current_session(request)
    preferences = UserPreferenceStore().update(
        session.email,
        {
            "page_size": payload.page_size,
            "mark_read_on_open": payload.mark_read_on_open,
        },
    )
    return success_response(
        request,
        {
            "account": {"email": session.email},
            "preferences": preferences,
        },
    )


@app.get(
    "/api/folders",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="获取邮箱文件夹",
    response_description="系统文件夹与未读数量",
)
def folders(request: Request) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, {"folders": list_folders(session)})


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
    session = get_current_session(request)
    effective_page_size = page_size or int(session.preferences.get("page_size", 30) or 30)
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
    page: int = 1,
    page_size: int | None = Query(default=None, ge=1, le=100),
    refresh: bool = False,
) -> dict[str, object]:
    session = get_current_session(request)
    effective_page_size = page_size or int(session.preferences.get("page_size", 30) or 30)
    page_data = search_messages(
        session,
        folder,
        q,
        page=max(page, 1),
        page_size=min(max(effective_page_size, 1), 100),
        refresh=refresh,
    )
    return success_response(
        request,
        {
            "folder": page_data.folder,
            "query": q,
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
    page: int = 1,
    page_size: int | None = Query(default=None, ge=1, le=100),
    refresh: bool = False,
) -> dict[str, object]:
    return search_folder_messages(request, folder, q, page, page_size, refresh)


@app.get(
    "/api/folders/{folder}/messages/{uid}",
    tags=["mailbox"],
    response_model=ApiResponse,
    summary="获取邮件详情",
    response_description="邮件头、正文和附件元数据",
)
def message_detail(request: Request, folder: str, uid: str) -> dict[str, object]:
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


@app.post(
    "/api/attachments",
    tags=["compose"],
    response_model=ApiResponse,
    summary="上传临时附件",
    response_description="临时附件元数据",
)
async def upload_attachments(request: Request, files: list[UploadFile] = File(...)) -> dict[str, object]:
    session = get_current_session(request)
    attachments = await upload_temp_attachments(session, files)
    return success_response(request, {"attachments": attachments})


@app.post(
    "/api/messages/send",
    tags=["compose"],
    response_model=ApiResponse,
    summary="发送邮件",
    response_description="SMTP 发送和已发送归档结果",
)
def send_message(request: Request, payload: SendMailRequest) -> dict[str, object]:
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
) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(
        request,
        {
            "query": query,
            "contacts": search_recent_contacts(session, query=query, limit=limit),
        },
    )


@app.post(
    "/api/drafts",
    tags=["compose"],
    response_model=ApiResponse,
    summary="保存草稿",
    response_description="草稿保存结果",
)
def save_mail_draft(request: Request, payload: DraftPayload) -> dict[str, object]:
    session = get_current_session(request)
    return success_response(request, save_draft(session, payload))


@app.get(
    "/api/drafts/{draft_id}",
    tags=["compose"],
    response_model=ApiResponse,
    summary="获取草稿",
    response_description="草稿内容",
)
def fetch_mail_draft(request: Request, draft_id: str) -> dict[str, object]:
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
    session = get_current_session(request)
    return success_response(request, delete_draft(session, draft_id))
