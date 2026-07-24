"""Phase 4 压测脚本（locust）。

目标：在 gunicorn + PostgreSQL + Redis 的生产配置下，对核心链路做压测，
观测 p95 延迟是否 < 200ms @ ~1000 RPS。

本地快速冒烟（无需生产依赖，用默认 SQLite + 限流 fail-open）：
    locust -f tests/perf/locustfile.py --headless -u 20 -r 5 -t 30s \
        --host http://127.0.0.1:8000

生产口径（需 gunicorn + PostgreSQL + Redis）：
    locust -f tests/perf/locustfile.py -u 1000 -r 100 -t 5m \
        --host http://<your-host>
"""

import random

from locust import HttpUser, between, task


class AuthUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        # 给每个虚拟用户注册并登录，拿到 token 供后续带鉴权请求
        self.email = f"lu_{random.randint(0, 10 ** 9)}@example.com"
        self.password = "Test@123456"
        self.client.post(
            "/api/v1/auth/register",
            json={"email": self.email, "password": self.password},
        )
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
        )
        self.token = resp.json().get("access_token")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    @task(3)
    def me(self):
        self.client.get("/api/v1/auth/me", headers=self.headers)

    @task(2)
    def profile(self):
        self.client.get("/api/v1/rbac/profile", headers=self.headers)

    @task(1)
    def login(self):
        self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
        )
