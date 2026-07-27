"""端到端集成测试套件：跨模块验证「认证 → 授权 → 审计 → 可观测 → 限流」。

直接通过 httpx.ASGITransport 驱动 ASGI app（不占用端口），每个用例使用独立的
内存 SQLite（async + StaticPool）作为测试库，通过 dependency_overrides 注入，
多个用例之间完全隔离、可重复运行，且不污染开发库(auth_dev.db)。

覆盖范围：
1. 启动与应用可用（/health）
2. 认证闭环：注册 → 登录 → /me → refresh → 错误/过期/类型错误令牌被拒
3. 授权(RBAC)：管理员可访问管理路由；普通用户越权被拒(403)；casbin 策略生效
4. 审计：登录/越权等操作产生审计记录，且 /api/v1/admin/audit-logs 可分页查到
5. 可观测：响应头含 X-Request-ID；/metrics 返回 Prometheus 文本(含请求计数)；
   中间件对 4xx/5xx 记录分级日志
6. 限流：compute_token_bucket 纯函数正确性（重点验证参数顺序）；HTTP 层 429
   仅在 Redis 可达时验证，否则 skip（不污染套件）

说明：本套件配套的源码修复见 src/ 下改动（compute_token_bucket 参数顺序、
refresh 回查用户、ObservabilityMiddleware 跳过 /metrics、CORS 收紧、审计登录
事件、AUDIT_FAILURES 指标接线等）。
"""

import io

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from auth_middleware.core.config import settings
from auth_middleware.core.database import Base, get_db
from auth_middleware.core.rate_limit import compute_token_bucket
from auth_middleware.core.security import create_access_token
from auth_middleware.main import app
from auth_middleware.models.audit_log import AuditLog
from auth_middleware.models.user import User
from auth_middleware.repositories.audit_repository import AuditRepository
from auth_middleware.repositories.user_repository import UserRepository


# --------------------------------------------------------------------------- #
# 测试库（独立内存 SQLite）与依赖注入
# --------------------------------------------------------------------------- #
TEST_ENGINE = create_async_engine(
    "sqlite+aiosqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
TestSession = async_sessionmaker(TEST_ENGINE, expire_on_commit=False)


@pytest.fixture
async def client():
    import auth_middleware.models  # 确保 users / audit_logs 表注册到 metadata

    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# --------------------------------------------------------------------------- #
# 测试辅助
# --------------------------------------------------------------------------- #
async def _register(client, email, password="Secret123"):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def _login(client, email, password="Secret123"):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def _promote_to_admin(email):
    """直接把某用户提升为 admin（模拟种子管理员/授权动作）。"""
    async with TestSession() as session:
        user = await UserRepository(session).get_by_email(email)
        assert user is not None
        user.role = "admin"
        await session.commit()


async def _deactivate(email):
    """停用某用户，用于验证 refresh token 失效。"""
    async with TestSession() as session:
        user = await UserRepository(session).get_by_email(email)
        user.is_active = False
        await session.commit()


def _redis_available() -> bool:
    """Redis 是否可达（用于决定是否验证 HTTP 层 429）。"""
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        return bool(client.ping())
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 1. 启动 / 健康检查
# --------------------------------------------------------------------------- #
async def test_app_is_runnable_and_health_ok(client):
    # app 在导入时即完成路由装配、中间件挂载；这里验证可正常响应健康检查
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "service" in body


# --------------------------------------------------------------------------- #
# 2. 认证闭环
# --------------------------------------------------------------------------- #
async def test_full_auth_loop_register_login_me_refresh(client):
    # 注册
    r = await _register(client, "alice@example.com")
    assert r.status_code == 201
    assert "password" not in r.json()  # 响应绝不回传口令

    # 登录拿令牌对
    login = await _login(client, "alice@example.com")
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]

    # 无 token 访问受保护接口 -> 401
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    # 用 access 访问 /me -> 200，身份正确
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"

    # refresh 换发新令牌对，新 access 仍可用
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 200
    new_access = refresh.json()["access_token"]
    me2 = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert me2.status_code == 200
    assert me2.json()["email"] == "alice@example.com"


async def test_expired_access_token_rejected(client):
    await _register(client, "bob@example.com")
    expired = create_access_token("1", ttl=-10)  # 已过期
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert r.status_code == 401


async def test_access_token_cannot_be_used_as_refresh(client):
    await _register(client, "carol@example.com")
    login = await _login(client, "carol@example.com")
    access = login.json()["access_token"]
    # 用 access token 去刷新 -> 类型不匹配，应 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


async def test_refresh_token_rejected_for_inactive_user(client):
    """安全回归：被停用用户的 refresh token 必须立即失效（F2 修复点）。"""
    await _register(client, "dave@example.com")
    login = await _login(client, "dave@example.com")
    refresh_token = login.json()["refresh_token"]
    # 停用该用户
    await _deactivate("dave@example.com")
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert r.status_code == 401
    assert "inactive" in r.json()["detail"].lower() or "revoked" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# 3. 授权 (RBAC)
# --------------------------------------------------------------------------- #
async def test_admin_can_access_admin_endpoint_but_user_cannot(client):
    # 普通用户访问管理路由 -> 403
    await _register(client, "user1@example.com")
    user_token = (await _login(client, "user1@example.com")).json()["access_token"]
    denied = await client.get(
        "/api/v1/rbac/admin/users",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert denied.status_code == 403

    # 管理员访问管理路由 -> 200
    await _register(client, "admin1@example.com")
    await _promote_to_admin("admin1@example.com")
    admin_token = (await _login(client, "admin1@example.com")).json()["access_token"]
    ok = await client.get(
        "/api/v1/rbac/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ok.status_code == 200
    assert any(u["email"] == "admin1@example.com" for u in ok.json())


async def test_casbin_policy_effectively_blocks_and_allows(client):
    """casbin 策略真正生效：admin 通配、user 仅 profile。"""
    from auth_middleware.core.casbin import enforce as casbin_enforce

    # user 角色：profile 读/写放行，users 读拒绝
    assert casbin_enforce("user", "profile", "read") is True
    assert casbin_enforce("user", "profile", "write") is True
    assert casbin_enforce("user", "users", "read") is False
    # admin 通配所有
    assert casbin_enforce("admin", "users", "read") is True
    assert casbin_enforce("admin", "audit", "read") is True


# --------------------------------------------------------------------------- #
# 4. 审计
# --------------------------------------------------------------------------- #
async def test_login_is_audited_and_queryable_by_admin(client):
    await _register(client, "audituser@example.com")
    await _login(client, "audituser@example.com")  # 成功登录 -> 审计 allowed=True

    # 升为 admin 并查审计日志
    await _promote_to_admin("audituser@example.com")
    admin_token = (await _login(client, "audituser@example.com")).json()["access_token"]
    r = await client.get(
        "/api/v1/admin/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    assert "items" in body
    # 能查到刚发生的登录审计（action=auth:login, allowed=True）
    login_logs = [i for i in body["items"] if i["action"] == "auth:login"]
    assert login_logs, "登录必须产生审计日志"
    assert login_logs[0]["allowed"] is True
    assert login_logs[0]["user_email"] == "audituser@example.com"


async def test_audit_logs_pagination_and_filter(client):
    await _register(client, "pager@example.com")
    await _promote_to_admin("pager@example.com")
    admin_token = (await _login(client, "pager@example.com")).json()["access_token"]

    # 触发一次越权审计：用普通用户访问管理路由
    await _register(client, "victim@example.com")
    victim_token = (await _login(client, "victim@example.com")).json()["access_token"]
    await client.get(
        "/api/v1/rbac/admin/users",
        headers={"Authorization": f"Bearer {victim_token}"},
    )

    # 按 allowed=false 过滤，应至少命中一条越权审计
    r = await client.get(
        "/api/v1/admin/audit-logs",
        params={"allowed": "false", "limit": "5"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(i["allowed"] is False for i in body["items"])

    # 分页：limit=1 时 items 长度为 1，pages 合理
    r2 = await client.get(
        "/api/v1/admin/audit-logs",
        params={"limit": "1", "page": "1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body2 = r2.json()
    assert len(body2["items"]) == 1
    assert body2["pages"] >= 1


async def test_audit_logs_date_range_filter(client):
    """OQ-2 回归：GET /api/v1/admin/audit-logs 的 date_from 时间范围过滤必须真正生效。

    直接插入不同时间戳的审计记录，再请求带 date_from 的查询，断言只返回该时间
    之后的记录，且返回数量严格少于不过滤的结果（证明过滤逻辑确实接线并生效）。
    """
    from datetime import datetime, timedelta

    # 准备一个 admin 账户用于调用审计查询接口
    await _register(client, "daterange@example.com")
    await _promote_to_admin("daterange@example.com")
    admin_token = (await _login(client, "daterange@example.com")).json()["access_token"]

    # 在测试库直接插入三条不同时间戳的审计记录（naive datetime，保证 SQLite 比较一致）
    base = datetime(2024, 1, 1, 12, 0, 0)
    async with TestSession() as session:
        repo = AuditRepository(session)
        for offset_hours in (0, 2, 4):
            log = AuditLog(
                user_id=None,
                user_email="daterange@example.com",
                action="auth:login",
                resource="GET /api/v1/auth/login",
                allowed=True,
                created_at=base + timedelta(hours=offset_hours),
            )
            await repo.add(log)
        await session.commit()

    # 不过滤：应能查到至少我插入的 3 条
    r_all = await client.get(
        "/api/v1/admin/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r_all.status_code == 200
    total_all = r_all.json()["total"]
    assert total_all >= 3

    # 过滤 date_from = base + 3h：应只命中 base+4h 的那条（排除 base 与 base+2h）
    cutoff = base + timedelta(hours=3)
    r_filtered = await client.get(
        "/api/v1/admin/audit-logs",
        params={"date_from": cutoff.isoformat()},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r_filtered.status_code == 200
    body = r_filtered.json()
    # 过滤后数量必须严格少于不过滤（证明 date_from 真的在起作用）
    assert 0 < body["total"] < total_all, (
        f"date_from 过滤应减少返回数量：filtered={body['total']}, all={total_all}"
    )
    # 所有返回记录的 created_at 必须 >= cutoff
    for item in body["items"]:
        assert datetime.fromisoformat(item["created_at"]) >= cutoff


# --------------------------------------------------------------------------- #
# 5. 可观测性
# --------------------------------------------------------------------------- #
async def test_x_request_id_present_on_responses(client):
    r = await client.get("/health")
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) == 8  # uuid4 前 8 位


async def test_metrics_endpoint_exposes_prometheus_and_request_counter(client):
    # 制造一个 4xx 与一个 2xx 请求，验证中间件能抓取真实状态码（F9 修复点）
    await client.get("/api/v1/auth/me")  # 401（无 token）
    await client.get("/health")  # 200
    # Starlette 会把 /metrics 307 重定向到 /metrics/，跟随重定向
    r = await client.get("/metrics", follow_redirects=True)
    assert r.status_code == 200
    text = r.text
    # Prometheus 文本格式 + 请求计数类指标
    assert "auth_request_total" in text
    # 越权审计失败指标已接线并暴露（F6 修复点：此前 AUDIT_FAILURES 定义了却从未计数）
    assert "auth_audit_failures_total" in text
    # REQUEST_COUNT 的 status 标签必须反映真实状态码（不再恒为 200）
    assert 'status="401"' in text
    assert 'status="200"' in text
    # /metrics 本身不经过 Observability 中间件，不应出现 X-Request-ID
    assert "x-request-id" not in r.headers


async def test_observability_logs_graded_levels(client):
    """中间件对 2xx / 4xx 记录分级日志（info / warning）。"""
    buf = io.StringIO()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )

    # 4xx：无 token 访问受保护接口
    await client.get("/api/v1/auth/me")
    # 2xx：健康检查
    await client.get("/health")

    out = buf.getvalue()
    assert "request warning" in out  # 4xx -> warning
    assert "request ok" in out  # 2xx -> info


# --------------------------------------------------------------------------- #
# 6. 限流
# --------------------------------------------------------------------------- #
def test_compute_token_bucket_pure_function_correctness():
    """compute_token_bucket 纯函数正确性（参数顺序已修正为 rate, capacity）。

    与 consume()/Lua 保持一致的 (tokens, ts, now, rate, capacity, requested)。
    """
    # 满桶(容量10, rate=1/s)，t=1 仍满，消费1 -> 剩9
    assert compute_token_bucket(10, 0.0, 1.0, 1, 10) == (True, 9.0)
    # 空桶且时间未流逝 -> 拒绝
    assert compute_token_bucket(0, 0.0, 0.0, 1, 10) == (False, 0.0)
    # 空桶 rate=1/s，5 秒后补充 5（受容量10限制），消费1 -> 剩4
    assert compute_token_bucket(0, 0.0, 5.0, 1, 10) == (True, 4.0)
    # 新桶初始化为容量
    assert compute_token_bucket(None, 0.0, 1.0, 1, 10) == (True, 9.0)
    # 长时间未用也不超过容量
    assert compute_token_bucket(0, 0.0, 1000.0, 1, 10) == (True, 9.0)
    # 一次请求超过剩余 -> 拒绝且不扣减
    assert compute_token_bucket(3, 0.0, 0.0, 1, 10, requested=5) == (False, 3.0)


@pytest.mark.skipif(
    not _redis_available(),
    reason="Redis 不可达：限流 fail-open，跳过 HTTP 层 429 验证（不污染套件）",
)
async def test_rate_limit_http_returns_429_when_exhausted(client, monkeypatch):
    """仅在 Redis 可达时验证：超过令牌桶容量返回 429。

    通过临时把容量调小（AUTH_RATE_LIMIT=3）来触发限流，避免真实 60 次请求。
    """
    monkeypatch.setattr(settings, "rate_limit", 3)
    # 同一 IP（ASGITransport 下 request.client 为 None -> 共享 rl:unknown 桶）
    status_codes = []
    for _ in range(6):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "ratelimit@example.com", "password": "wrong"},
        )
        status_codes.append(r.status_code)
    # 容量=3，第 4 次起应被限流
    assert 429 in status_codes
