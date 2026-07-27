本文档基于全量 pytest 33 passed + 1 skipped 实测结果整理。

# 认证与授权中台 — 测试文档

> 项目路径：`D:\code\auth-middleware`
> 测试框架：pytest 9.1.1 + pytest-asyncio（asyncio_mode=auto）+ httpx 0.27+
> 实测环境：Python 3.13.2（`.venv`），Windows，Redis **不可达**
> 实测结论：**33 passed / 1 skipped**（收集 34 项，全量运行 55.67s）

---

## 1. 测试策略

### 1.1 单元测试 vs 集成测试划分

| 层级 | 文件 | 依赖 | 目的 |
| --- | --- | --- | --- |
| 纯函数单测 | `test_rate_limit.py` | 无（不触网/不触库） | 验证令牌桶算法 `compute_token_bucket` 正确性，重点防参数顺序写反 |
| 接口层测试 | `test_health.py`、`test_auth.py`、`test_rbac.py` | httpx 直驱 ASGI + 内存 SQLite | 验证单一能力域（健康检查、认证、RBAC+审计落库） |
| 端到端集成 | `test_integration.py` | httpx 直驱 ASGI + 内存 SQLite（+ Redis 可选） | 跨「认证→授权→审计→可观测→限流」全链路串联验证 |

### 1.2 集成测试用 httpx `AsyncClient` 直驱 ASGI app

所有 API 测试通过 `httpx.ASGITransport(app=app)` + `AsyncClient(transport=..., base_url="http://test")` 驱动 FastAPI 应用，**不占用任何网络端口**，因此可在 CI/单机上并发、可重复运行，且不需要真实 HTTP 服务进程。

### 1.3 独立内存 SQLite 测试库，不污染开发库

- 每个用例使用独立的 `create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})` 建立**进程内内存库**；
- 通过 `app.dependency_overrides[get_db] = override_get_db` 把生产 `get_db` 依赖替换为测试库会话；
- fixture 在用例前 `Base.metadata.create_all` 建表、用例后 `drop_all` 清库，**用例间完全隔离**；
- 与开发库 `auth_dev.db`（落地在仓库根的 SQLite 文件）物理隔离，测试不会改写、不会锁定开发数据。

### 1.4 Redis 不可达时限流用例 fail-open 降级并 skip 的设计意图

- **纯函数层** `compute_token_bucket` 不依赖 Redis，故令牌桶逻辑在任意环境下都可测（见 `test_rate_limit.py` 与 `test_integration.test_compute_token_bucket_pure_function_correctness`）。
- **HTTP 层 429** 依赖 Redis 中的令牌桶状态（`consume()` 经 Lua 脚本原子扣减）。当 Redis 不可达时，`consume()` 按 `fail-open` 设计**放行**（返回 `(True, capacity)`），保证限流组件自身故障不阻断业务。
- 因此 `test_rate_limit_http_returns_429_when_exhausted` 用 `@pytest.mark.skipif(not _redis_available(), reason=...)` 在 Redis 不可达时**自动跳过**，避免套件因环境缺 Redis 而变红，同时保留"Redis 可达时必验证 429"的契约。
- 设计意图：限流是**加固项而非阻断项**；缺 Redis 不污染套件稳定性，CI 起 Redis 即可补齐该用例（见 §3、§7）。

---

## 2. 测试范围

| # | 覆盖模块 | 关键验证点 | 对应测试文件 / 用例 |
| --- | --- | --- | --- |
| 1 | 认证闭环 | 注册→登录→`/me`→refresh→新令牌可用；错误密码/过期令牌/类型错令牌（access 当 refresh）均 401；**停用用户 refresh 立即失效**（F2） | `test_auth.py`、`test_integration.py` |
| 2 | RBAC 越权 | 普通用户访问管理路由 403；admin 200；casbin 策略真正生效（user 仅 profile，admin 通配） | `test_rbac.py`、`test_integration.py` |
| 3 | 审计落库 + 时间过滤 | 登录/越权均落审计；`GET /api/v1/admin/audit-logs` 分页；**`date_from`/`date_to` 时间范围过滤真实生效**（OQ-2 回归） | `test_rbac.test_denied_access_is_audited`、`test_integration` 审计三用例 |
| 4 | 可观测 X-Request-ID 与 /metrics | 响应头含 `X-Request-ID`（uuid4 前 8 位）；`/metrics` 暴露 `auth_request_total` 且 `status` 标签真实（含 401/200）；中间件 4xx→warning、2xx→info 分级日志 | `test_integration.py` |
| 5 | 限流（纯函数 + HTTP 层） | `compute_token_bucket` 正确性（含 rate/capacity 顺序回归）；HTTP 429 仅 Redis 可达验证 | `test_rate_limit.py`、`test_integration.py` |
| 6 | 生产密钥 fail-fast | `Settings.validate_security()` 在非 debug 且使用默认密钥时启动即抛错（F-生产安全） | 仅生命周期装配，**暂无专用用例**（见 §7 已知风险③） |
| 7 | CORS | 禁用 `*` 通配，仅放行 `AUTH_CORS_ALLOW_ORIGINS` 显式源 | 仅中间件装配，**暂无专用用例**（见 §7 已知风险③） |

> 范围说明：模块 1–5 均有断言级覆盖；模块 6–7 已在应用装配/配置中生效，但当前测试套件未为其编写专用自动化断言，列为已知覆盖缺口。

---

## 3. 测试环境准备

### 3.1 Python 解释器与依赖

- **首选**：项目内置虚拟环境
  `D:\code\auth-middleware\.venv\Scripts\python.exe`
  （实测为 Python 3.13.2，已安装项目依赖与 pytest 等开发依赖）。
- **回退**：若该 venv 不可用，使用
  `C:\minicode3\python.exe`，并先安装项目：
  ```bash
  C:\minicode3\python.exe -m pip install -e ".[dev]"
  ```
  （`dev` extra 含 `pytest`、`pytest-asyncio`、`httpx`；运行时依赖见 `pyproject.toml`。）
- `pyproject.toml` 已配置 `asyncio_mode = "auto"` 与 `testpaths = ["tests"]`，无需额外命令行开关。

### 3.2 环境变量（`.env`）

- 仓库根 `.env` 已含 `AUTH_JWT_SECRET=...`（强随机密钥）。
- 该密钥使应用以**非默认密钥**运行；`validate_security()` 仅在应用 **lifespan 启动**时校验（生产 fail-fast 守卫）。
- **注意**：httpx `ASGITransport` 默认**不触发 lifespan**，因此测试过程不会执行 `validate_security()`、不会连接开发库/Redis；这与测试隔离目标一致。验证 fail-fast 需单独以 `uvicorn` 启动应用（见 §7 风险③）。

### 3.3 可选 Redis（覆盖被 skip 的 429 用例）

- 默认 `AUTH_REDIS_URL=redis://localhost:6379/0`。
- 用仓库自带 `docker-compose.yml` 起 Redis：
  ```bash
  docker-compose up -d redis   # redis:7
  ```
- Redis 可达后，`test_rate_limit_http_returns_429_when_exhausted` 将从 **skip** 变为执行，全量结果预期变为 **34 passed / 0 skipped**。

---

## 4. 执行命令

```bash
# 进入项目根（Windows Git Bash 路径写法）
cd /d/code/auth-middleware

# 仅运行集成测试套件（实测 14 passed, 1 skipped，约 31.30s）
D:/code/auth-middleware/.venv/Scripts/python.exe -m pytest tests/test_integration.py -v

# 运行全量测试（实测 33 passed, 1 skipped，约 55.67s）
D:/code/auth-middleware/.venv/Scripts/python.exe -m pytest -v
```

> 说明：本机 Redis 不可达，故全量出现 1 个 **skipped**（HTTP 429 用例）。其余用例均 PASS。

---

## 5. 测试用例清单

### 5.1 `test_health.py`（1 例）

| 用例 | 步骤 | 预期结果 |
| --- | --- | --- |
| `test_health_returns_ok` | 经 httpx 直驱 app，`GET /health` | 200；`body.status == "ok"`；含 `service` 字段 |

### 5.2 `test_auth.py`（6 例，内存 SQLite + httpx）

| 用例 | 步骤 | 预期结果 |
| --- | --- | --- |
| `test_register_success` | 注册新邮箱 | 201；响应含 `email` 且**不含 `password`** |
| `test_register_duplicate` | 同邮箱注册两次 | 第二次 409 |
| `test_login_wrong_password` | 正确邮箱 + 错误密码登录 | 401 |
| `test_login_ok_and_me` | 登录拿令牌对；无 token 访问 `/me`；带 access 访问 `/me` | 登录 200 且 `token_type=="bearer"`；无 token→401；带 token→200 且身份正确 |
| `test_refresh_issues_new_access_token` | 登录后用 refresh_token 换发 | 200；新 access 可访问 `/me`（200） |
| `test_expired_access_token_rejected` | 用 `ttl=-10` 的过期 access 访问 `/me` | 401 |

### 5.3 `test_rbac.py`（5 例，内存 SQLite + httpx + 审计落库验证）

| 用例 | 步骤 | 预期结果 |
| --- | --- | --- |
| `test_user_can_read_own_profile` | 普通用户 `GET /rbac/profile` | 200；`role=="user"` |
| `test_user_cannot_access_admin_endpoint` | 普通用户 `GET /rbac/admin/users` | 403（越权被拒） |
| `test_admin_can_access_admin_endpoint` | 提 admin 后 `GET /rbac/admin/users` | 200；列表含自己 |
| `test_user_can_update_own_profile` | 普通用户 `PUT /rbac/profile` 改昵称 | 200；`display_name` 已更新 |
| `test_denied_access_is_audited` | 普通用户越权访问管理路由 | 审计库中存在 `allowed=False` 且 `action=="users:read"` 的记录，且 `user_email` 正确 |

### 5.4 `test_rate_limit.py`（7 例，纯函数，不依赖 Redis）

| 用例 | 步骤 | 预期结果 |
| --- | --- | --- |
| `test_full_bucket_allows_one` | `compute_token_bucket(10,0,1,1,10)` | `(True, 9.0)` |
| `test_empty_bucket_rejects` | `compute_token_bucket(0,0,0,1,10)` | `(False, 0.0)` |
| `test_refill_over_time` | `compute_token_bucket(0,0,5,1,10)` | `(True, 4.0)` |
| `test_first_call_initializes_to_capacity` | `compute_token_bucket(None,0,1,1,10)` | `(True, 9.0)` |
| `test_bucket_caps_at_capacity` | `compute_token_bucket(0,0,1000,1,10)` | `(True, 9.0)`（不超容量） |
| `test_request_more_than_remaining_rejected` | `compute_token_bucket(3,0,0,1,10,requested=5)` | `(False, 3.0)`（不扣减） |
| `test_capacity_rate_order_matches_consume` | `compute_token_bucket(4,10,11,2,4)` | `(True, 3.0)`（rate/capacity 顺序与 `consume()` 一致） |

### 5.5 `test_integration.py`（15 例，1 跳过；全链路内存 SQLite + httpx）

| 用例 | 步骤 | 预期结果 |
| --- | --- | --- |
| `test_app_is_runnable_and_health_ok` | `GET /health` | 200，`status=="ok"` |
| `test_full_auth_loop_register_login_me_refresh` | 注册→登录→无 token `/me`→带 token `/me`→refresh→新 access `/me` | 全链路 201/200；无 token 401；响应不含 `password` |
| `test_expired_access_token_rejected` | 过期 access 访问 `/me` | 401 |
| `test_access_token_cannot_be_used_as_refresh` | 用 access 当 refresh_token 调 `/refresh` | 401（类型不匹配） |
| `test_refresh_token_rejected_for_inactive_user` | 登录后停用用户，再用其 refresh | 401；detail 含 `inactive`/`revoked`（F2 安全回归） |
| `test_admin_can_access_admin_endpoint_but_user_cannot` | 普通用户 vs admin 访问 `/rbac/admin/users` | 普通 403；admin 200 且含自己 |
| `test_casbin_policy_effectively_blocks_and_allows` | 直接调 `casbin.enforce` 校验 user/admin 规则 | user 仅 profile 放行、users 拒绝；admin 通配 |
| `test_login_is_audited_and_queryable_by_admin` | 登录后提 admin，`GET /api/v1/admin/audit-logs` | 200；`total>=1`；存在 `action=="auth:login"` 且 `allowed=True`、`user_email` 正确 |
| `test_audit_logs_pagination_and_filter` | 触发越权审计；按 `allowed=false` 过滤；`limit=1` 分页 | 过滤仅返回 `allowed=False`；`limit=1` 时 `items` 长 1，`pages>=1` |
| `test_audit_logs_date_range_filter` ⭐OQ-2 | 插入 3 条不同时间戳审计；查 `date_from=base+3h` | 过滤后数量**严格小于**不过滤；且返回记录 `created_at >= cutoff` |
| `test_x_request_id_present_on_responses` | `GET /health` | 响应头含 `x-request-id` 且长度 8 |
| `test_metrics_endpoint_exposes_prometheus_and_request_counter` | 造 401 与 200 请求；`GET /metrics`（跟随重定向） | 200；含 `auth_request_total`、`auth_audit_failures_total`；`status="401"` 与 `status="200"` 均存在；`/metrics` 自身无 `x-request-id` |
| `test_observability_logs_graded_levels` | 无 token 访问（4xx）+ `/health`（2xx），捕获日志 | 日志含 `request warning`（4xx）与 `request ok`（2xx） |
| `test_compute_token_bucket_pure_function_correctness` | 多组参数断言 `compute_token_bucket` | 与 `test_rate_limit.py` 互补，参数顺序正确性再验 |
| `test_rate_limit_http_returns_429_when_exhausted` ⚠️ | **仅 Redis 可达时执行**：`monkeypatch` 容量=3，连续登录 | 状态码含 429（Redis 不可达时 **skip**） |

---

## 6. 实际执行结果与通过率

### 6.1 逐文件实测（2026-07-24，Python 3.13.2，Redis 不可达）

| 测试文件 | 收集 | 通过 | 跳过 | 失败 |
| --- | --- | --- | --- | --- |
| `test_health.py` | 1 | 1 | 0 | 0 |
| `test_auth.py` | 6 | 6 | 0 | 0 |
| `test_rbac.py` | 5 | 5 | 0 | 0 |
| `test_rate_limit.py` | 7 | 7 | 0 | 0 |
| `test_integration.py` | 15 | 14 | 1 | 0 |
| **合计** | **34** | **33** | **1** | **0** |

- 全量命令 `python -m pytest -v`：**33 passed, 1 skipped（55.67s）**
- 集成命令 `python -m pytest tests/test_integration.py -v`：**14 passed, 1 skipped（31.30s）**

### 6.2 通过率

- 以**全部收集数（34）**为分母：33 / 34 ≈ **97.1%（≈97%）**
- 以**非跳过用例数（33）**为分母：33 / 33 = **100%**
- 与预期 **33 passed + 1 skipped 完全一致**；无失败用例。

### 6.3 与预期归类的差异说明（如实记录）

- 任务预估为「原 16 单测 + 新增 17 集成」。实测按**文件**真实划分为：
  **19 单测**（`test_health` 1 + `test_auth` 6 + `test_rbac` 5 + `test_rate_limit` 7）
  + **15 集成**（`test_integration.py`，含 1 条件跳过）。
- 总数（34 收集 → 33 passed + 1 skipped）与预期**完全吻合**；差异仅在归类口径（部分"接口层测试"被计入单测），不影响结论与通过率。
- 性能观察（非缺陷）：全量约 56s，主要耗时来自 **Redis 不可达时每个受保护请求在 fail-open 路径上等待 ~1s 套接字超时**（约 50+ 次限流请求累计）。属设计预期，非正确性缺陷；CI 起 Redis 后该等待消失、运行显著加快（且 429 用例转为执行）。

---

## 7. 结论与已知风险

### 7.1 结论

- **质量结论：可交付。** 核心链路「认证 → 授权(RBAC/casbin) → 审计落库(含时间过滤) → 可观测(X-Request-ID /metrics / 分级日志) → 限流纯函数」已实现**断言级全覆盖且稳定通过（33 passed / 1 skipped）**。
- 限流 **HTTP 层 429** 为唯一 skip 项，根因是环境缺 Redis，非实现缺陷；Redis 就绪即自动补测。

### 7.2 已知风险 / 待办

1. **HTTP 429 用例依赖 Redis（当前 skip）。**
   CI/本地起 `docker-compose up -d redis`（redis:7）即可覆盖，预期结果变为 **34 passed / 0 skipped**。建议将 Redis 纳入 CI 服务矩阵。
2. **`auth_active_users` 指标未埋点。**
   该 Gauge 已在 `/metrics` 暴露（值为 0），但 `middleware.py` 仅 `import` 未对其 `set/inc`，属占位未接线；`test_metrics_*` 也未断言它。建议：① 在中间件对活跃认证请求 `set/inc`；或 ② 若暂不需要则移除，避免误导监控。
3. **本次修复与测试尚未 git commit。**
   建议用户提交：`compute_token_bucket` 参数顺序修正、refresh 回查用户（F2）、ObservabilityMiddleware 跳过 `/metrics`、CORS 收紧、审计登录事件、`AUDIT_FAILURES` 指标接线、OQ-2 审计时间过滤等源码改动与新增/补全测试。
4. **fail-fast 与 CORS 暂无专用自动化用例（覆盖缺口）。**
   `Settings.validate_security()`（生产密钥 fail-fast）仅在应用 lifespan 启动校验，当前测试不触发；CORS 仅中间件装配。建议补充：① 单测 `validate_security` 在 `debug=False` 且默认密钥时抛 `RuntimeError`；② 接口测试校验预检 `OPTIONS` 与 `Access-Control-Allow-Origin` 行为。
5. **fail-open 时延（性能，非缺陷）。**
   Redis 不可达时建议对"Redis 不可用"做短熔断/缓存，避免每请求 ~1s 超时拖累压测与 CI。

---

*文档生成：QA 工程师「严过关」｜实测工具：pytest 9.1.1 / httpx / aiosqlite（内存库）｜数据来源：本机真实运行，非采信他人。*
