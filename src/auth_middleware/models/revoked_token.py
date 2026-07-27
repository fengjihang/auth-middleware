"""RevokedToken ORM 模型：令牌吊销黑名单（OQ-6）。

当某张 JWT 被主动吊销（logout / 管理员踢人）时，把它的 `jti` 写进本表。
校验链在每次认证时查本表：命中即视为已作废。表很小、按 jti 主键查，开销极低。
`exp` 记录原令牌过期时间，便于后台定期清理（purge）已无意义的黑名单条目。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from auth_middleware.core.database import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    exp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
