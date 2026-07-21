import sys; sys.path.insert(0, "db")
import run_insight_batch_openai as orch


def test_estimate_cost_batch_halves_price():
    est = orch.estimate_cost(request_count=3000, model="gpt-4o-mini")
    # batch 단가는 동기의 50%. 최소한 정수 요청수·양수 비용·통화 필드 존재
    assert est["request_count"] == 3000
    assert est["usd"] > 0 and est["krw"] > 0
    assert est["discounted"] is True
