"""Redis 客户端层（Phase 4 限流/缓存底座）。

用 redis.asyncio 的 ConnectionPool：进程级单例，所有请求共享连接，
避免每请求新建 TCP 连接。gunicorn 多 worker 下每个 worker 进程各自一个池
（跨进程不共享，这正是 Redis 作为中心化限流存储的意义）。
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool

from auth_middleware.core.config import settings

_pool: ConnectionPool | None = None


def init_redis() -> ConnectionPool:
    """创建（或复用）进程级连接池。"""
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=50,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _pool


def get_redis():
    """从连接池拿一个客户端（连接复用）。"""
    return aioredis.Redis(connection_pool=init_redis())


async def close_redis() -> None:
    """释放连接池（lifespan 关闭时调用）。"""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


@asynccontextmanager
async def redis_lifespan() -> AsyncIterator[None]:
    """可挂进 FastAPI lifespan 的 Redis 启停上下文。

    启动时只建池对象、不建连接；连不上也不影响应用启动（限流会 fail-open）。
    """
    init_redis()
    try:
        yield
    finally:
        await close_redis()
