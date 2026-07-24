"""结构化日志配置（structlog）。

Phase 6 接入，替代默认 logging 输出。
- 开发环境（AUTH_JSON_LOGS=false）：带颜色的控制台渲染，适合人读
- 生产环境（AUTH_JSON_LOGS=true）：JSON 行输出，适合日志收集系统
"""

import structlog
from auth_middleware.core.config import settings


def configure_logging() -> None:
    """应用启动时调用一次，全局生效。"""
    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.json_logs:
        # 生产：JSON 行
        processors.append(structlog.processors.JSONRenderer())
    else:
        # 开发：带颜色的控制台
        processors.append(
            structlog.dev.ConsoleRenderer(colors=True, sort_keys=False)
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    """随处可拿的结构化 logger。"""
    return structlog.get_logger()
