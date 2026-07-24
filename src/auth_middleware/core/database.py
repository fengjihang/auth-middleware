"""数据库层：异步引擎、会话工厂、ORM 基类、依赖注入 get_db。

生产用 PostgreSQL(asyncpg)，本地开发/测试默认 SQLite(aiosqlite)，
二者共用同一套 SQLAlchemy ORM，切换只改 AUTH_DATABASE_URL。
"""

from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from auth_middleware.core.config import settings


def _build_engine():
    # SQLite 开发/测试用单连接池（StaticPool）；PostgreSQL 走真正的连接池并做调优
    if settings.database_url.startswith("sqlite"):
        return create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=settings.db_pool_recycle,
    )


# 异步引擎：按数据库类型选择连接池策略（Phase 4 高并发调优）
engine = _build_engine()

# 会话工厂：每次请求从它拿一个 AsyncSession
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(AsyncAttrs, DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def get_db():
    """FastAPI 依赖：每个请求一个会话，用完自动关闭（DI 机制，见 Phase 1）。"""
    async with SessionLocal() as session:
        yield session


async def init_db():
    """开发/测试(dev/test)用 create_all 建表，方便本地直接跑。

    生产(PostgreSQL)不要用这个——建表职责交给 Alembic 迁移：
    部署前执行 `alembic upgrade head`（见 scripts/migrate.py 与 README）。
    这样 schema 变更可版本化、可回滚、多环境一致。
    """
    if not settings.database_url.startswith("sqlite"):
        # 生产环境：建表交给 Alembic，这里什么都不做
        return
    # 导入模型，确保表结构注册到 Base.metadata
    import auth_middleware.models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
