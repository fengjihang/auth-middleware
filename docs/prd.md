> 本文档基于 Phase 1-6 已实现代码 + OQ-2 修复后整理，供学习/维护参考。

# 生产级认证与授权中台（Auth & Authorization Middleware）需求文档（PRD）

| 项 | 内容 |
| --- | --- |
| 文档版本 | v1.0（PRD） |
| 整理基准 | Phase 1-6 已实现代码 + 集成测试/漏洞修复（含 OQ-2 审计时间过滤修复） |
| 应用版本 | `app.version = 0.1.0` |
| 技术栈 | FastAPI + SQLAlchemy(async) + PostgreSQL/SQLite + Redis + casbin(RBAC) + bcrypt + JWT(PyJWT/HS256) + Prometheus + structlog + gunicorn + Docker |
| 读者 | 开发、测试、运维、安全负责人、学习/维护者 |

---

## 1. 产品目标与定位

**一句话定位**：本中台为后端服务提供统一、可复用的「身份认证（Authentication）+ 基于角色的访问控制（RBAC 授权）+ 安全审计 + 可观测性」能力，让业务服务无需各自重复实现登录、令牌、限流、权限与监控等横切关注点，即可获得生产级的安全底座。

- **解决什么问题**：分散在多个服务中的认证/授权逻辑难以统一、易出安全漏洞（越权、弱密钥、口令泄露、限流失效）；缺乏集中审计与指标，安全事件不可追溯。
- **服务于谁**：需要快速获得安全基座的「业务后端服务」（本仓库即该中台自身，亦可作为模板被复用）；其终端用户（普通用户）、管理员、以及平台运维/安全观测者。

---

## 2. 用户角色（Persona）

| 角色 | 目标 | 痛点 / 关注点 |
| --- | --- | --- |
| **普通用户** | 注册账号、登录获取令牌、查看/修改个人资料、凭令牌访问受保护资源 | 口令是否安全存储；令牌过期后如何续期；越权访问被拒时得不到原因说明；被停用后旧 refresh token 是否仍可用 |
| **管理员（admin）** | 查看审计日志排查安全事件、管理用户（当前为演示性列表）、理解权限模型 | Phase 7 取消后**缺失**：无法在系统中规范创建管理员、停用/启用/改角色/删除用户、轮换密钥；越权行为希望被量化监控 |
| **平台运维 / 安全观测者** | 容器化部署、监控请求指标与日志、排查 429/越权/5xx、保证密钥与生产配置安全 | 活跃用户指标当前未埋点（恒为空）；审计日志无归档策略；需确认限流误伤与 X-Forwarded-For 信任边界 |

---

## 3. 功能需求（用户故事 + 验收标准）

> 编号规则：模块前缀 `AUTH`（认证）/ `RBAC`（授权）/ `RL`（限流）/ `AUD`（审计）/ `OBS`（可观测）/ `OPS`（部署运维）。

### 3.1 认证模块

**FR-AUTH-1 用户注册**
- **作为**普通用户，**我希望**用邮箱 + 口令注册账号，**以便**获得系统身份并开始使用。
- 验收标准：
  - 成功返回 `201`，响应体为 `UserOut`，**绝不**包含 `hashed_password`。
  - 邮箱唯一（`users.email` 唯一索引），重复注册返回 `409 Conflict`。
  - 口令以 bcrypt 哈希存储（含随机盐），库中只存哈希。
  - 口令长度 8–128，低于 8 由 Pydantic 拒绝（`422`）。

**FR-AUTH-2 用户登录并签发令牌对**
- **作为**注册用户，**我希望**用邮箱 + 口令登录，**以便**获得 access/refresh 令牌访问受保护资源。
- 验收标准：
  - 成功返回 `Token`（access_token + refresh_token + `token_type=bearer`）。
  - `access_token_ttl = 3600s`（1 小时），`refresh_token_ttl = 604800s`（7 天），由配置驱动。
  - 失败返回 `401`，并写 `auth:login` 审计（`allowed=false`）；用户不存在时 `user_id` 为 NULL 但 `user_email` 留存可溯源。
  - 登录**成功与失败均落审计**（高危安全事件）。

**FR-AUTH-3 刷新令牌对**
- **作为**已登录用户，**我希望**用 refresh token 换发新令牌对，**以便**access 过期后无需重新登录。
- 验收标准：
  - 校验 refresh token 签名/过期/`type=refresh`；类型不匹配（如用 access token 刷新）返回 `401`。
  - **安全加固**：除令牌校验外，回查用户是否存在且 `is_active`；停用/注销用户的 refresh token 立即失效，返回 `401`（detail 含 `revoked`/`inactive`）。
  - 换发成功返回新 `Token`。

**FR-AUTH-4 获取当前用户**
- **作为**已认证用户，**我希望**访问 `/me` 获取我的资料，**以便**前端展示身份。
- 验收标准：
  - 需 `Bearer <access_token>`；无 token 返回 `401`，无效/过期返回 `401`。
  - 解码后回查用户，用户不存在或非活跃同样 `401`。
  - 返回 `UserOut`（id/email/is_active/role/display_name/created_at）。

**FR-AUTH-5 口令强度策略**
- **作为**平台，**我希望**注册口令满足最低强度，**以便**降低弱口令风险。
- 验收标准：
  - 密码 `min_length=8, max_length=128`，由 `UserCreate` 在入口校验。

### 3.2 授权 / RBAC 模块

**FR-RBAC-1 接口级权限校验**
- **作为**后端开发者，**我希望**受保护接口在进入业务逻辑前自动校验 RBAC 权限，**以便**统一防越权、逻辑只写一处。
- 验收标准：
  - `require_permission(obj, act)` 作为 FastAPI 依赖工厂复用；无 token `401`，无权限 `403`。
  - 每次调用（无论放行/拒绝）均写一条审计日志。

**FR-RBAC-2 角色策略驱动授权**
- **作为**管理员，**我希望**权限由策略文件定义，**以便**改权限不动代码、不重新部署。
- 验收标准：
  - casbin `Enforcer` 为进程级单例（模型 + 策略一次性加载），`enforce(role, obj, act)` 纯内存运算。
  - 策略：`admin` 通配 `*,*`；`user` 仅 `profile:read` / `profile:write`；支持 `*` 通配符匹配。
  - 改权限只需编辑 `casbin_policy.csv`。

**FR-RBAC-3 越权拒绝与审计**
- **作为**安全审计方，**我希望**越权访问被拒（403）且被记录，**以便**发现异常访问模式。
- 验收标准：
  - 普通用户访问 `users:read` 等管理接口返回 `403`。
  - 审计记录 `allowed=false`，且 `AUDIT_FAILURES` 计数器 `+1`。

**FR-RBAC-4 读取个人资料**
- **作为**普通用户，**我希望**读取自己的资料（`profile:read`），**以便**查看身份/昵称/角色。
- 验收标准：
  - `GET /api/v1/rbac/profile`；`user` 与 `admin` 均可访问；返回 id/email/display_name/role。

**FR-RBAC-5 修改个人资料昵称**
- **作为**普通用户，**我希望**修改自己的昵称（`profile:write`），**以便**个性化展示。
- 验收标准：
  - `PUT /api/v1/rbac/profile`；仅接受 `display_name`（≤64）；持久化到 `users.display_name`。

### 3.3 限流模块

**FR-RL-1 客户端 IP 令牌桶限流**
- **作为**运维，**我希望**按客户端 IP 限流，**以便**抵御突发流量与接口滥用。
- 验收标准：
  - 默认 `rate_limit=60` 次 / `rate_window=60s`（即 60 次/分钟/IP）；桶容量 = `rate_limit`，恒定速率补充，**允许短时突发**。
  - 超出返回 `429 Too many requests`。
  - 限流逻辑以 Redis Lua 脚本原子执行（`HMGET`+补充+扣减），避免并发竞态。

**FR-RL-2 限流组件降级（fail-open）**
- **作为**运维，**我希望**Redis 不可用时限流组件放行，**以便**限流自身故障不阻断业务。
- 验收标准：
  - `consume()` 捕获 Redis 异常后返回「放行」，不影响正常请求。
  - 集成测试中该类用例在 Redis 不可达时 `skip`（不污染套件）。

### 3.4 审计模块

**FR-AUD-1 登录审计**
- **作为**安全审计方，**我希望**登录成功/失败都被记录，**以便**追溯账户活动与撞库尝试。
- 验收标准：
  - 每次 `/login` 写 `auth:login` 审计；成功 `allowed=true`，失败 `allowed=false`。
  - 失败且用户不存在时 `user_id` 为 NULL，但 `user_email` 留存。

**FR-AUD-2 鉴权事件审计**
- **作为**安全审计方，**我希望**每次受保护接口调用都记录 who/what/result，**以便**满足合规审计。
- 验收标准：
  - `require_permission` 写 `AuditLog(user_id, user_email, action="obj:act", resource="METHOD /path", allowed)`。

**FR-AUD-3 审计日志分页查询**
- **作为**管理员，**我希望**分页查询审计日志（按 user_id/action/allowed 过滤），**以便**排查安全事件。
- 验收标准：
  - `GET /api/v1/admin/audit-logs`，需 `audit:read`（仅 admin）。
  - 支持 `page`（≥1）、`limit`（1–100，默认 20）；返回 `total/pages/items`，按时间倒序。
  - 支持 `user_id` / `action` / `allowed` 过滤。

**FR-AUD-4 审计日志时间范围过滤（OQ-2 已修复）**
- **作为**管理员，**我希望**按时间区间（`date_from`/`date_to`）过滤审计日志，**以便**聚焦特定时段的事件。
- 验收标准：
  - 端点**接收并透传** `date_from` / `date_to`（ISO 8601 `datetime`）。
  - `AuditRepository.list_paginated` 按 `created_at >= date_from` 且 `created_at <= date_to` 过滤，并同步计入总数查询。
  - 已通过集成测试 `test_audit_logs_date_range_filter` 验证：带 `date_from` 时返回数量**严格少于**不过滤结果，且所有返回记录 `created_at >= cutoff`。

**FR-AUD-5 越权行为指标化**
- **作为**运维，**我希望**越权请求被计数到 Prometheus，**以便**监控异常访问模式。
- 验收标准：
  - 越权时 `AUDIT_FAILURES.labels(action="obj:act").inc()` 触发。
  - `/metrics` 暴露 `auth_audit_failures_total`。

### 3.5 可观测性模块

**FR-OBS-1 结构化日志**
- **作为**运维，**我希望**日志为结构化输出，**以便**接入日志收集系统检索。
- 验收标准：
  - 使用 structlog；`AUTH_JSON_LOGS=true`（生产）输出 JSON 行，否则彩色 console。
  - 含 log level、ISO 时间戳。

**FR-OBS-2 请求追踪 ID**
- **作为**运维，**我希望**每个响应携带 `X-Request-ID`，**以便**跨服务串联请求链路。
- 验收标准：
  - `ObservabilityMiddleware` 注入 `X-Request-ID`（uuid4 前 8 位）。
  - `/metrics` 与 `/metrics/` 跳过注入（避免指标端点自污染）。

**FR-OBS-3 Prometheus 指标端点**
- **作为**运维，**我希望**暴露请求计数/耗时指标，**以便**接入 Grafana 告警。
- 验收标准：
  - `GET /metrics`（Starlette ASGI 子应用挂载，不经过本中间件自计数）。
  - `auth_request_total{method,path,status}`：**真实**状态码（修复后不再恒为 200）。
  - `auth_request_duration_seconds{method,path}` 直方图（buckets 0.01–10s）。

**FR-OBS-4 分级日志与告警**
- **作为**运维，**我希望**4xx/5xx 记录 warning/error，**以便**快速发现异常。
- 验收标准：
  - 从 `http.response.start` 消息**真实捕获**状态码（修复点）；`status≥500` → `error`，`≥400` → `warning`，否则 `info`。

### 3.6 部署运维模块

**FR-OPS-1 容器化部署**
- **作为**运维，**我希望**一键容器化部署，**以便**环境一致、易迁移。
- 验收标准：
  - `Dockerfile`（python:3.13-slim，分离 builder/runtime，venv，非 root `appuser` 运行，启动先 `migrate` 后 `gunicorn`）。
  - `docker-compose.yml`：postgres:16 + redis:7 + app，含健康检查与依赖就绪条件。

**FR-OPS-2 数据库迁移（Alembic）**
- **作为**运维，**我希望**schema 版本化迁移，**以便**多环境一致、可回滚。
- 验收标准：
  - Alembic 迁移：`users` + `audit_logs` + `audit_logs.user_id` 外键（两个 revision）。
  - 启动执行 `alembic upgrade head`（经 `scripts/migrate.py`）。

**FR-OPS-3 多进程高并发部署**
- **作为**运维，**我希望**用 gunicorn 多 worker 跑满多核，**以便**提升并发吞吐。
- 验收标准：
  - `gunicorn -c gunicorn.conf.py`，`UvicornWorker`，`workers = cpu*2+1`，`worker_connections=1000`，支持滚动重启。

**FR-OPS-4 生产密钥 fail-fast**
- **作为**安全负责人，**我希望**生产环境未配置强 JWT 密钥时启动失败，**以便**避免以不安全配置上线。
- 验收标准：
  - `Settings.validate_security()`：当 `debug=False` 且 `jwt_secret` 仍为默认值 `change-me-in-production` 时抛 `RuntimeError` 终止启动。

**FR-OPS-5 CORS 收敛为显式源**
- **作为**安全负责人，**我希望**跨域仅放行显式配置的源，**以便**避免任意网站带用户凭证调用本 API。
- 验收标准：
  - `allow_origins = settings.cors_allow_origins`（默认空列表 = 不开放跨域），**禁用 `*` 通配符**；`allow_credentials=True`；仅放行 `AUTH_CORS_ALLOW_ORIGINS` 配置源。

**FR-OPS-6 种子管理员自动创建**
- **作为**本地/演示用户，**我希望**启动时自动创建初始管理员，**以便**快速体验管理功能。
- 验收标准：
  - `seed_admin()`：若 `admin_email` 不存在则创建 `role=admin`（使用 `admin_email`/`admin_password`）。

---

## 4. 需求池（优先级）

> P0 = 核心已交付（含本轮已修复项）；P1 = 重要（多数对应 Phase 7 取消导致的缺失能力）；P2 = 增强/可选。

### P0（Must have — 已实现）
| 需求 | 说明 | 状态 |
| --- | --- | --- |
| FR-AUTH-1~5 | 注册/登录/刷新/me/口令强度 | 已交付 |
| FR-RBAC-1~5 | 接口级鉴权/策略驱动/越权审计/资料读写 | 已交付 |
| FR-RL-1~2 | IP 令牌桶限流 + Redis 不可用 fail-open | 已交付 |
| FR-AUD-1~5 | 登录审计/鉴权审计/分页查询/时间过滤(OQ-2)/越权计数 | 已交付（OQ-2 已修复） |
| FR-OBS-1~4 | 结构化日志/Request-ID/Prometheus/分级日志 | 已交付 |
| FR-OPS-1~6 | 容器化/迁移/多进程/密钥 fail-fast/CORS 收敛/种子管理员 | 已交付 |
| 修复① | ObservabilityMiddleware 抓取真实状态码（4xx/5xx 告警、`REQUEST_COUNT.status` 真实） | 已修复 |
| 修复② | `compute_token_bucket` 参数顺序统一为 `(tokens, ts, now, rate, capacity, requested)` | 已修复 |
| 修复③ | `/refresh` 回查用户存在且 `is_active`（停用/注销拒 401） | 已修复 |
| 修复④ | 登录（成功/失败）写 `auth:login` 审计 + `AUDIT_FAILURES` 越权计数接线 | 已修复 |
| 修复⑤ | 生产环境默认 JWT 密钥 fail-fast | 已修复 |
| 修复⑥ | OQ-2：`GET /api/v1/admin/audit-logs` 接收并透传 `date_from`/`date_to` | 已修复 |
| 修复⑦ | CORS 收敛为显式源、禁用通配符 | 已修复 |

### P1（Should have — 建议补充）
| 需求 | 说明 | 关联 |
| --- | --- | --- |
| 用户管理 API | 停用/启用、改角色、删除、分页列表用户（当前仅 `rbac/admin/users` 演示列表） | Phase 7 缺失 |
| Token 吊销机制 | refresh token 黑名单/状态表；当前仅依赖 `is_active` 回查 + 过期 | Phase 7 缺失 |
| casbin 策略热更新 | 修改 `casbin_policy.csv` 无需重启进程（当前为进程内单例） | Phase 7 缺失 |
| 规范化管理员创建 + 密钥轮换 | 管理命令/迁移创建 admin，密钥定期轮换；当前用 `.env` 明文 `admin_password` | Phase 7 缺失 |
| `auth_active_users` 指标埋点 | 指标已定义但未埋点（见 OQ-1） | OQ-1 |
| 登录失败防护 | 账户级锁定 / 失败延迟 / 撞库检测 | 安全增强 |
| access token 主动吊销语义 | 明确 `is_active` 变更后 access token 在 TTL 内仍有效的可接受性 | 安全增强 |

### P2（Nice to have — 可选）
| 需求 | 说明 |
| --- | --- |
| 审计日志留存/归档/清理策略 | 防止审计表无限增长影响查询性能 |
| 限流维度增强 | 账户级限流；`X-Forwarded-For` 信任策略可配置 |
| 多因素认证（MFA） | 提升敏感操作安全性 |
| 审计日志导出 | CSV/JSON 导出供离线分析 |
| 角色自定义 / 多角色 | 突破 `user`/`admin` 两角色 |
| OpenAPI/文档增强 | 权限说明、示例 |

---

## 5. 非功能需求（NFR）

### 5.1 性能
| 项 | 指标/配置 | 来源 |
| --- | --- | --- |
| 限流阈值 | 默认 `60` 次 / `60s` / IP；桶容量 = 阈值，恒定速率补充，允许突发 | `config.rate_limit` / `rate_window` |
| 限流原子性 | Redis Lua 脚本原子「补充+扣减」 | `rate_limit._TOKEN_BUCKET_LUA` |
| 并发部署 | gunicorn `workers = cpu*2+1`，`worker_connections=1000` | `gunicorn.conf.py` |
| 数据库连接池（PG） | `pool_size=5`、`max_overflow=10`、`pool_recycle=1800s`、`pool_pre_ping` | `database._build_engine` |
| 口令运算 | bcrypt 经 `asyncio.to_thread` 避免阻塞事件循环 | `security.hash/verify_password_async` |
| 指标耗时直方图 | buckets：0.01–10s | `metrics.REQUEST_DURATION` |

### 5.2 安全
| 项 | 要求 | 来源 |
| --- | --- | --- |
| 口令哈希 | bcrypt（含随机盐），只存哈希，响应绝不回传明文/哈希 | `security.hash_password` / `UserOut` |
| JWT | HS256；access/refresh 以 `type` 区分；默认 TTL 1h / 7d | `security` / `config` |
| 令牌失效 | `/refresh` 回查 `is_active`，停用/注销立即失效；access token 类型校验 | `auth.refresh` / `deps.get_current_user` |
| RBAC 防越权 | 接口级鉴权，无权限 403 且审计；越权计数 | `deps.require_permission` / `AUDIT_FAILURES` |
| 生产密钥 | 默认弱密钥 + `debug=False` 时启动 fail-fast（`RuntimeError`） | `config.validate_security` |
| CORS | 显式源、禁用 `*`，`allow_credentials=True` | `main.py` / `config.cors_allow_origins` |
| 最小权限镜像 | 容器以非 root `appuser` 运行 | `Dockerfile` |

### 5.3 可观测性
| 项 | 要求 | 来源 |
| --- | --- | --- |
| 结构化日志 | structlog；生产 JSON 行 / 开发彩色 console | `logging.configure_logging` |
| 请求追踪 ID | 响应注入 `X-Request-ID`（uuid4 前 8 位） | `middleware.ObservabilityMiddleware` |
| 指标 | `/metrics` 暴露 `auth_request_total`(真实 status)、`auth_request_duration_seconds`、`auth_audit_failures_total` | `metrics` / `middleware` |
| 分级日志 | 4xx→warning、5xx→error、否则 info（真实状态码） | `middleware` |
| 注意 | `auth_active_users` Gauge 已定义但**未埋点**（恒为空），见 OQ-1 | `metrics.py` / `middleware.py` |

### 5.4 可用性
| 项 | 要求 | 来源 |
| --- | --- | --- |
| 容器化 | Dockerfile + docker-compose（postgres:16 + redis:7），健康检查与就绪依赖 | `Dockerfile` / `docker-compose.yml` |
| 启动顺序 | 启动先 `alembic upgrade head` 再起 gunicorn | `Dockerfile` CMD / `scripts/migrate.py` |
| 优雅退出 | gunicorn `graceful_timeout=30`、`keepalive=5` | `gunicorn.conf.py` |

### 5.5 兼容性
| 项 | 要求 | 来源 |
| --- | --- | --- |
| 双数据库后端 | PostgreSQL（asyncpg，生产）/ SQLite（aiosqlite，开发测试）共用同一 ORM，切换仅改 `AUTH_DATABASE_URL` | `database._build_engine` |
| Python | `>=3.11`（运行镜像 3.13） | `pyproject.toml` / `Dockerfile` |
| 配置 12-factor | 配置经 `AUTH_` 前缀环境变量 + `.env`，密钥不写死代码 | `config.Settings` |

---

## 6. 接口清单（API Inventory）

> 鉴权列：`公开`=无需令牌；`认证`=需 Bearer access token；`RBAC:<obj>:<act>`=额外需该权限（仅 admin 满足时标注 admin）。

| # | 方法 | 路径 | 鉴权要求 | 功能 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/health` | 公开 | 健康检查（探活） | 返回 `{"status":"ok","service":...}` |
| 2 | POST | `/api/v1/auth/register` | 公开（受限流） | 注册用户 | 201；重复邮箱 409 |
| 3 | POST | `/api/v1/auth/login` | 公开（受限流） | 登录签发令牌对 + 写审计 | 失败 401；写 `auth:login` 审计 |
| 4 | POST | `/api/v1/auth/refresh` | 公开（受限流） | refresh token 换发新令牌对 | 回查 `is_active`，失效 401 |
| 5 | GET | `/api/v1/auth/me` | 认证（access） | 获取当前用户资料 | 无 token 401 |
| 6 | GET | `/api/v1/rbac/profile` | 认证 + `RBAC:profile:read` | 读取个人资料 | user/admin 均可 |
| 7 | PUT | `/api/v1/rbac/profile` | 认证 + `RBAC:profile:write` | 修改昵称 | 仅 `display_name` |
| 8 | GET | `/api/v1/rbac/admin/users` | 认证 + `RBAC:users:read`（admin） | 列出全部用户（演示） | 越权 403 + 审计 |
| 9 | GET | `/api/v1/admin/audit-logs` | 认证 + `RBAC:audit:read`（admin） | 审计日志分页查询 | **支持参数**：`page`、`limit`(1–100)、`user_id`、`action`、`allowed`、`date_from`、`date_to`（ISO 8601）；返回 `total/pages/items` |
| 10 | GET | `/metrics` | 公开 | Prometheus 指标 | 文本格式；不经过本中间件自计数 |

> 注：FastAPI 自动挂载 `/docs`、`/redoc`（Swagger/ReDoc），属框架自带文档端点，不计入上述产品 API。

---

## 7. 待确认问题（Open Questions）

> 说明：**OQ-2（审计时间过滤 `date_from`/`date_to`）已随本轮修复闭环**，不计入待确认；下方为仍需决策/补充的项。Phase 7 已被用户取消，其规划能力（见下）当前缺失。

- **OQ-1 · `auth_active_users` 指标未埋点**：`ACTIVE_USERS` Gauge 已在 `metrics.py` 定义并被 `middleware.py` 导入，但代码中**从未调用** `.set()/.inc()`，当前恒为空值。建议三选一：① 在 `/me` 或中间件注入 `user_id` 时 `ACTIVE_USERS.set(...)` 实现近似活跃计数；② 将该指标从文档明确降级为「未启用」并加注释，避免误导监控；③ 直接移除该指标。可酌情简化处理。

- **OQ-3 · 规范化管理员创建 / 密钥轮换（Phase 7 缺失）**：当前管理员依赖启动 `seed_admin()` + `.env` 明文 `admin_password`，不符合生产规范。需确认是否提供管理命令/迁移脚本创建管理员，以及 JWT 密钥的轮换机制。

- **OQ-4 · casbin 策略热更新（Phase 7 缺失）**：当前 `enforcer` 为进程内单例，修改 `casbin_policy.csv` 需重启生效。需确认是否支持运行时热加载（如文件监听 / 管理接口 / 定时 reload）。

- **OQ-5 · 用户管理 API（Phase 7 缺失）**：当前仅有 `GET /rbac/admin/users` 演示列表，缺停用/启用、改角色、删除、分页列表等管理接口。需确认是否纳入后续迭代及权限边界。

- **OQ-6 · Token 吊销机制（Phase 7 缺失）**：refresh token 无黑名单/状态表，仅依赖 `is_active` 回查与 7 天过期；access token 无服务端状态。需确认是否需主动吊销能力（如退出登录使令牌即时失效）。

- **OQ-7 · 登录失败 / 撞库防护**：当前登录失败仅写审计，无账户级锁定、失败延迟或 IP 级失败计数。需确认是否需补充防暴力破解措施，避免凭审计被动发现撞库。

- **OQ-8 · 限流维度与 `X-Forwarded-For` 信任**：当前按客户端 IP 限流，取 `X-Forwarded-For` 首段（不可信代理可伪造）。需确认是否在反向代理后启用、是否增加账户级限流，以及 `X-Forwarded-For` 信任策略。

- **OQ-9 · 审计日志留存 / 归档 & access token 语义**：审计表无清理/归档策略，长期增长影响查询；此外 `is_active` 变更后，已签发的 access token 在 TTL（1h）内仍有效。需确认留存周期与「access token 在停用后最长 1h 内仍可用」是否可接受。

---

## 附录 A：测试现状（供参考）

- 全量测试：共 **34** 个用例；Redis 不可达时 **33 passed + 1 skipped**（skip 为 HTTP 层 429 验证，因限流 fail-open 降级）。
- 分布：`test_auth.py`(6) + `test_health.py`(1) + `test_rate_limit.py`(7) + `test_rbac.py`(5) + `test_integration.py`(15，其中 1 个条件 skip)。
- 集成测试覆盖：启动/health、认证闭环、RBAC 越权、审计（含 `date_from` 时间过滤回归）、可观测（`X-Request-ID`、真实状态码、`AUDIT_FAILURES` 暴露）、限流纯函数与条件 429。

## 附录 B：本轮已落地修复（已纳入 P0）

1. ObservabilityMiddleware 抓取真实状态码（4xx/5xx 告警、`REQUEST_COUNT.status` 真实）。
2. `compute_token_bucket` 参数顺序统一为 `(tokens, ts, now, rate, capacity, requested)`。
3. `/refresh` 回查用户存在且 `is_active`（停用/注销拒 401）。
4. 登录（成功/失败）写 `auth:login` 审计 + `AUDIT_FAILURES` 越权计数接线。
5. 生产环境默认 JWT 密钥 fail-fast。
6. **OQ-2 已修复**：`GET /api/v1/admin/audit-logs` 接收并透传 `date_from`/`date_to` 时间过滤。
7. CORS 收敛为显式源、禁用通配符。
