"""请求中间件与请求标识注入逻辑。"""

from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from fastapi import Request, Response

from app.observability import record_request_log

REQUEST_ID_HEADER = "X-Request-ID"


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """为每个请求补充 request_id，并记录请求耗时日志。"""
    request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid4().hex}"
    request.state.request_id = request_id
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (perf_counter() - started_at) * 1000
        record_request_log(request, 500, duration_ms)
        raise
    duration_ms = (perf_counter() - started_at) * 1000
    response.headers[REQUEST_ID_HEADER] = request_id
    record_request_log(request, response.status_code, duration_ms)
    return response
