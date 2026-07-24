"""共享依赖：解析 Bearer Token 得到当前用户，以及接口级 RBAC 鉴权。

- get_current_user：解析 token → 当前用户（Phase 2 已建立）。
- require_permission(obj, act)：接口级鉴权依赖工厂，路由前检查 RBAC 权限，
  并落审计日志。鉴权逻辑只写这一处，所有受保护接口复用（DI 机制，见 Phase 1）。
"""

from collections.abc import Awaitable, Callable
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.core.casbin import enforce as casbin_enforce
from auth_middleware.core.database import get_db
from auth_middleware.core.security import decode_token
from auth_middleware.models.audit_log import AuditLog
from auth_middleware.models.user import User
from auth_middleware.repositories.audit_repository import AuditRepository
from auth_middleware.repositories.user_repository import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    user = await UserRepository(db).get_by_id(int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


def require_permission(obj: str, act: str) -> Callable[..., Awaitable[User]]:
    """接口级鉴权依赖工厂。

    用法：
        @router.get("/x")
        async def x(user: Annotated[User, Depends(require_permission("users", "read"))]):
            ...

    内部流程：
        1. 先经 get_current_user 拿到已认证用户（无 token → 401）。
        2. 用 casbin 检查 该用户角色 对 (obj, act) 是否有权。
        3. 无论放行/拒绝，都写一条审计日志（谁、何时、访问什么、结果）。
        4. 无权限 → 抛 403；有权 → 返回 user 给路由继续处理。
    """

    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        allowed = casbin_enforce(user.role, obj, act)
        audit = AuditLog(
            user_id=user.id,
            user_email=user.email,
            action=f"{obj}:{act}",
            resource=f"{request.method} {request.url.path}",
            allowed=allowed,
        )
        await AuditRepository(db).add(audit)
        await db.commit()
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
            )
        return user

    return checker
