"""User ORM 模型：对应数据库 users 表。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from auth_middleware.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # 只存哈希，绝不存明文口令
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 角色：RBAC 的核心属性，默认 "user"；授权判断基于这个字段（见 casbin_policy.csv）
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)
    # 昵称（演示 profile:write 权限）
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
