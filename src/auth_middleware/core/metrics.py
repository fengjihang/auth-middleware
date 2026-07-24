"""Prometheus 指标定义（Phase 6：日志与可观测性）。

收集以下指标供 /metrics 端点暴露：
- auth_request_total：总请求数，按 method/path/status 标签
- auth_request_duration_seconds：请求耗时直方图
- auth_active_users_gauge：活跃用户（依赖 /me 调用频率，非精确计数）
- auth_audit_failures_total：审计失败的请求数（越权行为）
"""

from prometheus_client import Counter, Histogram, Gauge

REQUEST_COUNT = Counter(
    "auth_request_total",
    "Total HTTP requests by method, path, and status",
    labelnames=["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "auth_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ACTIVE_USERS = Gauge(
    "auth_active_users",
    "Number of active authenticated requests (approximate)",
)

AUDIT_FAILURES = Counter(
    "auth_audit_failures_total",
    "Total permission-denied requests (audit failures)",
    labelnames=["action"],
)
