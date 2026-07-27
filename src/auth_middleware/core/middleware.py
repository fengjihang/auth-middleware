"""ASGI 中间件：请求级结构化日志 + Prometheus 指标采集。

挂在 app 上，每个请求自动记录：
1. structlog：request_id、method、path、status、duration、user_id（若有）
2. prometheus：counter（method+path+status）、histogram（duration）
"""

import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from auth_middleware.core.metrics import ACTIVE_USERS, REQUEST_COUNT, REQUEST_DURATION
from auth_middleware.core.logging import get_logger

logger = get_logger()


class ObservabilityMiddleware:
    """挂在 FastAPI app 上的 ASGI 中间件。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # /metrics 是 Prometheus 子应用（make_asgi_app 挂载）。它本身已是
        # 可观测数据的出口，若再被本中间件计数/记录，会造成指标自污染与无意义的
        # 日志刷屏。故直接透传，不参与计数、日志与 X-Request-ID 注入。
        # 注意：Starlette 的 Mount 会把 /metrics 307 重定向到 /metrics/，
        # 两个路径都要跳过，否则重定向后的请求仍会被计数。
        if scope["path"] == "/metrics" or scope["path"] == "/metrics/":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())[:8]
        method = scope["method"]
        path = scope["path"]
        start = time.monotonic()

        # 真实状态码要从 http.response.start 消息里取——Starlette 的 Response
        # 不会把 status 写回 scope["status_code"]，若用 scope.get("status_code")
        # 永远拿到默认值 200，会导致错误分级日志与 REQUEST_COUNT 的 status 标签
        # 永远失真（4xx/5xx 永不记录）。用可变容器在 send_wrapper 中捕获。
        status_holder: dict[str, int] = {"code": 200}

        # 注入 request_id 到响应头，并从响应起始消息捕获真实状态码
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                headers["X-Request-ID"] = request_id
                status_holder["code"] = int(message.get("status", 200))
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration = time.monotonic() - start
        status = status_holder["code"]
        user_id = scope.get("user_id", None)

        # 结构化日志
        log = logger.bind(
            request_id=request_id,
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration * 1000, 1),
        )
        if user_id:
            log = log.bind(user_id=user_id)

        if status >= 500:
            log.error("request failed")
        elif status >= 400:
            log.warning("request warning")
        else:
            log.info("request ok")

        # Prometheus 指标
        norm_path = _normalize_path(path)
        REQUEST_COUNT.labels(method=method, path=norm_path, status=status).inc()
        REQUEST_DURATION.labels(method=method, path=norm_path).observe(duration)


def _normalize_path(path: str) -> str:
    """把 /api/v1/auth/register 归一化为 /api/v1/auth/register，保留原样。
    
    若后续有带 ID 的路由（如 /api/v1/users/123），可在此隐去 ID。当前项目无此类路由。
    """
    return path
