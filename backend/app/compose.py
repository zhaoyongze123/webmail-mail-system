from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from fastapi import status
from pydantic import BaseModel, EmailStr, Field, model_validator

from app import mail_adapters
from app.attachments import load_temp_attachment
from app.auth import AuthSession
from app.contacts import record_recent_contacts
from app.config import get_settings
from app.errors import AppError
from app.mail_adapters import ImapSettings, MailAdapterError, SmtpSettings


class SendMailRequest(BaseModel):
    to: list[EmailStr] = Field(default_factory=list)
    cc: list[EmailStr] = Field(default_factory=list)
    bcc: list[EmailStr] = Field(default_factory=list)
    subject: str = ""
    html_body: str | None = None
    text_body: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    draft_id: str | None = None

    @model_validator(mode="after")
    def validate_recipients(self) -> "SendMailRequest":
        recipients = [str(item).lower() for item in [*self.to, *self.cc, *self.bcc]]
        if not recipients:
            raise ValueError("至少需要一个收件人")
        if len(set(recipients)) != len(recipients):
            raise ValueError("收件人不能重复")
        return self


def _smtp_settings(session: AuthSession) -> SmtpSettings:
    smtp_config = session.smtp
    settings = get_settings()
    return SmtpSettings(
        host=str(smtp_config.get("host") or settings.mail_smtp_host),
        port=int(smtp_config.get("port") or settings.mail_smtp_port),
        username=session.email,
        password=session.password,
        use_ssl=bool(smtp_config.get("ssl", settings.mail_smtp_ssl)),
        starttls=bool(smtp_config.get("starttls", settings.mail_smtp_starttls)),
        timeout=15,
    )


def _imap_settings(session: AuthSession) -> ImapSettings:
    imap_config = session.imap
    settings = get_settings()
    return ImapSettings(
        host=str(imap_config.get("host") or settings.mail_imap_host),
        port=int(imap_config.get("port") or settings.mail_imap_port),
        username=session.email,
        password=session.password,
        use_ssl=bool(imap_config.get("ssl", settings.mail_imap_ssl)),
        starttls=bool(imap_config.get("starttls", settings.mail_imap_starttls)),
        timeout=15,
    )


def _build_message(session: AuthSession, payload: SendMailRequest) -> EmailMessage:
    message = EmailMessage()
    message["Message-ID"] = make_msgid(domain=session.email.split("@")[-1])
    message["Date"] = formatdate(localtime=True)
    message["From"] = session.email
    message["To"] = ", ".join(str(item) for item in payload.to)
    if payload.cc:
        message["Cc"] = ", ".join(str(item) for item in payload.cc)
    message["Subject"] = payload.subject

    text_body = payload.text_body or ""
    html_body = payload.html_body
    if html_body:
        message.set_content(text_body or " ")
        message.add_alternative(html_body, subtype="html")
    else:
        message.set_content(text_body)

    for attachment_id in payload.attachment_ids:
        attachment = load_temp_attachment(session, attachment_id)
        maintype, _, subtype = str(attachment["content_type"]).partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            attachment["content"],
            maintype=maintype,
            subtype=subtype,
            filename=str(attachment["filename"]),
        )
    return message


def send_mail(session: AuthSession, payload: SendMailRequest) -> dict[str, Any]:
    message = _build_message(session, payload)
    smtp = mail_adapters.SmtpAdapter(_smtp_settings(session))
    imap = mail_adapters.ImapAdapter(_imap_settings(session))
    try:
        smtp.connect().login().send_message(message)
    except MailAdapterError as exc:
        raise AppError(
            "MAIL_SMTP_SEND_FAILED",
            "SMTP 发送邮件失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        smtp.quit()

    try:
        imap.connect().login().append_message(".Sent", message)
    except MailAdapterError as exc:
        raise AppError(
            "MAIL_IMAP_APPEND_FAILED",
            "已发送归档失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        imap.logout()

    recipients = [str(item) for item in [*payload.to, *payload.cc, *payload.bcc]]
    record_recent_contacts(session, recipients)

    if payload.draft_id:
        from app.drafts import delete_draft

        delete_draft(session, payload.draft_id)
    return {"message_id": message["Message-ID"], "sent": True, "archived_folder": ".Sent"}
