import pytest
import naver_review_geo as nrg
from insight_engine import batch_openai as bo

ITEMS = [{"title": "발볼 넉넉하고 좋아요", "desc": "쿠션 훌륭"},
         {"title": "가볍고 편함", "desc": "장거리도 무난"}]


def test_build_request_lines_three_schemas_with_custom_ids():
    lines = bo.build_request_lines("CTLG123", "아식스 젤카야노", ITEMS, "gpt-4o-mini")
    assert len(lines) == 3
    cids = {l["custom_id"] for l in lines}
    assert cids == {"CTLG123|sourced", "CTLG123|context", "CTLG123|aspect"}
    for l in lines:
        assert l["method"] == "POST" and l["url"] == "/v1/chat/completions"
        b = l["body"]
        assert b["model"] == "gpt-4o-mini" and b["temperature"] == 0
        assert b["messages"][0]["role"] == "user"
        assert "아식스 젤카야노" in b["messages"][0]["content"]
        assert b["response_format"]["type"] == "json_schema"


def test_same_snippets_across_three_calls():
    lines = bo.build_request_lines("C1", "kw", ITEMS, "gpt-4o-mini")
    snips = set()
    for l in lines:
        c = l["body"]["messages"][0]["content"]
        snips.add(c.split("--- 수집 데이터 ---")[1].split("--- 끝 ---")[0])
    # 세 콜의 snippets(수집데이터 부분)가 동일해야 id_map 일관
    assert len(snips) == 1


def test_pipe_in_ctlg_no_raises():
    with pytest.raises(ValueError):
        bo.build_request_lines("C|X", "kw", ITEMS, "gpt-4o-mini")


def test_chunk_requests_splits_over_max():
    lines = [{"custom_id": f"c{i}"} for i in range(95)]
    chunks = bo.chunk_requests(lines, max_per_batch=40)
    assert [len(c) for c in chunks] == [40, 40, 15]
