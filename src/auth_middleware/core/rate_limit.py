"""Redis 令牌桶限流（Phase 4）。

令牌桶：桶容量 capacity，以恒定速率 rate(个/秒) 自动补充；每个请求消耗 1 个令牌，
桶空则拒绝(429)。相比固定窗口，令牌桶允许短时突发（攒够的令牌），更平滑。

生产实现：把桶状态(token 数, 上次时间)存在 Redis Hash，用 Lua 脚本原子地"补充+扣减"，
避免并发请求下的竞态（多条请求同时读到旧 token 数导致超发）。

依赖降级：Redis 不可用时 fail-open（放行），保证限流组件自身不会把服务搞挂。
"""

import time

from fastapi import Depends, HTTPException, Request, status

from auth_middleware.core.config import settings
from auth_middleware.core.redis import get_redis

# Lua：KEYS[1]=桶key；ARGV=rate, capacity, now(秒), requested
_TOKEN_BUCKET_LUA = """
local tokens_key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local data = redis.call('HMGET', tokens_key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
local delta = math.max(0, now - ts)
tokens = math.min(capacity, tokens + delta * rate)
local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end
local ttl = math.floor(capacity / rate * 2) + 1
redis.call('HSET', tokens_key, 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', tokens_key, ttl * 1000)
return {allowed, tostring(tokens)}
"""


def compute_token_bucket(
    tokens, ts, now, capacity, rate, requested: int = 1
) -> tuple[bool, float]:
    """纯函数版令牌桶逻辑（与 Lua 等价），便于单元测试、不依赖 Redis。"""
    if tokens is None:
        tokens, ts = capacity, now
    delta = max(0.0, now - ts)
    tokens = min(capacity, tokens + delta * rate)
    allowed = tokens >= requested
    if allowed:
        tokens -= requested
    return allowed, float(tokens)


async def consume(
    key: str, rate: float, capacity: int, requested: int = 1
) -> tuple[bool, float]:
    """在 Redis 上原子地消耗令牌；Redis 不可用时 fail-open 放行。"""
    client = get_redis()
    try:
        allowed, remaining = await client.eval(
            _TOKEN_BUCKET_LUA, 1, key, rate, capacity, time.time(), requested
        )
        return bool(int(allowed)), float(remaining)
    except Exception:
        # 限流组件自身故障不应阻断业务：降级放行
        return True, float(capacity)


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    elif request.client is not None:
        ip = request.client.host
    else:
        ip = "unknown"
    return f"rl:{ip}"


async def rate_limit(request: Request) -> None:
    """FastAPI 依赖：按客户端 IP 做令牌桶限流，超限返回 429。"""
    if not settings.rate_limit_enabled:
        return
    rate = settings.rate_limit / max(settings.rate_window, 1)
    allowed, _ = await consume(_client_key(request), rate, settings.rate_limit)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests"
        )
