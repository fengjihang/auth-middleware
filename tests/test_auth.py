"""认证接口集成测试：用 httpx 直接打 ASGI app（不占用端口）。

用内存 SQLite + StaticPool 做测试库，通过 dependency_overrides 替换 get_db，
保证每个用例在干净库上运行，且完全不依赖外部服务。
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
    import auth_middleware.models  # 确保表结构注册

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


async def _register(client, email="alice@example.com", password="secret123"):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def test_register_success(client):
    r = await _register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in body  # 响应里绝不能回传口令


async def test_register_duplicate(client):
    await _register(client, email="dup@example.com")
    r = await _register(client, email="dup@example.com")
    assert r.status_code == 409


async def test_login_wrong_password(client):
    await _register(client, email="bob@example.com")
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "wrong-password"},
    )
    assert r.status_code == 401


async def test_login_ok_and_me(client):
    await _register(client, email="carol@example.com")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "secret123"},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]

    # 无 token 访问 /me -> 401
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    # 带 token 访问 /me -> 200，且身份正确
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "carol@example.com"


async def test_refresh_issues_new_access_token(client):
    await _register(client, email="dave@example.com")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "dave@example.com", "password": "secret123"},
    )
    refresh_token = login.json()["refresh_token"]
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert r.status_code == 200
    new_access = r.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert me.status_code == 200


async def test_expired_access_token_rejected(client):
    from auth_middleware.core.security import create_access_token

    expired = create_access_token("1", ttl=-10)  # 已过期
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert r.status_code == 401
