"""RBAC 鉴权 + 审计日志集成测试。

沿用 Phase 2 的内存 SQLite 隔离方案：每个用例在干净库上跑，不依赖外部服务。
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from auth_middleware.core.database import Base, get_db
from auth_middleware.main import app
from auth_middleware.models.audit_log import AuditLog
from auth_middleware.models.user import User
from auth_middleware.repositories.audit_repository import AuditRepository
from auth_middleware.repositories.user_repository import UserRepository


TEST_ENGINE = create_async_engine(
    "sqlite+aiosqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
TestSession = async_sessionmaker(TEST_ENGINE, expire_on_commit=False)


@pytest.fixture
async def client():
    import auth_middleware.models  # 确保 audit_logs / users 表都注册

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


async def _register(client, email, password="secret123"):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def _login(client, email, password="secret123"):
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return r.json()["access_token"]


async def _promote_to_admin(email):
    """测试辅助：直接把某用户提升为 admin（模拟"授权"动作）。"""
    async with TestSession() as session:
        user = await UserRepository(session).get_by_email(email)
        user.role = "admin"
        await session.commit()


async def test_user_can_read_own_profile(client):
    await _register(client, "u1@example.com")
    token = await _login(client, "u1@example.com")
    r = await client.get(
        "/api/v1/rbac/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json()["email"] == "u1@example.com"
    assert r.json()["role"] == "user"


async def test_user_cannot_access_admin_endpoint(client):
    await _register(client, "u2@example.com")
    token = await _login(client, "u2@example.com")
    r = await client.get(
        "/api/v1/rbac/admin/users", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403  # 越权被拒


async def test_admin_can_access_admin_endpoint(client):
    await _register(client, "admin@example.com")
    await _promote_to_admin("admin@example.com")
    token = await _login(client, "admin@example.com")
    r = await client.get(
        "/api/v1/rbac/admin/users", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert any(u["email"] == "admin@example.com" for u in r.json())


async def test_user_can_update_own_profile(client):
    await _register(client, "u3@example.com")
    token = await _login(client, "u3@example.com")
    r = await client.put(
        "/api/v1/rbac/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "继航"},
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "继航"


async def test_denied_access_is_audited(client):
    await _register(client, "bad@example.com")
    token = await _login(client, "bad@example.com")
    await client.get(
        "/api/v1/rbac/admin/users", headers={"Authorization": f"Bearer {token}"}
    )
    async with TestSession() as session:
        logs = await AuditRepository(session).list_all()
        denied = [l for l in logs if not l.allowed and l.action == "users:read"]
        assert denied, "越权访问必须被记入审计日志"
        assert denied[0].user_email == "bad@example.com"
