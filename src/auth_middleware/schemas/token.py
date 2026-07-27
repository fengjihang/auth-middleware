"""令牌相关模型。"""

from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """登出请求：提供需要吊销的令牌（OQ-6）。access/refresh 至少其一。"""

    access_token: str | None = None
    refresh_token: str | None = None
