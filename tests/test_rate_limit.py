"""Phase 4：令牌桶限流纯逻辑单测（不依赖 Redis）。

注意：compute_token_bucket 的参数顺序刻意与 consume()/Lua 脚本保持一致，
即 (tokens, ts, now, rate, capacity, requested)，调用时务必按此顺序传参，
避免把 capacity / rate 写反（历史上曾因此出现限流失效）。
"""

from auth_middleware.core.rate_limit import compute_token_bucket


def test_full_bucket_allows_one():
    # 满桶(10) + rate=1/s，t=1 时仍满，消费 1 个 -> 剩 9
    allowed, remaining = compute_token_bucket(10, 0.0, 1.0, 1, 10)
    assert allowed is True
    assert remaining == 9


def test_empty_bucket_rejects():
    # 桶空于 t=0 且 now==ts（无时间流逝->不补充），应拒绝
    allowed, remaining = compute_token_bucket(0, 0.0, 0.0, 1, 10)
    assert allowed is False
    assert remaining == 0


def test_refill_over_time():
    # 桶空于 t=0，rate=1/s，到 t=5 应补充 5 个（上限容量 10）
    allowed, remaining = compute_token_bucket(0, 0.0, 5.0, 1, 10)
    assert allowed is True
    assert remaining == 4


def test_first_call_initializes_to_capacity():
    # tokens=None 表示新桶，初始化为 capacity 并立即消费 1
    allowed, remaining = compute_token_bucket(None, 0.0, 1.0, 1, 10)
    assert allowed is True
    assert remaining == 9


def test_bucket_caps_at_capacity():
    # 长时间未请求后，令牌不应超出容量上限
    allowed, remaining = compute_token_bucket(0, 0.0, 1000.0, 1, 10)
    assert allowed is True
    assert remaining == 9  # min(10, 0 + 1000*1) = 10, 再 -1 = 9


def test_request_more_than_remaining_rejected():
    # 桶里只剩 3 个，一次请求 5 个 -> 拒绝且不扣减
    allowed, remaining = compute_token_bucket(3, 0.0, 0.0, 1, 10, requested=5)
    assert allowed is False
    assert remaining == 3


def test_capacity_rate_order_matches_consume():
    """回归测试：rate 与 capacity 的顺序必须与 consume() 一致。

    consume(key, rate, capacity, requested) 透传给 Lua(rate, capacity, ...)，
    这里用同样的 (rate, capacity) 顺序调用纯函数，保证二者语义等价。
    """
    # 与 consume 调用约定一致：rate=2/s, capacity=4
    allowed, remaining = compute_token_bucket(4, 10.0, 11.0, 2, 4)
    # 经过 1 秒补充 2 个（仍受容量 4 限制），消费 1 -> 剩 3
    assert allowed is True
    assert remaining == 3
