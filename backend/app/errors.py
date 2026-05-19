"""应用层错误类型定义。"""

from fastapi import status


class AppError(Exception):
    """带业务错误码和 HTTP 状态码的应用异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        super().__init__(message)
