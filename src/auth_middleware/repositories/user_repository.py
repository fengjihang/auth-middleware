"""用户仓储层：封装所有数据库访问，service 不直接写 SQL。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware.models.user import User


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.db.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def exists_by_email(self, email: str) -> bool:
        return await self.get_by_email(email) is not None

    async def create(
        self, email: str, hashed_password: str, role: str = "user"
    ) -> User:
        user = User(email=email, hashed_password=hashed_password, role=role)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def list_all(self) -> list[User]:
        result = await self.db.execute(select(User))
        return list(result.scalars().all())

    async def bump_token_version(self, user_id: int) -> User | None:
        """全量吊销：把该用户的 token_version +1，使其所有已签发 token 立即失效（OQ-6）。

        返回被更新的用户；用户不存在时返回 None。
        """
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.token_version = (user.token_version or 0) + 1
        await self.db.commit()
        await self.db.refresh(user)
        return user
