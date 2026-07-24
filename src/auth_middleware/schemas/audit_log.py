"""审计日志查询的 Pydantic schema。"""

from datetime import datetime
from pydantic import BaseModel


class AuditLogOut(BaseModel):
    """对外展示的审计日志条目。"""
    id: int
    user_id: int | None
    user_email: str | None
    action: str
    resource: str
    allowed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogQueryParams(BaseModel):
    """审计日志查询参数（querystring）。"""
    page: int = 1
    limit: int = 20
    user_id: int | None = None
    action: str | None = None
    allowed: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class PaginatedAuditLogs(BaseModel):
    """分页响应。"""
    items: list[AuditLogOut]
    total: int
    page: int
    limit: int
    pages: int
