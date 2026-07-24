"""审计日志 ORM 模型：记录'谁、在何时、访问了什么、结果如何'。

与 users 表解耦，单独成表，方便后续按用户/动作/时间检索（见 Phase 6 可观测性）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from auth_middleware.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    user_email: Mapped[str] = mapped_column(String(255), default="anonymous")
    # 形如 "users:read" —— 对应 casbin 的 obj:act
    action: Mapped[str] = mapped_column(String(128))
    # 形如 "GET /api/v1/rbac/admin/users"
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 允许 / 拒绝（拒绝的也要记，这才是审计的价值）
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
