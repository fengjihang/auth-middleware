"""应用入口：FastAPI 实例、lifespan、路由装配。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from auth_middleware.api.routes.audit import router as audit_router
from auth_middleware.api.routes.auth import router as auth_router
from auth_middleware.api.routes.rbac import router as rbac_router
from auth_middleware.core.bootstrap import seed_admin
from auth_middleware.core.config import settings
from auth_middleware.core.database import init_db
from auth_middleware.core.logging import configure_logging
from auth_middleware.core.middleware import ObservabilityMiddleware
from auth_middleware.core.redis import redis_lifespan

# 确保 ORM 模型在 create_all 前被注册到 Base.metadata
import auth_middleware.models


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化结构化日志
    configure_logging()
    # Redis 连接池随应用启停（不可用时自动降级，不影响启动）
    async with redis_lifespan():
        # 启动时建表（开发环境用；生产用 Alembic 迁移，见 Phase 3）
        await init_db()
        # 演示用：确保至少存在一个 admin 账号
        await seed_admin()
        yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---- Phase 6：ASGI 中间件链 ----
# 可观测性中间件：structlog + Prometheus 指标 + request_id 注入
app.add_middleware(ObservabilityMiddleware)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """健康检查：负载均衡/容器探活会调用它。"""
    return {"status": "ok", "service": settings.app_name}


# ---- Phase 6：Prometheus metrics 端点 ----
# 用 Starlette 的 ASGI app 挂载，保证 /metrics 不经过中间件计自己
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# 挂载认证路由：/api/v1/auth/register | /login | /refresh | /me
app.include_router(auth_router, prefix="/api/v1")
# 挂载 RBAC 路由：/api/v1/rbac/profile | /rbac/admin/users ...
app.include_router(rbac_router, prefix="/api/v1")
# 挂载审计日志查询路由：/api/v1/admin/audit-logs
app.include_router(audit_router, prefix="/api/v1")
