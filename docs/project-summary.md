# 生产级认证与授权中台 — 项目总结（供新对话接手优化用）

> 整理时间：2026-07-27　|　用途：把当前项目状态压缩成一段可粘进新对话的上下文，便于继续优化
> 项目路径：`D:\code\auth-middleware`　|　Python 3.13.2（`C:\minicode3\python.exe`），依赖优先用项目内 `.venv`

---

## 0. 一句话定位
这是一个用 FastAPI 手写、可作为模板复用的**认证 + RBAC 授权 + 限流 + 审计 + 可观测性**中台，目标是给业务服务提供生产级安全底座。Phase 1-6 已实战交付，Phase 7（AI 辅助开发/版本管理）被用户取消。当前正在进行「集成测试 + 漏洞修复 + 补文档」的收尾优化。

## 1. 技术栈
FastAPI + SQLAlchemy(async) + PostgreSQL(asyncpg,生产)/SQLite(aiosqlite,开发) + Redis + casbin(RBAC) + bcrypt + JWT(python-jose, HS256) + Prometheus + structlog + gunicorn(UvicornWorker) + Docker。

## 2. 目录结构（关键文件）
```
src/auth_middleware/
├── main.py                 # FastAPI 入口：lifespan(启动校验+seed_admin)、挂中间件、mount /metrics、include routers
├── core/
│   ├── config.py           # Settings(pydantic)，AUTH_ 前缀环境变量；validate_security() 生产密钥 fail-fast
│   ├── database.py         # async engine；SQLite=StaticPool，PG=连接池(pool_size/max_overflow/pre_ping/recycle)
│   ├── security.py         # bcrypt 哈希(同步版+asyncio.to_thread 异步版)、JWT 签发/校验
│   ├── casbin.py           # Enforcer 进程单例，读 casbin_model.conf + casbin_policy.csv
│   ├── redis.py            # ConnectionPool 单例 + redis_lifespan
│   ├── rate_limit.py       # Lua 令牌桶；compute_token_bucket 纯函数；(rate,capacity) 顺序已统一
│   ├── middleware.py       # ObservabilityMiddleware：抓真实状态码 + 注入 X-Request-ID + 分级日志
│   ├── metrics.py          # Prometheus 4 指标(REQUEST_COUNT/REQUEST_DURATION/AUTH_ACTIVE_USERS/AUDIT_FAILURES)
│   ├── logging.py          # structlog 配置(json_logs 开关)
│   └── bootstrap.py        # seed_admin() 创建默认管理员
├── models/                 # user.py, audit_log.py(user_id FK→users.id, nullable)
├── schemas/                # user.py, token.py, audit_log.py
├── repositories/           # user_repository.py, audit_repository.py(list_paginated 支持 date_from/date_to)
├── services/auth_service.py
└── api/
    ├── deps.py             # get_current_user, require_permission(obj,act) 依赖工厂(自动写审计+AUDIT_FAILURES)
    └── routes/             # auth.py(register/login/refresh/me), rbac.py(profile), audit.py(audit-logs)
alembic/                    # env.py(async,render_as_batch) + versions(ad367512aefc 建表, df3cf6adbab3 user_id FK)
tests/                      # test_health/test_auth/test_rbac/test_rate_limit + test_integration.py(15例,1 skip)
Dockerfile / docker-compose.yml(postgres:16+redis:7+app) / gunicorn.conf.py / pyproject.toml
```

## 3. 当前完成状态
- ✅ Phase 1 工程骨架 /health
- ✅ Phase 2 MVP 认证 register/login/refresh/me
- ✅ Phase 3 授权 casbin RBAC + 审计 + 种子管理员
- ✅ Phase 4 高并发 Redis 令牌桶限流 + gunicorn 多 worker + 异步 bcrypt + 连接池
- ✅ Alembic 迁移（建表 + user_id 外键）
- ✅ Phase 5 容器化（多阶段 Dockerfile + compose 健康编排）
- ✅ Phase 6 日志与可观测（structlog + ObservabilityMiddleware + /metrics + 审计查询 API）
- ❌ Phase 7 已取消（用户管理 API / Token 吊销 / casbin 热更新 / 密钥轮换 等均未做）
- ✅ 集成测试 + 漏洞修复 + OQ-2 修复 + 三套文档（均**未 git commit**，见 §6）

## 4. 已落地修复（src 真实改动）
1. **ObservabilityMiddleware 抓真实状态码**：原 `scope.get("status_code")` 永远 200 → 改从 `http.response.start` 消息取真实 status，4xx/5xx 现告警、`REQUEST_COUNT.status` 标签真实（关键 bug，由集成测试暴露）。
2. **compute_token_bucket 参数顺序**统一为 `(tokens, ts, now, rate, capacity, requested)`，与 Lua 一致。
3. **/refresh 校验补强**：回查用户存在且 `is_active`，停用/注销用户拒 401。
4. **审计埋点**：登录(成功/失败)写 `auth:login` 审计；`AUDIT_FAILURES` 在越权路径 `inc()`。
5. **生产密钥 fail-fast**：`debug=False` 且 `jwt_secret` 仍为默认值 `change-me-in-production` 时启动抛 RuntimeError。
6. **CORS 收敛**：显式源列表、禁通配符、按需 `allow_credentials`。
7. **OQ-2 审计时间过滤**：`GET /api/v1/admin/audit-logs` 现接收并透传 `date_from`/`date_to` 到 `repo.list_paginated`。

## 5. 测试现状与运行
- 全量：**33 passed + 1 skipped**（0 失败，≈97%）。跳过项 = HTTP 层 429 限流用例，因本机 Redis 不可达而 fail-open 跳过（CI 起 Redis 即覆盖）。
- 运行：`.venv/Scripts/python.exe -m pytest -v`（或 `C:\minicode3\python.exe` + `pip install -e .`）。集成测试用独立内存 SQLite，不污染开发库。

## 6. Git 现状（重要：本轮改动尚未提交）
- 最新 commit：`7957d12 feat: 生产级认证与授权中台 Phase 1-6 完整交付`
- **已修改(8)**：`api/deps.py`、`api/routes/audit.py`(OQ-2)、`api/routes/auth.py`、`core/config.py`、`core/middleware.py`、`core/rate_limit.py`、`main.py`、`tests/test_rate_limit.py`
- **未跟踪(5)**：`docs/design.md`、`docs/prd.md`、`docs/test-report.md`、`docs/learning-note-phase1-6.docx`、`tests/test_integration.py`
- 建议：新对话里第一件事可以是 `git add -A && git commit -m "..."` 先把收尾工作入库。

## 7. 已知问题 / 待优化清单（新对话可挑着做）
- **OQ-1 · `auth_active_users` 指标未埋点**：`core/metrics.py` 已定义 Gauge 但从未 `.set()/.inc()`，`/metrics` 恒为空。→ 埋点实现活跃会话计数，或移除。
- **OQ-3 · 规范化管理员创建 + 密钥轮换**：当前靠 `seed_admin()` + `.env` 明文 `admin_password`，不符生产规范。
- **OQ-4 · casbin 策略热更新**：Enforcer 进程内单例，改 csv 需重启 → 支持 `load_policy()` 热加载。
- **OQ-5 · 用户管理 API**：仅演示列表，缺停用/启用/改角色/删除/分页。
- **OQ-6 · Token 吊销机制**：refresh 无黑名单/状态表，access 无服务端状态；`is_active` 变更后 access 在 1h TTL 内仍有效。
- **OQ-7 · 登录失败/撞库防护**：仅审计，无账户锁定/失败延迟/IP 计数。
- **OQ-8 · 限流维度与 X-Forwarded-For 信任**：当前按客户端 IP，取 `X-Forwarded-For` 首段（可伪造）；可加账户级限流 + 信任策略配置。
- **OQ-9 · 审计日志留存/归档**：审计表无清理策略，长期会无限增长。
- **测试缺口**：`validate_security`(密钥 fail-fast) 与 CORS 暂无专用自动化用例，建议补。

## 8. 新对话建议开场白（直接复制）
> 继续优化 `D:\code\auth-middleware` 这个认证授权中台。现状：Phase 1-6 已完成，Phase 7 取消；已补集成测试(33 passed+1 skipped) 并修复 7 类 bug + OQ-2 审计时间过滤；三套文档(prd/design/test-report)已生成；但所有收尾改动尚未 git commit。请先帮我：(1) 把当前改动提交入库；(2) 挑选一个最有价值的优化项（如 OQ-1 指标埋点 / OQ-6 Token 吊销 / OQ-7 撞库防护）做实现并补测试。先给方案再动手。

## 9. 配套文档（均在 docs/）
- `prd.md`（365 行）：27 功能需求 / 10 API / 8 待确认问题
- `design.md`（630 行）：9 张 Mermaid 图 + 23 模块说明 + 7 处修复设计说明
- `test-report.md`（224 行）：实测 33 passed/1 skipped，含风险标注
- `learning-note-phase1-6.md` / `.docx`：分阶段学习笔记
