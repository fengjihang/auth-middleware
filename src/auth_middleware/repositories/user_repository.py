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
