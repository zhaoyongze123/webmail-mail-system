"""写信与发信流程的邮件组装逻辑。

这个模块负责把前端提交的写信表单整理成标准 MIME 邮件，处理附件挂载、
SMTP 发送以及发件后归档到已发送文件夹。
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from fastapi import status
from pydantic import BaseModel, EmailStr, Field, model_validator

from app import mail_adapters
from app.attachments import load_temp_attachment
from app.auth import AuthSession
from app.config import get_settings
from app.errors import AppError
from app.mail_adapters import ImapSettings, MailAdapterError, SmtpSettings
from app.mailbox import _folder_name_from_list_line, _system_folder_map


class SendMailRequest(BaseModel):
    """写信请求体，包含收件人、正文和附件引用。"""
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
        """确保收件人非空且不重复。"""
        recipients = [str(item).lower() for item in [*self.to, *self.cc, *self.bcc]]
        if not recipients:
            raise ValueError("至少需要一个收件人")
        if len(set(recipients)) != len(recipients):
            raise ValueError("收件人不能重复")
        return self


def _smtp_settings(session: AuthSession) -> SmtpSettings:
    """从当前会话和全局配置构造 SMTP 连接参数。"""
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
    """从当前会话和全局配置构造 IMAP 连接参数。"""
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
    """把写信请求组装为可发送的 MIME 邮件对象。"""
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
        # 附件先从临时缓存读取，再挂载到最终邮件中。
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
    """发送邮件并尝试将已发邮件归档到服务器端已发送文件夹。"""
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
        imap.connect().login()
        folder_map = _system_folder_map([_folder_name_from_list_line(line) for line in imap.list_folders()])
        sent_folder = folder_map.get(".Sent", ".Sent")
        imap.append_message(sent_folder, message)
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
    from app import contacts as contacts_module

    contacts_module.record_recent_contacts(session, recipients)

    if payload.draft_id:
        from app.drafts import delete_draft

        delete_draft(session, payload.draft_id)
    return {"message_id": message["Message-ID"], "sent": True, "archived_folder": sent_folder}
