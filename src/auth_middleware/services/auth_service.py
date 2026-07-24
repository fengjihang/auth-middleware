"""认证业务逻辑层：注册 / 登录。令牌签发放在 core.security，保持职责单一。"""

from auth_middleware.core.security import hash_password_async, verify_password_async
from auth_middleware.repositories.user_repository import UserRepository


class EmailAlreadyExists(Exception):
    """注册时邮箱已存在。"""


class AuthService:
    def __init__(self, repo: UserRepository) -> None:
        self.repo = repo

    async def register(self, email: str, password: str) -> object:
        if await self.repo.exists_by_email(email):
            raise EmailAlreadyExists(email)
        return await self.repo.create(email, await hash_password_async(password))

    async def authenticate(self, email: str, password: str) -> object | None:
        user = await self.repo.get_by_email(email)
        if user is None or not await verify_password_async(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user
