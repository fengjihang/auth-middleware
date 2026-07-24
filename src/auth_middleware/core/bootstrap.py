"""启动种子：本地/演示环境自动创建一个初始管理员账号。

注意：这是"学习演示"用的便捷手段。生产环境里管理员应由迁移脚本或
独立的管理命令创建，密钥不应写在 .env 的明文 admin_password 里。
（Phase 7 会讲更规范的做法）
"""

from auth_middleware.core.config import settings
from auth_middleware.core.database import SessionLocal
from auth_middleware.core.security import hash_password_async
from auth_middleware.repositories.user_repository import UserRepository


async def seed_admin() -> None:
    """若初始管理员不存在则创建（角色 = admin）。"""
    async with SessionLocal() as db:
        repo = UserRepository(db)
        if not await repo.exists_by_email(settings.admin_email):
            await repo.create(
                settings.admin_email,
                await hash_password_async(settings.admin_password),
                role="admin",
            )
