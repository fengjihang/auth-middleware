"""吊销令牌黑名单仓储（OQ-6）。

负责把被吊销 token 的 `jti` 落库，并在认证时快速判断某 `jti` 是否已作废。
表以 jti 为主键，查询为点查，开销极低；`purge_expired` 用于清掉已过期的黑名单项。
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.models.revoked_token import RevokedToken


class RevokedTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, jti: str, exp: datetime, reason: Optional[str] = None) -> None:
        """把某个 jti 加入黑名单（幂等：重复写入同一 jti 不报错）。"""
        existing = await self.is_revoked(jti)
        if existing:
            return
        self.db.add(RevokedToken(jti=jti, exp=exp, reason=reason))
        await self.db.commit()

    async def is_revoked(self, jti: Optional[str]) -> bool:
        """判断 jti 是否已被吊销；jti 为空直接视为未吊销。"""
        if not jti:
            return False
        result = await self.db.execute(
            select(RevokedToken).where(RevokedToken.jti == jti)
        )
        return result.scalar_one_or_none() is not None

    async def purge_expired(self, now: Optional[datetime] = None) -> int:
        """清理已过期的黑名单条目，返回删除行数。可挂后台定时任务（OQ-9 范畴）。"""
        now = now or datetime.now(timezone.utc)
        result = await self.db.execute(
            delete(RevokedToken).where(RevokedToken.exp < now)
        )
        await self.db.commit()
        return result.rowcount or 0
