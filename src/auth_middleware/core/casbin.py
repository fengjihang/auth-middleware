"""Casbin 授权引擎：策略与代码解耦的核心。

- casbin_model.conf 定义 RBAC 模型（请求/策略/匹配规则）
- casbin_policy.csv 定义具体角色能做什么（改权限只改这个文件，不动代码）

enforcer 是进程级单例：模型+策略一次性加载进内存，enforce() 是纯内存运算，
不碰数据库、不碰网络，所以可以直接在 FastAPI 的 async 路由里同步调用。
"""

from pathlib import Path

from casbin import Enforcer

# 这两个文件放在包根目录（src/auth_middleware/），用绝对路径定位，避免相对路径歧义
_BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = _BASE_DIR / "casbin_model.conf"
POLICY_PATH = _BASE_DIR / "casbin_policy.csv"

# 单例 enforcer：整个进程加载一次
enforcer = Enforcer(str(MODEL_PATH), str(POLICY_PATH))


def enforce(role: str, obj: str, act: str) -> bool:
    """判断某角色是否对资源 obj 有 act 权限。返回 Python bool。"""
    return bool(enforcer.enforce(role, obj, act))
