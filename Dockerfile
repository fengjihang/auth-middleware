# syntax=docker/dockerfile:1

# ============ builder：装依赖 + 构建可安装包 ============
FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build

# 构建兜底工具（bcrypt/asyncpg 优先用 wheel，gcc 仅作编译兜底）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# 隔离依赖到 venv，便于 runtime 阶段整体复制
RUN python -m venv /opt/venv

# 复制依赖清单与源码，安装运行时依赖 + 包本身（editable 不需要，版本固定）
COPY pyproject.toml ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN /opt/venv/bin/python -m pip install --upgrade pip && \
    /opt/venv/bin/python -m pip install .

# ============ runtime：最小运行镜像 ============
FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# 只复制 venv 与运行所需文件（不含构建工具、不含宿主机 .venv）
COPY --from=builder /opt/venv /opt/venv
COPY pyproject.toml alembic.ini gunicorn.conf.py ./
COPY src ./src
COPY alembic ./alembic
COPY scripts ./scripts

# 以非 root 运行，降低镜像被攻破后的影响面
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 启动：先应用 Alembic 迁移（幂等，可重复执行），再起 gunicorn 多 worker
# 数据库/Redis 连接串等通过环境变量注入（见 docker-compose.yml）
CMD ["sh", "-c", "python scripts/migrate.py && gunicorn -c gunicorn.conf.py auth_middleware.main:app"]
