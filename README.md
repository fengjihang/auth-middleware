# 生产级认证与授权中台 (auth-middleware)

基于 FastAPI 的认证与授权中台，按"实战 + 目标衍生"路线逐步构建。


## 本地开发
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"

# 运行
uvicorn auth_middleware.main:app --reload

# 测试
pytest
```

接口文档：http://127.0.0.1:8000/docs

##  高并发

本阶段叠加四块生产级能力：

1. **异步全链路**：bcrypt 哈希/校验用 `asyncio.to_thread` 卸载到线程池，不阻塞事件循环；
   PostgreSQL 连接池按 `AUTH_DB_POOL_SIZE` / `AUTH_DB_MAX_OVERFLOW` / `AUTH_DB_POOL_RECYCLE` 调优。
2. **多进程部署**：gunicorn 拉起 N 个 uvicorn worker（`workers = 2*CPU+1`），用满多核、绕过 GIL。
3. **Redis 令牌桶限流**：对 `/register`、`/login` 按 IP 限流，
   `AUTH_RATE_LIMIT` / `AUTH_RATE_WINDOW` 可调；Redis 不可用时自动 fail-open。
4. **压测**：见 `tests/perf/locustfile.py`。

### 本地运行
```bash
# 单进程（开发/调试）
uvicorn auth_middleware.main:app --reload

# 多进程（生产口径，需先 pip install -e ".[dev]"）
gunicorn -c gunicorn.conf.py auth_middleware.main:app
```

### 压测
```bash
# 本地快速冒烟（默认 SQLite + 限流 fail-open）
locust -f tests/perf/locustfile.py --headless -u 20 -r 5 -t 30s --host http://127.0.0.1:8000
```

> 注：Redis 需单独运行（如 `docker run -p 6379:6379 redis:7`）。
> 不跑 Redis 时，限流自动降级为放行，不影响其他功能。

## 数据库迁移 (Alembic)

开发/test 仍由 `init_db()` 用 `create_all` 建表（仅 SQLite）；**生产改用版本化迁移**，
让 schema 变更可版本化、可回滚、多环境一致。

```bash
# 1. 根据模型差异生成迁移脚本（自动对比当前库与 Base.metadata）
alembic revision --autogenerate -m "add xxx"

# 2. 部署前把迁移应用到数据库（生产入口等价于 `python scripts/migrate.py`）
alembic upgrade head

# 3. 回滚上一版（紧急修复用）
alembic downgrade -1
```

- 迁移脚本位于 `alembic/versions/`，当前版本记录在库的 `alembic_version` 表。
- `alembic/env.py` 复用应用异步 engine，连接串取自 `AUTH_DATABASE_URL`。
- 容器/CI：启动应用前先跑 `python scripts/migrate.py`，再起 gunicorn。

## 容器化

把"生产口径"整套用 Docker 跑起来（多进程 gunicorn + PostgreSQL + Redis）。

### 文件
- `Dockerfile`：多阶段构建。builder 装依赖 + 构建包到 venv；runtime 仅复制 venv 与源码，
  以**非 root 用户**运行，入口先 `python scripts/migrate.py`（幂等升级 schema）再起 gunicorn。
- `docker-compose.yml`：`db`(postgres:16) + `redis`(redis:7) + `app`，三者带 healthcheck，
  app 等 db/redis healthy 后才启动；`AUTH_DATABASE_URL` 用 asyncpg 指向 PG。
- `.dockerignore`：排除 `.venv`/缓存/`.env`/`*.db`，避免把宿主机环境带进镜像。

### 运行
```bash
# 构建并启动（首次会自动执行 Alembic 迁移建表）
docker compose up --build

# 仅后台运行
docker compose up -d --build
```

- 访问 http://localhost:8000/docs
- 生产务必覆盖密钥：`AUTH_JWT_SECRET`、数据库密码等可写在同级 `.env`（docker compose 自动读取）。
- 本地若只想跑 Redis（应用仍用 SQLite）：`docker run -p 6379:6379 redis:7`。

> 注：本机若无 Docker 守护进程（如受限环境），文件已就绪，可在有 Docker 的机器/CI 上直接 `up`。

## 日志与可观测性

本阶段叠加三块可观测能力：结构化日志 + 审计日志查询 API + Prometheus 指标。

### 结构化日志 (structlog)

- `core/logging.py`：structlog 配置，`AUTH_JSON_LOGS` 环境变量控制输出格式
  - `false`（默认，本地开发）：带颜色控制台，易读
  - `true`（生产）：JSON 行输出，适合日志收集系统
- `core/middleware.py`：ASGI 中间件，每个请求自动记录：
  - `request_id`（UUID 前 8 位，注入 `X-Request-ID` 响应头）
  - `method` / `path` / `status` / `duration_ms`
  - `user_id`（若已认证）
  - `5xx → error` 级别，`4xx → warning` 级别，`2xx/3xx → info` 级别

### 审计日志查询 API

- `GET /api/v1/admin/audit-logs` — 分页查询审计日志
- 参数：`page`、`limit`（默认 20，最大 100）、`user_id`、`action`、`allowed`、`date_from`、`date_to`
- 仅 `admin` 角色可访问（`require_permission("audit", "read")`）
- 返回格式：
  ```json
  {"items": [...], "total": 50, "page": 1, "limit": 20, "pages": 3}
  ```

### Prometheus 指标

- `/metrics` 端点（用 `prometheus_client.make_asgi_app` 挂载，不经过中间件自计）
- 暴露指标：
  - `auth_request_total`：总请求数（method / path / status 标签）
  - `auth_request_duration_seconds`：请求耗时直方图
  - `auth_active_users`：活跃用户数（gauge，近似值）
  - `auth_audit_failures_total`：越权请求数（action 标签）

### 运行验证

```bash
# 启动后访问
curl http://localhost:8000/health
curl http://localhost:8000/metrics          # Prometheus 指标
curl -H "Authorization: Bearer <admin_token>" \
  "http://localhost:8000/api/v1/admin/audit-logs?page=1&limit=5"
```

## 项目亮点 

这是一个按"生产级"标准逐步构建的认证授权中台，覆盖 **认证 · RBAC 授权 · 令牌吊销 · 限流 · 审计 · 可观测性** 六层能力。为什么算生产级、以及后续升级路线，见 [`docs/production-grade.md`](docs/production-grade.md)；需求/设计/测试三套文档在 `docs/`。

> 快速自检：`/health` 探活、`/metrics` Prometheus 指标、`/docs` Swagger、根路径 `/` 附带纯静态试用面板（注册/登录/刷新/RBAC 越权演示/审计查询/登出）。



