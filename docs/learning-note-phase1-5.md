# 生产级认证与授权中台 — 学习笔记（Phase 1-6）

> 适用：Python 工程化实战学习，技术栈：FastAPI + SQLAlchemy async + JWT + RBAC + Docker

---

## 目录

1. [Phase 1：工程骨架](#phase-1工程骨架)
2. [Phase 2：MVP 认证](#phase-2mvp-认证)
   - [消化图解：端到端请求流 + 四层架构](#消化图解phase-2-端到端请求流)
3. [Phase 3：授权与权限管理](#phase-3授权与权限管理)
   - [消化图解：RBAC 鉴权全链路 + 审计日志](#消化图解phase-3-rbac-鉴权与审计日志)
4. [Phase 4：高并发与工程化部署](#phase-4高并发与工程化部署)
   - [消化图解：并发架构 + 令牌桶 fail-open](#消化图解phase-4-高并发架构)
5. [Phase 5：容器化](#phase-5容器化)
   - [消化图解：多阶段构建 + compose 拓扑 + 双路径](#消化图解phase-5-容器化架构)
6. [Phase 6：日志与可观测性](#phase-6日志与可观测性)
   - [消化图解：structlog 链 + ASGI 中间件 + histogram + 审计 SQL](#消化图解phase-6-可观测性四件套)
7. [Alembic 数据库迁移](#alembic-数据库迁移)
8. [命令速查表](#命令速查表)
9. [遇到的坑与解决](#遇到的坑与解决)

---

## Phase 1：工程骨架

### 核心目标
搭建项目的目录结构、依赖管理、可编辑安装、健康检查端点、测试框架。

### 关键文件

```
auth-middleware/
├── pyproject.toml          # 项目元数据 + 依赖声明 + 可编辑安装
├── src/auth_middleware/    # 包源码（editable install）
│   ├── __init__.py
│   ├── main.py             # FastAPI app + /health 端点 + lifespan
│   ├── api/                # HTTP 路由层
│   │   ├── __init__.py
│   │   └── routes/         # 路由模块
│   ├── core/               # 横切关注点
│   │   ├── __init__.py
│   │   └── config.py       # pydantic-settings 配置
│   ├── models/             # SQLAlchemy ORM 模型
│   ├── schemas/            # Pydantic 序列化/验证
│   ├── repositories/       # 数据访问层
│   └── services/           # 业务逻辑层
├── tests/                  # pytest 测试
├── alembic/                # 数据库迁移（Phase 3 后完善）
├── .gitignore
└── README.md
```

### pyproject.toml 核心配置

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "auth-middleware"
version = "0.1.0"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "sqlalchemy[asyncio]>=2.0.30",
    "aiosqlite>=0.20.0",
    "asyncpg>=0.29.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.3.0",
    "redis>=5.0.0",
    "bcrypt>=4.1.0",
    "pyjwt>=2.9.0",
    "pycasbin>=1.6.0",
    "structlog>=24.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
    "ruff>=0.6.0",
    "mypy>=1.11.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
auth_middleware = ["*.conf", "*.csv"]
```

### 运行命令

```bash
# 安装（可编辑模式）
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 启动
.venv\Scripts\python -m uvicorn auth_middleware.main:app --reload

# 测试
.venv\Scripts\python -m pytest -q
```

### 关键知识点

- **可编辑安装**（`pip install -e .`）：包源码指向项目目录，修改代码立即生效，不用反复重装
- **pydantic-settings**：从环境变量/`.env` 读取配置，自动校验类型，用 settings.xxx 随处可访问
- **FastAPI lifespan**：`async with lifespan()` 上下文管理器，启动时建表/Seed，关闭时清理资源
- **始终用 venv 的 python 启动**：`.venv\Scripts\python.exe -m uvicorn`，避免串到系统 Python

---

## Phase 2：MVP 认证

### 核心目标
用户注册、登录（JWT 双令牌）、刷新令牌、获取当前用户信息。

### 架构分层

```
API (routes) ──→ Service (业务逻辑) ──→ Repository (数据访问) ──→ Model (ORM)
```

每层只调下一层，不跨层调用。Service 层写业务规则，Repository 层写 SQL 逻辑。

### 用户注册流程
```
POST /register
  → 校验 email 是否已存在（查库）
  → bcrypt 哈希密码
  → 写入 users 表（role=member，display_name 可选）
  → 返回用户信息（不含密码哈希）
```

### 用户登录流程
```
POST /login
  → 按 email 查用户
  → bcrypt 校验密码
  → 生成 access_token（1h 过期）+ refresh_token（7d 过期）
  → 返回双令牌
```

### 刷新令牌
```
POST /refresh
  → 校验 refresh_token 的签名和类型（type="refresh"）
  → 不查库，只做 JWT 校验（性能优）
  → 发新 access_token
```

### JWT 双令牌设计

| 令牌 | 有效期 | 用途 | 存储 |
|------|--------|------|------|
| access_token | 1 小时 | 每次请求的鉴权 | 前端内存 / 短时存储 |
| refresh_token | 7 天 | 换取新的 access_token | httpOnly cookie / 安全存储 |

JWT 是无状态的——服务端不保存 token，只校验签名。payload 里写明 `sub`（用户 ID）、`type`（access/refresh）、`exp`（过期时间）。

```python
def create_token_pair(user_id: int) -> dict:
    access = jwt.encode(
        {"sub": user_id, "type": "access", "exp": time + 3600},
        secret, algorithm="HS256"
    )
    refresh = jwt.encode(
        {"sub": user_id, "type": "refresh", "exp": time + 7*86400},
        secret, algorithm="HS256"
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}
```

### 依赖注入（FastAPI Depends）

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(token, expected_type="access")
    user = await user_repository.get_by_id(db, payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="user not found")
    return user
```

### 关键知识点

- **SQLAlchemy async**：`async with async_session() as session`、`await session.execute()`、`await session.commit()`，全程异步无阻塞
- **bcrypt**：刻意慢的哈希算法（防爆破），计算一次约 50-100ms，是高并发瓶颈（Phase 4 解决）
- **密码不存明文**：存 `hashed_password` 字段，用 `bcrypt.checkpw(明文, 哈希)` 校验
- **oauth2_scheme**：FastAPI 的 `OAuth2PasswordBearer(tokenUrl="login")`，自动从 Authorization header 提取 Bearer token

---

### 【消化图解】Phase 2 端到端请求流

#### 图 2-1：用户注册到登录的完整生命线

```
  Client                  FastAPI                   AuthService              Repository              Database
    |                         |                         |                        |                    |
    |--- POST /register ----->|                         |                        |                    |
    |   email + password      |-- auth_service.register -|                        |                    |
    |                         |   email, password        |-- repo.get_by_email --| db.execute()       |
    |                         |                         |    email               |                    |
    |                         |                         |                        |--- SELECT user --->|
    |                         |                         |                        |<--- None/User ----|
    |                         |                         |<-- user/None ---------|                    |
    |                         |                         |                        |                    |
    |                         |                         |-- bcrypt.hashpw(password)---------- CPU --->|
    |                         |                         |  (~50ms，Phase 4 做了异步卸载)           |
    |                         |                         |                        |                    |
    |                         |                         |-- repo.create() -------| db.execute()       |
    |                         |                         |   hashed_password       |                    |
    |                         |                         |                        |--- INSERT INTO --->|
    |                         |                         |<-- User --------------|                    |
    |                         |<-- UserOut ------------|                        |                    |
    |<-- 201 Created ---------|                         |                        |                    |
    |                         |                         |                        |                    |
    |--- POST /login -------->|                         |                        |                    |
    |   email + password      |-- auth_service.authenticate                      |                    |
    |                         |                         |-- repo.get_by_email --|--- SELECT -------->|
    |                         |                         |<-- User --------------|                    |
    |                         |                         |-- bcrypt.checkpw(password, hash) --- CPU -->|
    |                         |                         |-- jwt.encode(pair) ---|                    |
    |                         |<-- access+refresh ------|                        |                    |
    |<-- 200 + tokens --------|                         |                        |                    |
    |                         |                         |                        |                    |
    |--- GET /me ------------>|                         |                        |                    |
    |   Authorization: Bearer |-- jwt.decode(token) ----|(sync, Phase 4 已卸载)  |                    |
    |                         |-- repo.get_by_id -------| db.execute()            |--- SELECT -------->|
    |                         |<-- User ----------------|                        |                    |
    |<-- 200 + UserOut -------|                         |                        |                    |
```

**关键理解**：
- 注册和登录两个阶段：注册时 bcrypt 哈希密码（写），登录时 bcrypt 校验密码 + 签发 JWT（读+写）
- `/me` 不调 Service 层，直接在 API 层用 `get_current_user` 依赖——但前提是 JWT 已通过签名校验
- `/refresh` 更轻量：只校验 refresh_token 签名，连库都不查

#### 图 2-2：四层依赖链如何串联

```
FastAPI Request
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  API Layer (routes/auth.py)                          │
│  定义路由 + HTTP 响应码 + Pydantic 序列化             │
│  依赖：get_db(), get_current_user(), require_perm()   │
└────────────────────┬─────────────────────────────────┘
                     │  调用
                     ▼
┌──────────────────────────────────────────────────────┐
│  Service Layer (services/auth_service.py)            │
│  业务规则：密码策略、JWT 策略、注册逻辑               │
│  不写 SQL，通过 Repository 做数据访问                 │
└────────────────────┬─────────────────────────────────┘
                     │  调用
                     ▼
┌──────────────────────────────────────────────────────┐
│  Repository Layer (repositories/user_repository.py)  │
│  SQL 逻辑：SELECT/INSERT/UPDATE                      │
│  返回 ORM 模型对象，不返回 HTTP 响应                  │
└────────────────────┬─────────────────────────────────┘
                     │  ORM
                     ▼
┌──────────────────────────────────────────────────────┐
│  Model Layer (models/user.py)                        │
│  表映射：User.id, email, hashed_password, role...    │
│  + Core横切层：config/security/deps/database          │
└──────────────────────────────────────────────────────┘
```

**为什么分层？**
- 每层一个职责，改 SQL 不影响业务逻辑，改接口不影响数据库
- 测试时可以 mock 任意一层
- 未来加缓存、加消息队列只需改 Repository 层或 Service 层，API 层不动

---

## Phase 3：授权与权限管理

### 核心目标
角色权限控制（RBAC）、审计日志、admin 种子账号。

### RBAC （pycasbin）

**模型定义**（casbin_model.conf）：声明"谁(用户/角色) → 对什么(资源) → 能做什么(操作)"
```
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[role_definition]
g = _, _

[matchers]
g(r.sub, p.sub) && keyMatch2(r.obj, p.obj) && regexMatch(r.act, p.act)

[policy_effect]
e = some(where (p.eft == allow))
```

**策略数据**（casbin_policy.csv）：
```csv
p, admin, /api/v1/admin/*, (GET)|(POST)
p, member, /api/v1/auth/me, GET
g, admin@example.com, admin
```

模型与数据分离的好处：改权限只需改 CSV，不用改代码。

### Enforcer 单例

```python
_enforcer = None

async def get_enforcer() -> Enforcer:
    global _enforcer
    if _enforcer is None:
        _enforcer = Enforcer(MODEL_PATH, POLICY_PATH)
    return _enforcer
```

进程级单例：每个 worker 只加载一次，后续复用。

### 权限校验中间件

```python
def require_permission(obj: str, act: str):
    async def dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        enforcer: Enforcer = Depends(get_enforcer),
    ):
        allowed = await enforcer.enforce(current_user.email, obj, act)
        # 不管是否通过，先写审计日志
        await audit_repo.create(db, AuditLogCreate(...))
        if not allowed:
            raise HTTPException(status_code=403, detail="permission denied")
        return current_user
    return dependency
```

使用方式：
```python
@router.get("/admin/users", dependencies=[Depends(require_permission("/api/v1/admin/*", "GET"))])
async def list_users(...):
    ...
```

### 审计日志

```python
class AuditLog(Base):
    id: int PK auto
    user_id: int FK -> users.id (nullable, 匿名请求为空)
    action: str        # 如 "user.login", "admin.list_users"
    resource: str      # 如 "/api/v1/auth/login"
    detail: str|null   # 额外信息
    ip_address: str|null
    success: bool      # 是否成功
    created_at: datetime
```

每次权限校验（无论是否通过）都写审计日志，为事后追溯提供完整记录。

---

### 【消化图解】Phase 3 RBAC 鉴权与审计日志

#### 图 3-1：一个 admin 请求经过的全链路

以 `GET /api/v1/rbac/admin/users`（admin 用户访问用户列表）为例：

```
 Request                     FastAPI                  casbin                  Repository          Database
   │                          │                        │                         │                  │
   │  GET /admin/users        │                        │                         │                  │
   │  Authorization: Bearer   │                        │                         │                  │
   │                          │                        │                         │                  │
   │                          │  get_current_user      │                         │                  │
   │                          │  ├ jwt.decode(token)───│(校验签名+过期)          │                  │
   │                          │  └ repo.get_by_id(db, uid)───────────────────────│── SELECT ───────>│
   │                          │                         │                         │<── User ────────│
   │                          │                         │                         │                  │
   │                          │  require_permission(    │                         │                  │
   │                          │   "/api/v1/admin/*",    │                         │                  │
   │                          │   "GET")                │                         │                  │
   │                          │                         │                         │                  │
   │                          │  ── enforcer.enforce( ──│                         │                  │
   │                          │    admin@example.com,   │                         │                  │
   │                          │    "/api/v1/admin/*",   │                         │                  │
   │                          │    "GET")               │── in-memory match ─────│──────────────────│
   │                          │                         │  读取 model.conf:       │                  │
   │                          │                         │  g(r.sub, p.sub)  =    │                  │
   │                          │                         │  admin@example.com      │                  │
   │                          │                         │  是 admin 角色          │                  │
   │                          │                         │  keyMatch2/reqMatch   │                  │
   │                          │                         │  → ALLOW               │                  │
   │                          │                         │                         │                  │
   │                          │  ── 在判断前先写审计日志 ──│                         │                  │
   │                          │  audit_repo.create(     │                         │                  │
   │                          │   action="admin.list_   │                         │                  │
   │                          │   users",               │                         │                  │
   │                          │   success=True,         │                         │                  │
   │                          │   user_id=1)            │── INSERT audit_logs ───>│                  │
   │                          │                         │                         │                  │
   │                          │  通过！返回结果                                   │                  │
   │<── 200 + user list ─────│                         │                         │                  │
```

如果换成 member 用户访问同一端点：

```
   │                          │  enforcer.enforce(      │                         │                  │
   │                          │   qi@demo.com,          │                         │                  │
   │                          │   "/api/v1/admin/*",    │                         │                  │
   │                          │   "GET")                │                         │                  │
   │                          │                         │  → 策略中没有           │                  │
   │                          │                         │    qi@demo.com 是 admin │                  │
   │                          │                         │  → DENY               │                  │
   │                          │                         │                         │                  │
   │                          │  ── 仍然先写审计日志 ────│                         │                  │
   │                          │  audit_repo.create(     │── INSERT audit_logs ───>│                  │
   │                          │   success=False)        │  (allowed=False)        │                  │
   │                          │                         │                         │                  │
   │                          │  raise 403 Forbidden    │                         │                  │
   │<── 403 permission denied │                         │                         │                  │
```

**关键设计决策**：不管是否通过都写审计日志。这样安全审计人员能发现"谁在尝试越权 + 是否成功"。

#### 图 3-2：casbin 模型与策略分离架构

```
┌─────────────────────────────────────────────┐
│              pycasbin Enforcer               │
│       (core/casbin.py - 进程级单例)           │
└────┬───────────────────────┬────────────────┘
     │                       │
     ▼                       ▼
┌──────────────┐   ┌─────────────────┐
│ model.conf   │   │ policy.csv      │
│ 逻辑规则     │   │ 策略数据        │
│              │   │                 │
│ g(r.sub,     │   │ p, admin,       │
│   p.sub) &&  │   │   /admin/*,     │
│ keyMatch2(...│   │   (GET)|(POST)  │
│ ) && regex.. │   │                 │
│              │   │ g, admin@...,   │
│              │   │   admin         │
└──────────────┘   └─────────────────┘
```

**模型 vs 策略分离的意义**：模型是逻辑（"怎么判断权限"），策略是数据（"谁有什么权限"）。改权限只需改 CSV 不用改代码，运维人员也能操作。这和生产上"策略存数据库"是同一套思想——只是这里为了演示用了 CSV。

---

## Phase 4：高并发与工程化部署

### 四块能力

1. **异步全链路**：bcrypt 卸载到线程池 + DB 连接池调优
2. **Redis 令牌桶限流**：按 IP 对 register/login 限流
3. **多进程部署**：gunicorn 拉起多个 uvicorn worker
4. **压测脚本**：locust 性能测试

### 异步 bcrypt 卸载

bcrypt 是 CPU 密集操作（单个校验就 50-100ms）。如果在事件循环里同步调用，整个进程在此期间无法处理任何其他请求。

解决方案——用 `asyncio.to_thread` 把 bcrypt 丢到线程池执行：

```python
async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)

async def verify_password_async(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_password, password, hashed)
```

效果：事件循环不被阻塞，单进程也能并发服务大量 I/O 请求。

### 连接池调优

SQLite 开发环境用 `StaticPool`（单连接，测试够用）；PostgreSQL 生产用真正连接池：

```python
if database_url.startswith("sqlite"):
    engine = create_async_engine(url, poolclass=StaticPool)
else:
    engine = create_async_engine(url,
        pool_size=5,        # 池中维持的连接数
        max_overflow=10,    # 峰值可超出的连接数
        pool_pre_ping=True, # 每次取连接前 ping 一下，避免死连接
        pool_recycle=1800,  # 超过 30 分钟回收，避免 PG 闲置断开
    )
```

### 令牌桶限流

**原理**：桶容量 = 最大请求数，恒定速率补充令牌。桶空则拒绝。

```
桶 = { tokens, last_refill_time }
处理请求时：
  # 先补充（时间流逝应得的令牌）
  tokens = min(capacity, tokens + (now - last_refill_time) * rate)
  if tokens >= 1:
    tokens -= 1   → 放行
  else:
    拒绝 → 429
```

**Redis 上的 Lua 脚本**：保证"补充+扣减"的原子性（单个脚本在 Redis 上是串行执行）。

**Fail-open 降级**：Redis 连接失败时，限流直接放行。保证限流组件自身永不拖垮业务。

### gunicorn 多进程部署

```python
# gunicorn.conf.py
workers = multiprocessing.cpu_count() * 2 + 1  # 典型值：16核 → 33 workers
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:8000"
timeout = 120
graceful_timeout = 30
```

多进程绕过了 Python 的 GIL（每个 worker 一个独立进程），可以真正利用多核 CPU。

### locust 压测

```bash
locust -f tests/perf/locustfile.py --headless -u 20 -r 5 -t 30s --host http://127.0.0.1:8000
```

压测覆盖：register → login → me → rbac admin（完整的用户流）。

---

### 【消化图解】Phase 4 高并发架构

#### 图 4-1：gunicorn 多 Worker + 事件循环 + 线程池

```
                        ┌───────────────────────────────────┐
                        │       gunicorn (master)           │
                        │   监听 0.0.0.0:8000               │
                        │   负载分发到子进程                  │
                        └───────┬────────────┬──────────────┘
                                │            │
              ┌─────────────────┤     ┌──────┤
              ▼                 ▼     ▼      ▼
     ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
     │ Worker 1     │   │ Worker 2     │   │ Worker N     │
     │ (uvicorn)    │   │ (uvicorn)    │   │ (uvicorn)    │
     │              │   │              │   │              │
     │ 事件循环 ◄───┤   │ 事件循环     │   │ 事件循环     │
     │ 线程池 ◄─────┤   │ 线程池       │   │ 线程池       │
     └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
            │                  │                  │
            └──────────────────┼──────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │    PostgreSQL      │
                    │    连接池            │
                    │  pool_size=5       │
                    │  max_overflow=10   │
                    └────────────────────┘
```

**为什么这样设计？**

| 组件 | 作用 | 限制 |
|------|------|------|
| 单个事件循环 | 处理几千个并发 I/O 连接 | 不能跑 CPU 密集操作（会卡住所有请求） |
| 线程池 | 跑 bcrypt 等 CPU 操作，不阻塞事件循环 | 线程数有限（默认 min(32, CPU*5)） |
| 多个 Worker | 绕开 Python GIL，用满多核 CPU | 每个 Worker 独立进程，不共享内存 |

**请求处理路径**：
```
请求进入 → gunicorn 分发给空闲 Worker
  → uvicorn 解析 HTTP
  → FastAPI 路由分发
  → Depends 链（get_db → get_current_user → require_permission）
  → 业务逻辑（Service → Repository → DB：异步 I/O，事件循环处理）
  → bcrypt 校验/哈希→ 丢到线程池（asyncio.to_thread），事件循环继续处理其他请求
  → 线程池完成 → 事件循环拿到结果 → 返回响应
```

#### 图 4-2：令牌桶限流 + Fail-open 决策树

```
请求到达 /register 或 /login
          │
          ▼
┌──────────────────┐
│  rate_limit       │
│  (FastAPI Depends)│
└────────┬─────────┘
         │
         ▼
┌────────────────────┐      Redis 可用？      ┌─────────────────────────┐
│ try redis.ping()   │ ──────────否──────────►│   Fail-open 放行        │
│                    │                        │   log.warning("Redis    │
│      是            │                        │     unavailable,        │
└────────┬───────────┘                        │     rate limit skipped")│
         │                                    └─────────────────────────┘
         ▼
┌────────────────────┐
│ Redis Lua 脚本      │
│ EVALSHA token_     │
│ bucket.lua         │
│                    │
│ KEYS[1] = IP:reg   │
│ ARGV[1] = capacity │
│ ARGV[2] = rate     │
│ ARGV[3] = now      │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐    令牌足够？       ┌──────────────────────┐
│ Lua 返回:           │ ────────否────────►│  返回 (False, 0)     │
│ (allowed,          │                     │  触发 429 Too Many   │
│  remaining)        │                     │  请求                │
│                    │                     └──────────────────────┘
│      是            │
└────────┬───────────┘
         │
         ▼
   ┌─────────────────┐
   │ 放行，继续处理     │
   │ register/login    │
   └─────────────────┘
```

**令牌桶 Lua 脚本（简化逻辑）**：
```lua
-- KEYS[1] = bucket key (如 "rl:192.168.1.1:register")
-- ARGV[1] = capacity, ARGV[2] = rate, ARGV[3] = now

local bucket = redis.call("HMGET", KEYS[1], "tokens", "ts")
local tokens = tonumber(bucket[1]) or capacity
local ts = tonumber(bucket[2]) or now

-- 补充令牌（时间流逝应得的）
local elapsed = now - ts
tokens = math.min(capacity, tokens + elapsed * rate)

if tokens >= 1 then
    tokens = tokens - 1
    redis.call("HMSET", KEYS[1], "tokens", tokens, "ts", now)
    return {1, tokens}  -- 已转为 Python 的 True
else
    return {0, tokens}  -- 已转为 Python 的 False
end
```

**为什么 Lua 脚本**：Redis 保证单个脚本串行执行（原子性），不会出现"两个请求同时读到 tokens=1 都认为可以扣减"的竞态。如果用 Python 客户端做"读→判断→写"三步，高并发下 100% 会超发。

**Fail-open 设计哲学**：限流是"锦上添花"而非"核心功能"。Redis 宕机时，业务可以继续（虽然多跑了几个请求），但不能因为限流组件导致整个注册/登录不可用。这就是生产可用性设计。日志里能查到 Redis 不可用的警告，运维人员会处理。

---

## Phase 5：容器化

### 多阶段 Dockerfile

```dockerfile
# === Stage 1: Builder ===
FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install -e ".[dev]" --target /opt/venv
COPY src/ src/
COPY scripts/ scripts/
RUN pip install -e . --target /opt/venv

# === Stage 2: Runtime ===
FROM python:3.13-slim
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src/ src/
COPY scripts/ scripts/
COPY gunicorn.conf.py alembic.ini ./
COPY alembic/ alembic/

ENV PATH="/opt/venv/bin:$PATH"
RUN adduser --disabled-password --gecos "" appuser
USER appuser

ENTRYPOINT ["sh", "-c", "python scripts/migrate.py && exec gunicorn -c gunicorn.conf.py auth_middleware.main:app"]
```

**为什么多阶段？**：builder 阶段安装所有构建依赖（包含编译工具链），用完丢弃。runtime 阶段只复制编好的 venv + 源码，镜像更小、更安全。

### docker-compose 编排

```yaml
services:
  db:
    image: postgres:16
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U auth"]
      interval: 5s

  redis:
    image: redis:7
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s

  app:
    build: .
    depends_on:
      db:  { condition: service_healthy }
      redis: { condition: service_healthy }
    environment:
      - AUTH_DATABASE_URL=postgresql+asyncpg://auth:secret@db:5432/auth
      - AUTH_REDIS_URL=redis://redis:6379/0
    ports: ["8000:8000"]
```

**为什么要等 healthy 再启动？**：`depends_on` 默认只等容器进程启动，但 PostgreSQL 启动后还要几秒才能接受连接。用 `service_healthy` 保证 app 连上去时后端真正就绪。

### 幂等启动

```
容器入口：python scripts/migrate.py && gunicorn ...
                    │
                    ▼
            alembic upgrade head
                    │
            ┌───────┴───────┐
            ▼               ▼
       首次部署          后续重启
    alembic_version    已处于最新
       表为空              跳过
            │               │
            ▼               ▼
        建表完成        不做任何事
                    │
                    ▼
              gunicorn 启动
```

`migrate.py` 每次启动都跑，`alembic upgrade head` 只执行尚未应用的迁移脚本——幂等的。

---

### 【消化图解】Phase 5 容器化架构

#### 图 5-1：Docker 多阶段构建流程

```
                          Docker Build
                              │
                    ┌─────────▼──────────┐
                    │  Step 1: Builder    │
                    │  FROM python:3.13   │
                    │  -slim              │
                    │                     │
                    │  COPY pyproject     │
                    │  .toml              │
                    │  RUN pip install    │
                    │  -e ".[dev]"        │
                    │  --target /opt/venv │
                    │                     │
                    │  COPY src/          │
                    │  RUN pip install    │
                    │  -e . --target      │
                    │  /opt/venv          │
                    └─────────┬───────────┘
                              │ COPY --from=builder
                              ▼
                    ┌──────────────────────┐
                    │  Step 2: Runtime      │
                    │  FROM python:3.13     │
                    │  -slim (fresh)        │
                    │                       │
                    │  /opt/venv ◄── venv   │
                    │  src/          ◄──    │
                    │  scripts/     代码    │
                    │  gunicorn.conf.py     │
                    │  alembic.ini          │
                    │                       │
                    │  USER appuser         │
                    │  (非 root 运行)        │
                    │                       │
                    │  ENTRYPOINT:          │
                    │  migrate + gunicorn   │
                    └──────────┬────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  最终镜像 ≈ 200MB    │
                    │  (base ~120MB +     │
                    │   venv ~70MB +      │
                    │   src ~5MB)         │
                    └─────────────────────┘
```

**不加多阶段会怎样？** builder 阶段的 pip 缓存（~50MB）、编译中间文件、`gcc` 等工具链全会被带进镜像。最终大小从 ~200MB 膨胀到 ~500MB+，而且多了 gcc 等攻击面。

#### 图 5-2：docker-compose 网络拓扑与健康检查依赖

```
┌────────────────────────────────────────────────────────┐
│              compose network (bridge)                   │
│                                                        │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │  db               │    │  redis            │          │
│  │  postgres:16      │    │  redis:7          │          │
│  │                   │    │                   │          │
│  │  端口: 5432       │    │  端口: 6379       │          │
│  │  healthcheck:     │    │  healthcheck:     │          │
│  │  pg_isready -U    │    │  redis-cli ping   │          │
│  │  auth             │    │                   │          │
│  └────────┬──────────┘    └────────┬──────────┘          │
│           │                        │                     │
│           │  depends_on:           │  depends_on:        │
│           │  condition:            │  condition:         │
│           │  service_healthy       │  service_healthy    │
│           │                        │                     │
│           └──────────┬─────────────┘                     │
│                      │                                   │
│                      ▼                                   │
│           ┌──────────────────────┐                       │
│           │  app (auth-middleware)│                       │
│           │                      │                       │
│           │  gunicorn + uvicorn  │                       │
│           │  端口: 8000          │                       │
│           │                      │                       │
│           │  启动顺序:           │                       │
│           │  1. wait db+redis    │                       │
│           │  2. migrate.py       │                       │
│           │  3. gunicorn         │                       │
│           └──────────────────────┘                       │
│                                                        │
│  宿主机端口映射:                                         │
│  5432:5432 (db)   6379:6379 (redis)   8000:8000 (app)  │
└────────────────────────────────────────────────────────┘
```

**healthcheck 的 interval/retries 含义**：
```
Containers 同时启动 → db 可能需要 3-5s 完成初始化
  → healthcheck 每 5s 检查一次 → 第 1-2 次可能 FAIL
  → 第 3 次 pg_isready 返回 0 → 状态变为 healthy
  → app 的 depends_on 检测到 db healthy → 启动 app
```

不用 healthcheck 的后果：app 在 db 就绪前就连接 → 连不上报错退出 → Docker 重启 → 又连不上 → 循环崩溃（CrashLoopBackOff）。

#### 图 5-3：开发 vs 生产双路径——同一份代码两种运行方式

```
                        ┌────────────────────────┐
                        │     auth-middleware      │
                        │     同一份源码 + 模型     │
                        └────────────┬────────────┘
                                     │
                          AUTH_DATABASE_URL
                          (唯一控制开关)
                                     │
            ┌────────────────────────┼────────────────────┐
            │                        │                    │
            ▼                        ▼                    ▼
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
│ Dev (本地开发)        │   │ Test (CI)             │   │ Prod (容器生产)   │
│                      │   │                       │   │                  │
│ 数据库: SQLite       │   │ 数据库: SQLite(内存)   │   │ 数据库: PostgreSQL│
│ 启动: uvicorn --     │   │ 启动: pytest          │   │ 启动: gunicorn   │
│       reload         │   │                       │   │      多进程      │
│ 建表: create_all     │   │ 建表: create_all      │   │ 建表: Alembic    │
│ Redis: 可选/不用     │   │ Redis: mock            │   │ Redis: 必用     │
│ 热重载: 是           │   │ 热重载: 不适用        │   │ 热重载: 否      │
│                      │   │                       │   │                  │
│ 速度: 最快迭代        │   │ 速度: 秒级跑完         │   │ 速度: 生产口径   │
│ 环境: 本机直接跑       │   │ 环境: 无状态可重复     │   │ 环境: Docker    │
└──────────────────────┘   └──────────────────────┘   └──────────────────┘
```

**关键理解**：Alembic 的 batch 模式让迁移在 SQLite 和 PostgreSQL 上都能跑（SQLite 用"重建表"模拟 ALTER，PG 走原生 ALTER），所以 `migrate.py` 在不同环境上行为一致。

---

## Alembic 数据库迁移

### 为什么需要 Alembic？

| | `create_all`（开发） | Alembic（生产） |
|------|-------------------|----------------|
| 增量变更 | ❌ 不支持 | ✅ 版本化迁移 |
| 回滚 | ❌ 不可回滚 | ✅ `alembic downgrade` |
| 多环境一致性 | ❌ 容易漂移 | ✅ 同一套脚本 |
| 协作 | ❌ 无法协同 | ✅ 迁移文件进 Git |

### 关键文件

- `alembic.ini`：配置脚本路径、数据库连接串（占位符，运行时被 env.py 覆盖）
- `alembic/env.py`：异步迁移引擎，复用 app 的 `Base.metadata` 做自动对比
- `alembic/versions/`：版本化迁移脚本，每个文件含 `upgrade()` + `downgrade()`

### 日常命令

```bash
# 模型变更后生成迁移脚本
alembic revision --autogenerate -m "变更说明"

# 应用全部待执行迁移
alembic upgrade head

# 回滚上一版
alembic downgrade -1

# 查看当前状态
alembic current
```

### Batch 模式

```python
# SQLite 不支持 ALTER TABLE ADD CONSTRAINT，用 batch 模式重建表
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,  # SQLite 重建表 / PG 走原生 ALTER
)
```

---

## 命令速查表

### 本地开发

```bash
# 启动服务（热重载）
.venv\Scripts\python -m uvicorn auth_middleware.main:app --reload

# 跑测试
.venv\Scripts\python -m pytest -q

# 生成迁移
AUTH_DATABASE_URL="sqlite+aiosqlite:///./dev.db" .venv\Scripts\python -m alembic revision --autogenerate -m "xxx"

# 应用迁移
AUTH_DATABASE_URL="sqlite+aiosqlite:///./dev.db" .venv\Scripts\python -m alembic upgrade head
```

### 生产（容器）

```bash
# 构建并启动
docker compose up --build

# 后台运行
docker compose up -d --build

# 查看日志
docker compose logs -f app

# 停止
docker compose down
```

### 压测

```bash
# 先启动服务，再运行（另一个终端）
locust -f tests/perf/locustfile.py --headless -u 20 -r 5 -t 30s --host http://127.0.0.1:8000
```

---

## 遇到的坑与解决

| 坑 | 原因 | 解决 |
|----|------|------|
| `ModuleNotFoundError: No module named 'auth_middleware'` | 用系统 Python 的 uvicorn，而非 venv 内的 uvicorn | 始终 `venv\Scripts\python -m uvicorn` |
| `bcrypt 5.0` 与 `passlib 1.7.4` 不兼容 | passlib 未适配 bcrypt 5.0 的 API 变更 | 弃用 passlib，直接用 `bcrypt` 库（gensalt/checkpw） |
| `UnicodeDecodeError: 'gbk' codec` | Windows 上 configparser 用 GBK 读 `alembic.ini`，中文注释报错 | ini 文件只写 ASCII 字符，注释用英文 |
| `ValueError: Constraint must have a name` | batch 模式要求外键必须有名字 | 自动生成的 `None` 改成具名 `fk_audit_logs_user_id` |
| `token_bucket` 单测全挂 | 参数顺序写反（rate 和 capacity 位置互换） | 调正签名顺序 |
| `test_empty_bucket_rejects` 断言失败 | 测试用例的 `now=1.0` 比 `ts=0.0` 晚 1 秒，按 rate=1/s 已补充 1 令牌，应放行 | 改成 `now == ts` 才真正验证"空桶无补充即拒绝" |
| SQL 测试中 `Base.metadata.create_all` 被跳过 | 生产环境的 `init_db()` 非 SQLite 时直接 return | 测试默认用 SQLite，不受影响 |

---

## 依赖库总览

| 库 | 用途 | Phase |
|----|------|-------|
| fastapi | Web 框架 | P1 |
| uvicorn | ASGI 服务器 | P1 |
| sqlalchemy[asyncio] | 异步 ORM | P2 |
| aiosqlite | SQLite 异步驱动（dev） | P2 |
| asyncpg | PostgreSQL 异步驱动（prod） | P2 |
| pydantic | 数据校验/序列化 | P1 |
| pydantic-settings | 环境变量配置管理 | P1 |
| bcrypt | 密码哈希 | P2 |
| pyjwt | JWT 签发校验 | P2 |
| pycasbin | RBAC 权限模型 | P3 |
| redis | 令牌桶限流存储 | P4 |
| gunicorn | 多进程部署 | P4 |
| locust | HTTP 压测 | P4 |
| alembic | 数据库版本迁移 | — |
| structlog | 结构化日志（JSON/控制台） | P6 |
| prometheus-client | Prometheus 指标暴露 | P6 |

---

## 项目规模统计

- 源文件：~25 个
- 测试：16 个（全部通过）
- 容器：3 个（app + db + redis）
- 可观测性：结构化日志 + 审计查询 API + Prometheus 指标
- 覆盖阶段：工程骨架 → 认证 → 权限 → 高并发 → 容器化 → 日志与可观测性

---

## Phase 6：日志与可观测性

### 核心目标
结构化日志（structlog）、审计日志查询 API、Prometheus 指标端点——让系统"可观测"。

### 三块能力

#### 1. structlog 结构化日志

**配置**（`core/logging.py`）：
- `AUTH_JSON_LOGS=false`（默认，开发）：带颜色控制台渲染，人读友好
- `AUTH_JSON_LOGS=true`（生产）：JSON 行输出，适合 ELK/Loki 等日志收集系统

**ASGI 中间件**（`core/middleware.py`）：
每个请求自动记录：
- `request_id`（UUID 前 8 位，注入 `X-Request-ID` 响应头）
- `method` / `path` / `status` / `duration_ms`
- `user_id`（若已认证）
- 日志级别：`5xx → error`，`4xx → warning`，`2xx/3xx → info`

```python
class ObservabilityMiddleware:
    async def __call__(self, scope, receive, send):
        request_id = str(uuid.uuid4())[:8]
        start = time.monotonic()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                headers["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration = time.monotonic() - start
        log.info("request ok", request_id=request_id,
                 method=method, path=path, status=status,
                 duration_ms=round(duration * 1000, 1))
```

#### 2. 审计日志查询 API

**端点**：`GET /api/v1/admin/audit-logs`（仅 admin）

**查询参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| page | int | 页码（默认 1） |
| limit | int | 每页条数（默认 20，最大 100） |
| user_id | int? | 按用户 ID 过滤 |
| action | str? | 按操作类型过滤（如 "users:read"） |
| allowed | bool? | 按是否通过过滤 |
| date_from | datetime? | 起始时间 |
| date_to | datetime? | 截止时间 |

**响应格式**：
```json
{
  "items": [...],
  "total": 50,
  "page": 1,
  "limit": 20,
  "pages": 3
}
```

#### 3. Prometheus 指标

**端点**：`/metrics`（用 `prometheus_client.make_asgi_app` 挂载，不经过中间件自计）

**暴露指标**：
| 指标 | 类型 | 说明 |
|------|------|------|
| `auth_request_total` | Counter | 总请求数（method/path/status 标签） |
| `auth_request_duration_seconds` | Histogram | 请求耗时直方图（method/path 标签） |
| `auth_active_users` | Gauge | 活跃用户数（近似） |
| `auth_audit_failures_total` | Counter | 越权请求数（action 标签） |

```python
REQUEST_DURATION = Histogram(
    "auth_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
```

### 关键文件

| 文件 | 说明 |
|------|------|
| `core/logging.py` | structlog 配置（JSON/控制台切换） |
| `core/middleware.py` | ASGI 中间件（日志+指标+request_id） |
| `core/metrics.py` | Prometheus 指标定义 |
| `api/routes/audit.py` | 审计日志查询路由 |
| `schemas/audit_log.py` | 审计日志 Pydantic schema |
| `repositories/audit_repository.py` | 加 `list_paginated` 分页查询 |

### 关键知识点

- **structlog processor 链**：每个 processor 接收 event_dict，加工后传下一个。最后一个 processor 是 renderer，决定输出格式
- **ASGI send_wrapper**：中间件不能直接"看到"响应状态码（ASGI 是流式的），所以包装 `send` 函数，在 `http.response.start` 消息发出时拦截注入 header
- **Prometheus histogram**：不用平均值（会被 outlier 拉偏），用分桶计数 + `histogram_quantile(0.95, ...)` 插值算 p95。SLO 标准：`p95 < 200ms`
- **分页两次 SQL**：先 `COUNT(*)` 拿总数，再 `SELECT ... OFFSET ? LIMIT ?` 拿数据。`offset = (page - 1) * limit`

---

### 【消化图解】Phase 6 可观测性四件套

#### 图 6-1：structlog processor 链

一条 `log.info("request ok", duration_ms=42)` 的旅程：

```
log.info("request ok", duration_ms=42)
         │
         ▼
┌──────────────────────────┐
│ 1. add_log_level         │  → event_dict 加 level="info"
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 2. TimeStamper(fmt=iso)  │  → 加 timestamp="2026-07-24T..."
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 3. format_exc_info       │  → 有异常时追加 traceback
└──────────┬───────────────┘
           │
           ▼
    AUTH_JSON_LOGS?
      /        \
   false        true
    /            \
┌─────────────┐  ┌──────────────┐
│ Console     │  │ JSON         │
│ Renderer    │  │ Renderer     │
│ (彩色人读)   │  │ (机器解析)    │
└─────────────┘  └──────────────┘
```

开发输出：`2026-07-24 request ok  duration=42 level=info`
生产输出：`{"event":"request ok","level":"info","duration_ms":42,"timestamp":"..."}`

#### 图 6-2：ASGI 中间件 send_wrapper 机制

```
Client          Middleware              App (routes)
  │                 │                       │
  │── 1. request ──►│                       │
  │                 │                       │
  │                 │  start = monotonic()  │
  │                 │  request_id = uuid()  │
  │                 │                       │
  │                 │── 2. await app(scope, receive, send_wrapper) ──►│
  │                 │                       │
  │                 │                       │  route handler 处理
  │                 │                       │
  │                 │◄── 3. send({"type":"http.response.start", ──────│
  │                 │         "status": 200, "headers": [...]})        │
  │                 │                       │
  │                 │  ┌─ send_wrapper ──┐  │
  │                 │  │ 拦截 start msg   │  │
  │                 │  │ 注入 X-Request-ID│  │
  │                 │  │ 再调真实 send()  │  │
  │                 │  └─────────────────┘  │
  │                 │                       │
  │◄── 4. response (with X-Request-ID) ─────│
  │                 │                       │
  │                 │  duration = now - start
  │                 │  log.info() + metrics.inc()
  │                 │  (在 app 返回后执行)
```

**核心**：send_wrapper 包装真实 send。app 发响应时，wrapper 先拦截 `http.response.start` 消息注入 header，再转发。请求处理完全不被阻塞。

#### 图 6-3：Prometheus histogram 分桶设计

```
REQUEST_DURATION histogram
buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

请求数
  38 │              ████
  22 │         ████ ████
  18 │         ████ ████ ████
  15 │    ████ ████ ████ ████
   4 │    ████ ████ ████ ████ ████
   2 │    ████ ████ ████ ████ ████ ████
   1 │    ████ ████ ████ ████ ████ ████ ████
   0 │    ████ ████ ████ ████ ████ ████ ████ ████
      └──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────
     0.01s  0.025s 0.05s  0.1s   0.25s  0.5s   1.0s   +Inf
                    ▲
                    │
                  p95 = 0.05s  ← 95% 的请求在 50ms 以内完成

对比平均值：
  avg = 0.087s  ← 看起来还行？
  问题：1 个 1s 的 outlier 把 avg 拉高了，
        但你看不到"95% 用户体验是好的，只有 5% 慢"
```

**为什么不用平均值**：平均值会被少数慢请求拉偏，掩盖尾延迟问题。Histogram 存每个桶的累积计数，`histogram_quantile(0.95, ...)` 在桶间插值算分位数。SLO 标准：`p95 < 200ms` 是有意义的，`avg < 200ms` 会骗人。

#### 图 6-4：审计日志查询的 SQL 构建流程

```
GET /api/v1/admin/audit-logs?page=2&limit=10&user_id=3&allowed=false&date_from=2026-07-01
         │
         ▼
route: list_audit_logs()
         │
         ▼
repo.list_paginated(page=2, limit=10, user_id=3, allowed=False, date_from=...)
         │
         ▼
┌─────────────────────────────────────────┐
│ 动态 SQL 构建（链式 where）              │
│                                         │
│ stmt = select(AuditLog)                 │
│ count_stmt = select(func.count(id))     │
│                                         │
│ if user_id:   stmt.where(user_id == 3)  │
│ if allowed:   stmt.where(allowed == F)  │
│ if date_from: stmt.where(created >= ..) │
│                                         │
│ # 分页                                   │
│ offset = (2-1) * 10 = 10                │
│ stmt.order_by(id.desc()).offset(10).limit(10) │
└──────────────────┬──────────────────────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
   query 1: COUNT(*)    query 2: SELECT
   (同过滤，无分页)      (ORDER + OFFSET + LIMIT)
         │                   │
         ▼                   ▼
      total=50           items=[10条]
         │                   │
         └────────┬──────────┘
                  ▼
         PaginatedAuditLogs(
           items=[...], total=50,
           page=2, limit=10, pages=5
         )
```

**核心**：SQLAlchemy 的 `.where()` 是链式的——每个 `if` 条件成立就追加一个 WHERE 子句。两次 SQL：COUNT 拿总数（前端算总页数），SELECT 拿当前页数据。`offset = (page - 1) * limit` 是标准分页公式。
