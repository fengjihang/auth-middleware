"""安全层：口令哈希 + JWT 签发/校验。

要点：
- 口令哈希用 bcrypt 直调（phase1 已确认 bcrypt5 + passlib 不兼容，故绕开 passlib）。
- JWT 用 PyJWT，HS256 对称算法；access / refresh 用 payload 里的 type 字段区分，
  这样既能在刷新时拒绝拿 access token 来刷新，也为后续 Phase 4 的吊销留好扩展点。
"""

import asyncio
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone

from auth_middleware.core.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- 口令哈希 ----------------
def hash_password(password: str) -> str:
    """把明文口令哈希成 bcrypt 字符串（含随机盐，每次结果不同）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验明文口令与哈希是否匹配。"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ---------------- JWT ----------------
def _encode(sub: str, token_type: str, ttl: int) -> str:
    now = _now()
    payload = {
        "sub": sub,  # subject：这里放用户 id
        "type": token_type,  # "access" 或 "refresh"
        "iat": now,  # issued at
        "exp": now + timedelta(seconds=ttl),  # expiry
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(sub: str, ttl: int | None = None) -> str:
    return _encode(sub, "access", ttl or settings.access_token_ttl)


def create_refresh_token(sub: str, ttl: int | None = None) -> str:
    return _encode(sub, "refresh", ttl or settings.refresh_token_ttl)


def create_token_pair(sub: str) -> dict:
    """一次签发 access + refresh，返回可直接塞进 Pydantic Token 的字典。"""
    return {
        "access_token": create_access_token(sub),
        "refresh_token": create_refresh_token(sub),
        "token_type": "bearer",
    }


def decode_token(token: str, expected_type: str | None = None) -> dict:
    """校验并解码 JWT。过期/签名错会抛 jwt.PyJWTError（路由层捕获转 401）。"""
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("unexpected token type")
    return payload


# ---------------- 异步包装（Phase 4 高并发）----------------
async def hash_password_async(password: str) -> str:
    """在线程池里跑 bcrypt（CPU 密集），避免阻塞事件循环。"""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, hashed: str) -> bool:
    """在线程池里校验口令，避免阻塞事件循环。"""
    return await asyncio.to_thread(verify_password, password, hashed)
