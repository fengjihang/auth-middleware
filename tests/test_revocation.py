"""OQ-6 Token 吊销集成测试：用 httpx 直接打 ASGI app（不占端口）。

覆盖：
- 单会话登出：logout 后 refresh 立即 401
- 全量登出：logout-all 后 access + refresh 全部 401
- 无假阳性：正常 token 不受影响
- 幂等：重复 logout 同一令牌安全
- 全量登出后重新登录仍可用

依赖内存 SQLite + dependency_overrides，不依赖 Redis。
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from auth_middleware.core.database import Base, get_db
from auth_middleware.main import app

TEST_ENGINE = create_async_engine(
    "sqlite+aiosqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
TestSession = async_sessionmaker(TEST_ENGINE, expire_on_commit=False)


@pytest.fixture
async def client():
    import auth_middleware.models  # 确保 revoked_tokens / token_version 注册

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


async def _register(client, email="revoke@example.com", password="secret123"):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def _login(client, email="revoke@example.com", password="secret123"):
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_logout_revokes_refresh(client):
    """单会话登出：吊销 refresh 后，该 refresh 不能再换发 access。"""
    await _register(client)
    tokens = await _login(client)
    refresh = tokens["refresh_token"]

    r = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200
    assert r.json()["revoked"] >= 1

    # 用已吊销的 refresh 换发 → 401
    re = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert re.status_code == 401


async def test_logout_all_invalidates_access_and_refresh(client):
    """全量登出：bump token_version 后，旧 access 与 refresh 立即失效。"""
    await _register(client)
    tokens = await _login(client)
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    r = await client.post("/api/v1/auth/logout-all", headers=_auth(access))
    assert r.status_code == 200

    # 旧 access 访问 /me → 401
    me = await client.get("/api/v1/auth/me", headers=_auth(access))
    assert me.status_code == 401
    # 旧 refresh 换发 → 401
    re = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert re.status_code == 401


async def test_valid_token_not_false_revoked(client):
    """无假阳性：未被吊销的正常 token 仍可正常使用。"""
    await _register(client)
    tokens = await _login(client)
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    me = await client.get("/api/v1/auth/me", headers=_auth(access))
    assert me.status_code == 200

    re = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert re.status_code == 200
    assert re.json()["access_token"]


async def test_logout_idempotent(client):
    """幂等：重复 logout 同一 refresh 不报错，且仍 401。"""
    await _register(client)
    tokens = await _login(client)
    refresh = tokens["refresh_token"]

    first = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    second = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert first.status_code == 200 and second.status_code == 200
    # 第二次已存在黑名单，revoked 计数为 0
    assert second.json()["revoked"] == 0

    re = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert re.status_code == 401


async def test_logout_all_then_relogin_works(client):
    """全量登出后，用同一账号重新登录拿到新版本 token 仍可用。"""
    await _register(client)
    tokens = await _login(client)
    access = tokens["access_token"]

    await client.post("/api/v1/auth/logout-all", headers=_auth(access))

    # 重新登录（密码不变）应成功，且新 token 可用
    new = await _login(client)
    me = await client.get("/api/v1/auth/me", headers=_auth(new["access_token"]))
    assert me.status_code == 200
