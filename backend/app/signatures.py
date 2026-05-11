from __future__ import annotations

from uuid import UUID as UUIDType

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.auth import get_current_session
from app.db import get_session_factory
from app.errors import AppError
from app.models import MailAccount, MailSignature
from app.responses import success_response
from app.schemas import ApiResponse, SignatureCreateRequest, SignatureResponse, SignatureUpdateRequest


router = APIRouter(prefix="/api/signatures", tags=["signatures"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _get_account(db_session, email: str) -> MailAccount | None:
    normalized_email = _normalize_email(email)
    return db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))


def _get_signature(db_session, account_id: UUIDType, signature_id: UUIDType) -> MailSignature:
    signature = db_session.get(MailSignature, signature_id)
    if signature is None or signature.account_id != account_id:
        raise AppError("SIGNATURE_NOT_FOUND", "签名不存在", http_status=status.HTTP_404_NOT_FOUND)
    return signature


def _ordered_signatures(db_session, account_id: UUIDType) -> list[MailSignature]:
    return list(
        db_session.scalars(
            select(MailSignature)
            .where(MailSignature.account_id == account_id)
            .order_by(
                MailSignature.is_default.desc(),
                MailSignature.created_at.asc(),
                MailSignature.name.asc(),
            )
        ).all()
    )


def _signature_response(signature: MailSignature) -> SignatureResponse:
    return SignatureResponse(
        id=str(signature.id),
        name=signature.name,
        content=signature.content,
        is_default=bool(signature.is_default),
        created_at=signature.created_at.isoformat() if signature.created_at else "",
        updated_at=signature.updated_at.isoformat() if signature.updated_at else "",
    )


def _ensure_single_default(db_session, account_id: UUIDType) -> MailSignature | None:
    signatures = _ordered_signatures(db_session, account_id)
    if not signatures:
        return None

    default_signature = next((item for item in signatures if item.is_default), None)
    if default_signature is None:
        default_signature = signatures[0]

    for signature in signatures:
        signature.is_default = False
    db_session.flush()
    default_signature.is_default = True
    db_session.flush()
    return default_signature


def _set_only_default(db_session, account_id: UUIDType, default_signature: MailSignature) -> None:
    for signature in _ordered_signatures(db_session, account_id):
        signature.is_default = False
    db_session.flush()
    default_signature.is_default = True
    db_session.flush()


def _write_operation(callback):
    session_factory = get_session_factory()
    with session_factory() as db_session:
        result = callback(db_session)
        db_session.commit()
        return result


@router.get("", response_model=ApiResponse, summary="获取签名列表", response_description="当前账号的签名列表")
def list_signatures(request: Request) -> dict[str, object]:
    session = get_current_session(request)

    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            return {"signatures": []}
        signatures = _ordered_signatures(db_session, account.id)
        return {"signatures": [_signature_response(signature).model_dump() for signature in signatures]}

    return success_response(request, _write_operation(write))


@router.get("/default", response_model=ApiResponse, summary="获取默认签名", response_description="当前账号的默认签名")
def get_default_signature(request: Request) -> dict[str, object]:
    session = get_current_session(request)

    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            return {"signature": None}
        signature = next((item for item in _ordered_signatures(db_session, account.id) if item.is_default), None)
        return {"signature": _signature_response(signature).model_dump() if signature is not None else None}

    return success_response(request, _write_operation(write))


@router.post("", response_model=ApiResponse, summary="新增签名", response_description="新增后的签名")
def create_signature(request: Request, payload: SignatureCreateRequest) -> dict[str, object]:
    session = get_current_session(request)

    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)

        signature = MailSignature(
            account_id=account.id,
            name=payload.name,
            content=payload.content,
            is_default=payload.is_default,
        )
        db_session.add(signature)
        db_session.flush()

        if payload.is_default:
            _ensure_single_default(db_session, account.id)
        else:
            default_signature = next((item for item in _ordered_signatures(db_session, account.id) if item.is_default), None)
            if default_signature is None:
                signature.is_default = True

        db_session.flush()
        db_session.refresh(signature)
        return {"signature": _signature_response(signature).model_dump()}

    return success_response(request, _write_operation(write))


@router.patch("/{signature_id}", response_model=ApiResponse, summary="编辑签名", response_description="编辑后的签名")
def update_signature(request: Request, signature_id: UUIDType, payload: SignatureUpdateRequest) -> dict[str, object]:
    session = get_current_session(request)

    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)

        signature = _get_signature(db_session, account.id, signature_id)
        if payload.name is None and payload.content is None:
            raise AppError("SIGNATURE_NO_CHANGES", "未提供需要更新的签名内容", http_status=status.HTTP_400_BAD_REQUEST)

        if payload.name is not None:
            signature.name = payload.name
        if payload.content is not None:
            signature.content = payload.content
        db_session.flush()
        db_session.refresh(signature)
        return {"signature": _signature_response(signature).model_dump()}

    return success_response(request, _write_operation(write))


@router.delete("/{signature_id}", response_model=ApiResponse, summary="删除签名", response_description="删除结果")
def delete_signature(request: Request, signature_id: UUIDType) -> dict[str, object]:
    session = get_current_session(request)

    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)

        signature = _get_signature(db_session, account.id, signature_id)
        was_default = bool(signature.is_default)
        signature_id_text = str(signature.id)
        db_session.delete(signature)
        db_session.flush()
        if was_default:
            _ensure_single_default(db_session, account.id)
        return {"deleted": True, "signature_id": signature_id_text}

    return success_response(request, _write_operation(write))


@router.post("/{signature_id}/default", response_model=ApiResponse, summary="设为默认签名", response_description="更新后的默认签名")
def set_default_signature(request: Request, signature_id: UUIDType) -> dict[str, object]:
    session = get_current_session(request)

    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)

        signature = _get_signature(db_session, account.id, signature_id)
        _set_only_default(db_session, account.id, signature)
        db_session.refresh(signature)
        return {"signature": _signature_response(signature).model_dump()}

    return success_response(request, _write_operation(write))
