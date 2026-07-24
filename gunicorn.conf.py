"""gunicorn 配置：用 uvicorn worker 跑多进程（Phase 4 高并发）。

核心思想：pre-fork 出 N 个 worker 进程，每个进程内跑一个 uvicorn 事件循环，
从而用满多核（绕过 Python GIL 的单线程瓶颈）。

启动：gunicorn -c gunicorn.conf.py auth_middleware.main:app
"""

import multiprocessing

bind = "0.0.0.0:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 30
graceful_timeout = 30
keepalive = 5
# 收到 HUP 时逐步替换 worker，不丢在途连接（滚动重启）
reload = False
