"""健康检查接口测试：不依赖数据库，验证应用能起来并正确响应。"""

from httpx import ASGITransport, AsyncClient

from auth_middleware.main import app


async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "service" in body
