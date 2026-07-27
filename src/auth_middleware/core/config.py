"""应用配置（12-factor 风格）。

用 pydantic-settings 从环境变量读取配置，支持 .env 文件。
所有配置集中在此，避免在代码里写死密钥/连接串。
环境变量统一加 AUTH_ 前缀，例如 AUTH_DEBUG=true。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "auth-middleware"
    debug: bool = False
    json_logs: bool = False  # Phase 6：生产环境设为 True 输出 JSON 格式日志

    # ---- 数据库（Phase 2 用 SQLite 开箱即用；生产切 Postgres 改 AUTH_DATABASE_URL）----
    database_url: str = "sqlite+aiosqlite:///./auth_dev.db"

    # ---- Redis（Phase 4 限流/缓存用到）----
    redis_url: str = "redis://localhost:6379/0"

    # ---- 数据库池（Phase 4 高并发：连接池调优；仅 PostgreSQL 等真正连接池生效）----
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 1800  # 秒，超过则回收连接，避免 PG 闲置超时断开

    # ---- 限流（Phase 4：Redis 令牌桶）----
    rate_limit_enabled: bool = True
    rate_limit: int = 60  # 每个窗口允许的请求数（= 桶容量）
    rate_window: int = 60  # 窗口时长（秒）→ 默认 60 次/分钟/IP

    # ---- JWT（Phase 2 用到）----
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl: int = 3600  # 秒（access token 有效期）
    refresh_token_ttl: int = 604800  # 秒（refresh token 有效期，7 天）

    # ---- 初始管理员（仅 Phase 3 演示用；生产请用迁移/管理命令创建，勿用明文密码）----
    admin_email: str = "admin@example.com"
    admin_password: str = "admin123456"

    # ---- CORS（Phase 7 安全加固）----
    # 显式允许的前端源；默认空列表 = 不开放任何跨域，禁止 "*" 通配。
    # 生产环境由运维通过 AUTH_CORS_ALLOW_ORIGINS 设置具体域名（逗号分隔）。
    cors_allow_origins: list[str] = []

    def validate_security(self) -> None:
        """生产环境安全自检：使用默认 JWT 密钥时直接 fail-fast。

        密钥本身已从环境变量(AUTH_JWT_SECRET)读取、且 .env 已被 git 忽略，
        这里只是在"忘了配环境变量"时尽早暴露，避免以不安全配置上线。
        """
        if not self.debug and self.jwt_secret == "change-me-in-production":
            raise RuntimeError(
                "安全告警：生产环境(jwt_secret 未显式配置)仍使用默认 JWT 密钥。"
                "请通过环境变量 AUTH_JWT_SECRET 设置一个强随机密钥后再启动。"
            )


# 全局单例，其他地方直接 from auth_middleware.core.config import settings
settings = Settings()
