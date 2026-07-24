"""用户相关的请求/响应模型（Pydantic，负责出入参校验与序列化）。"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """注册入参。"""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    """登录入参。"""

    email: EmailStr
    password: str


class ProfileUpdate(BaseModel):
    """修改资料入参：仅昵称（演示 profile:write）。"""

    display_name: str | None = Field(default=None, max_length=64)


class UserOut(BaseModel):
    """用户出参：绝不暴露 hashed_password。"""

    id: int
    email: EmailStr
    is_active: bool
    role: str
    display_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}  # 允许从 ORM 对象直接构造
