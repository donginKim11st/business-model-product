"""OpenAI Batch API 백엔드 — 순수 로직(요청빌드·스키마변환·출력파싱·조립). I/O 없음."""
from openai.lib._parsing._completions import type_to_response_format_param
import sys, os, json

import naver_review_geo as nrg

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "db"))
import catalog_insight_backfill as cib  # to_insight 재사용
from insight_engine.versioning import build_run_meta
from insight_engine.types import EngineConfig

SCHEMAS = {
    "sourced": (nrg.SourcedInsights, nrg.EXTRACT_SOURCED_PROMPT),
    "context": (nrg.SourcedContext, nrg.EXTRACT_CONTEXT_PROMPT),
    "aspect": (nrg.SourcedAspectVerdict, nrg.EXTRACT_ASPECT_VERDICT_PROMPT),
}


def response_format_for(schema_key: str) -> dict:
    model_cls, _ = SCHEMAS[schema_key]
    return type_to_response_format_param(model_cls)


def build_request_lines(ctlg_no, keyword: str, items: list, model: str) -> list:
    # 실데이터 ctlg_no는 int일 수 있다 → custom_id(문자열)용으로 str 강제.
    # (원래 타입은 staging/Mongo 매칭에서 보존; 여긴 custom_id 표기만)
    ctlg_no = str(ctlg_no)
    if "|" in ctlg_no:
        raise ValueError(f"ctlg_no에 '|' 불가(custom_id 구분자 충돌): {ctlg_no}")
    snippets, _id_map, _dropped = nrg._build_sourced_snippets(items)
    out = []
    for key, (_cls, prompt) in SCHEMAS.items():
        out.append({
            "custom_id": f"{ctlg_no}|{key}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user",
                              "content": prompt.format(keyword=keyword, snippets=snippets)}],
                "response_format": response_format_for(key),
            },
        })
    return out


def chunk_requests(lines: list, max_per_batch: int = 40000) -> list:
    return [lines[i:i + max_per_batch] for i in range(0, len(lines), max_per_batch)]


def parse_output_line(line: dict):
    cid = line["custom_id"]
    ctlg, key = cid.rsplit("|", 1)
    model_cls, _ = SCHEMAS[key]
    content = line["response"]["body"]["choices"][0]["message"]["content"]
    return ctlg, key, model_cls.model_validate_json(content)


def regroup_by_sku(parsed: list) -> dict:
    groups: dict = {}
    for ctlg, key, model in parsed:
        groups.setdefault(ctlg, {})[key] = model
    return {c: t for c, t in groups.items() if set(t) == set(SCHEMAS)}


def assemble_insight(items: list, trio: dict, model: str) -> dict:
    snippets, id_map, _dropped = nrg._build_sourced_snippets(items)
    block = nrg.build_sourced_block(trio["sourced"], trio["context"],
                                    trio["aspect"], id_map, items)
    ins = cib.to_insight(block, len(items))
    ins["run_meta"] = build_run_meta(EngineConfig(model=model))
    ins["run_meta"]["execution"] = "openai_batch"
    return ins


def chunk_by_size(lines, max_bytes=180_000_000, max_count=50_000):
    """OpenAI Batch 입력 파일 한도(200MB/파일·50k요청)에 맞춰 바이트+건수 이중 기준 분할.
    각 요청에 네이버 스니펫이 통째 들어가 요청 건수만으론 파일이 200MB를 넘길 수 있다.
    한 줄이 max_bytes보다 커도 자기 청크로 격리(드롭·무한루프 방지)."""
    chunks, cur, cur_bytes = [], [], 0
    for l in lines:
        sz = len(json.dumps(l, ensure_ascii=False).encode("utf-8")) + 1  # +개행
        if cur and (cur_bytes + sz > max_bytes or len(cur) >= max_count):
            chunks.append(cur)
            cur, cur_bytes = [], 0
        cur.append(l)
        cur_bytes += sz
    if cur:
        chunks.append(cur)
    return chunks
