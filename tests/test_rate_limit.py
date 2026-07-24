"""Phase 4：令牌桶限流纯逻辑单测（不依赖 Redis）。"""

from auth_middleware.core.rate_limit import compute_token_bucket


def test_full_bucket_allows_one():
    allowed, remaining = compute_token_bucket(10, 0.0, 1.0, 10, 1)
    assert allowed is True
    assert remaining == 9


def test_empty_bucket_rejects():
    # 桶空于 t=0 且 now==ts（无时间流逝→不补充），应拒绝
    allowed, remaining = compute_token_bucket(0, 0.0, 0.0, 10, 1)
    assert allowed is False
    assert remaining == 0


def test_refill_over_time():
    # 桶空于 t=0，rate=1/s，到 t=5 应补充 5 个（上限容量 10）
    allowed, remaining = compute_token_bucket(0, 0.0, 5.0, 10, 1)
    assert allowed is True
    assert remaining == 4


def test_first_call_initializes_to_capacity():
    # tokens=None 表示新桶，初始化为 capacity 并立即消费 1
    allowed, remaining = compute_token_bucket(None, 0.0, 1.0, 10, 1)
    assert allowed is True
    assert remaining == 9
