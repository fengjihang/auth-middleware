"""Alembic 环境：异步迁移，target_metadata 来自 Base.metadata。

- 连接串取自项目配置 settings.database_url（= AUTH_DATABASE_URL 环境变量），
  保证迁移连的是当前环境的库，而不是 alembic.ini 里的占位串。
- 复用应用已建好的异步 engine，享受与运行期一致的连接池配置。
- 必须导入 auth_middleware.models，否则表不会注册到 Base.metadata，
  autogenerate 会误以为"没有表要建"。
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine

from auth_middleware.core.config import settings
from auth_middleware.core.database import Base, engine

import auth_middleware.models  # noqa: F401 注册所有表到 Base.metadata

config = context.config
# 用项目配置覆盖 sqlalchemy.url，保证迁移连的是当前环境的库
config.set_main_option("sqlalchemy.url", str(settings.database_url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。用于评审或 CI 产出升级脚本。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 不支持 ALTER ADD CONSTRAINT，batch 模式在 SQLite 上重建表、
        # 在 PostgreSQL 上直接发原生 ALTER，跨库安全（见 Phase 5 容器化）
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：连库并应用迁移。"""
    connectable: AsyncEngine = engine
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
