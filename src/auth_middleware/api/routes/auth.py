"""认证路由：register / login / refresh / me。

分层调用：route -> AuthService(业务) -> UserRepository(数据)。
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.api.deps import get_current_user
from auth_middleware.core.database import get_db
from auth_middleware.core.rate_limit import rate_limit
from auth_middleware.core.security import create_token_pair, decode_token
from auth_middleware.models.audit_log import AuditLog
from auth_middleware.models.user import User
from auth_middleware.repositories.audit_repository import AuditRepository
from auth_middleware.repositories.revoked_token_repository import RevokedTokenRepository
from auth_middleware.repositories.user_repository import UserRepository
from auth_middleware.schemas.token import LogoutRequest, RefreshRequest, Token
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
    return Token(**create_token_pair(str(user.id), user.token_version))


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

    # ---- OQ-6：refresh 吊销检查 ----
    # 单会话吊销：该 refresh jti 是否已被 logout 写入黑名单。
    jti = payload.get("jti")
    if jti and await RevokedTokenRepository(db).is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked",
        )

    sub = payload.get("sub")
    user = await UserRepository(db).get_by_id(int(sub)) if sub is not None else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or user inactive",
        )
    # 全量吊销：refresh 签发版本与用户当前版本不一致则失效。
    if "v" in payload and payload.get("v") != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token superseded",
        )
    return Token(**create_token_pair(str(user.id), user.token_version))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    data: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单会话登出：把提供的令牌 jti 写入吊销黑名单，使其立即失效（OQ-6）。

    客户端可传入 access_token / refresh_token（或两者）进行吊销；
    仅吊销成功识别且尚未过期的令牌。重复吊销同一令牌安全幂等。
    """
    repo = RevokedTokenRepository(db)
    revoked = 0
    for raw in (data.access_token, data.refresh_token):
        if not raw:
            continue
        try:
            payload = decode_token(raw)  # 不限定类型，access/refresh 都支持
        except Exception:
            continue  # 非法/已过期令牌无需吊销
        jti = payload.get("jti")
        if not jti:
            continue
        if await repo.is_revoked(jti):
            continue
        exp = payload.get("exp")
        exp_dt = (
            datetime.fromtimestamp(exp, tz=timezone.utc)
            if exp
            else datetime.now(timezone.utc)
        )
        await repo.add(jti, exp_dt, reason="logout")
        revoked += 1
    return {"revoked": revoked}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """全量登出：吊销当前用户所有会话（bump token_version）。

    一旦 token_version 自增，该用户所有已签发的 access/refresh（其 v 均为旧值）
    在后续校验时立即 401。需有效 Bearer 令牌调用；调用者自身也会被登出。
    """
    await UserRepository(db).bump_token_version(current_user.id)
    return {"detail": "all sessions revoked"}
