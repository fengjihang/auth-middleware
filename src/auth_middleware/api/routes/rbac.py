"""RBAC 演示路由：展示接口级鉴权 + 审计日志如何落地。

- /rbac/profile        ：需 profile:read 权限（user/admin 都能进）
- PUT /rbac/profile     ：需 profile:write 权限
- /rbac/admin/users     ：需 users:read 权限（仅 admin；user 越权返回 403，且被审计记录）
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.api.deps import get_current_user, require_permission
from auth_middleware.core.database import get_db
from auth_middleware.models.user import User
from auth_middleware.repositories.user_repository import UserRepository
from auth_middleware.schemas.user import ProfileUpdate

router = APIRouter(prefix="/rbac", tags=["rbac"])


@router.get("/profile")
async def read_profile(
    user: Annotated[User, Depends(require_permission("profile", "read"))],
):
    """当前登录用户读取自己的资料（user/admin 均有权限）。"""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }


@router.put("/profile")
async def update_profile(
    payload: ProfileUpdate,
    user: Annotated[User, Depends(require_permission("profile", "write"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """当前登录用户修改自己的昵称。"""
    if payload.display_name is not None:
        user.display_name = payload.display_name
    await db.commit()
    return {"id": user.id, "display_name": user.display_name}


@router.get("/admin/users")
async def list_users(
    _: Annotated[User, Depends(require_permission("users", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """列出所有用户（仅 admin 角色可访问，用于演示越权拦截）。"""
    users = await UserRepository(db).list_all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "role": u.role,
            "display_name": u.display_name,
        }
        for u in users
    ]
