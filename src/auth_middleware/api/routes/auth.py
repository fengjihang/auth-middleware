"""认证路由：register / login / refresh / me。

分层调用：route -> AuthService(业务) -> UserRepository(数据)。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.api.deps import get_current_user
from auth_middleware.core.database import get_db
from auth_middleware.core.rate_limit import rate_limit
from auth_middleware.core.security import create_token_pair, decode_token
from auth_middleware.models.user import User
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
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return Token(**create_token_pair(str(user.id)))


@router.post("/refresh", response_model=Token)
async def refresh(data: RefreshRequest) -> Token:
    try:
        payload = decode_token(data.refresh_token, expected_type="refresh")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    return Token(**create_token_pair(payload["sub"]))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
