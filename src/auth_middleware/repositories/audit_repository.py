"""审计日志仓储层：封装对 audit_logs 表的访问。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.models.audit_log import AuditLog


class AuditRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, log: AuditLog) -> AuditLog:
        self.db.add(log)
        return log

    async def list_all(self) -> list[AuditLog]:
        result = await self.db.execute(select(AuditLog))
        return list(result.scalars().all())

    async def list_paginated(
        self,
        page: int = 1,
        limit: int = 20,
        user_id: int | None = None,
        action: str | None = None,
        allowed: bool | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[AuditLog], int]:
        """分页查询审计日志，返回 (items, total_count)。

        支持按 user_id / action / allowed / 日期范围过滤。
        """
        stmt = select(AuditLog)
        count_stmt = select(func.count(AuditLog.id))

        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
            count_stmt = count_stmt.where(AuditLog.user_id == user_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
            count_stmt = count_stmt.where(AuditLog.action == action)
        if allowed is not None:
            stmt = stmt.where(AuditLog.allowed == allowed)
            count_stmt = count_stmt.where(AuditLog.allowed == allowed)
        if date_from is not None:
            stmt = stmt.where(AuditLog.created_at >= date_from)
            count_stmt = count_stmt.where(AuditLog.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(AuditLog.created_at <= date_to)
            count_stmt = count_stmt.where(AuditLog.created_at <= date_to)

        # 先查总数
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        # 分页：按时间倒序，最新的在前
        offset = (page - 1) * limit
        stmt = stmt.order_by(AuditLog.id.desc()).offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total
