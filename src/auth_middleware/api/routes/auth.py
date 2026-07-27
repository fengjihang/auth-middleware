"""认证路由：register / login / refresh / me。

分层调用：route -> AuthService(业务) -> UserRepository(数据)。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.api.deps import get_current_user
from auth_middleware.core.database import get_db
from auth_middleware.core.rate_limit import rate_limit
from auth_middleware.core.security import create_token_pair, decode_token
from auth_middleware.models.audit_log import AuditLog
from auth_middleware.models.user import User
from auth_middleware.repositories.audit_repository import AuditRepository
from auth_middleware.repositories.user_repository import UserRepository
from auth_middleware.schemas.token import RefreshRequest, Token
from auth_middleware.schemas.user import UserCreate, UserLogin, UserOut
from auth_middleware.services.auth_service import AuthService, EmailAlreadyExists

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    service = AuthService(UserRepository(db))
    try:
        return await service.register(data.email, data.password)
    except EmailAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )


@router.post("/login", response_model=Token, dependencies=[Depends(rate_limit)])
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)) -> Token:
    service = AuthService(UserRepository(db))
    user = await service.authenticate(data.email, data.password)

    # 登录是高危安全事件，无论成功失败都落审计（allowed 字段区分）。
    # 失败且用户不存在时 user_id 为 NULL（模型已允许），用 email 仍可溯源。
    audit = AuditLog(
        user_id=user.id if user is not None else None,
        user_email=data.email,
        action="auth:login",
        resource="POST /api/v1/auth/login",
        allowed=user is not None,
    )
    await AuditRepository(db).add(audit)
    await db.commit()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return Token(**create_token_pair(str(user.id)))


@router.post(
    "/refresh",
    response_model=Token,
    dependencies=[Depends(rate_limit)],
)
async def refresh(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """用 refresh token 换发新的 access/refresh 对。

    安全加固：除校验令牌本身的签名/过期/类型外，还会回查用户是否仍然存在且
    处于活跃状态。这样当用户被停用/删除后，其（最长 7 天有效的）refresh token
    会立即失效，避免"已注销账号仍可长期换发访问令牌"的越权窗口。
    """
    try:
        payload = decode_token(data.refresh_token, expected_type="refresh")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    sub = payload.get("sub")
    user = await UserRepository(db).get_by_id(int(sub)) if sub is not None else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or user inactive",
        )
    return Token(**create_token_pair(str(user.id)))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
