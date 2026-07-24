"""审计日志查询路由（Phase 6）。

仅 admin 角色可访问，提供分页+过滤查询能力。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.api.deps import get_current_user, require_permission
from auth_middleware.core.database import get_db
from auth_middleware.models.user import User
from auth_middleware.repositories.audit_repository import AuditRepository
from auth_middleware.schemas.audit_log import AuditLogOut, PaginatedAuditLogs

router = APIRouter(prefix="/admin", tags=["audit"])


@router.get(
    "/audit-logs",
    response_model=PaginatedAuditLogs,
    dependencies=[Depends(require_permission("audit", "read"))],
)
async def list_audit_logs(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    user_id: int | None = Query(None, description="按用户 ID 过滤"),
    action: str | None = Query(None, description="按操作类型过滤"),
    allowed: bool | None = Query(None, description="按是否通过过滤"),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> PaginatedAuditLogs:
    """获取审计日志（分页，可过滤）。"""
    repo = AuditRepository(db)
    items, total = await repo.list_paginated(
        page=page,
        limit=limit,
        user_id=user_id,
        action=action,
        allowed=allowed,
    )
    pages = max(1, (total + limit - 1) // limit)
    return PaginatedAuditLogs(
        items=[AuditLogOut.model_validate(item) for item in items],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )
