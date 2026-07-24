"""生产部署入口：把 Alembic 迁移应用到当前数据库。

在容器/CI 启动应用前调用，确保 schema 与代码版本一致：
    python scripts/migrate.py
等价命令：
    alembic upgrade head

连接串由 alembic/env.py 从 AUTH_DATABASE_URL 读取，无需在此指定。
"""

import subprocess
import sys


def main() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
