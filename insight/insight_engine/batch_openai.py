"""OpenAI Batch API 백엔드 — 순수 로직(요청빌드·스키마변환·출력파싱·조립). I/O 없음."""
from openai.lib._parsing._completions import type_to_response_format_param

import naver_review_geo as nrg

# Normalize EXTRACT_SOURCED_PROMPT to remove trailing text after "--- 끝 ---"
# so all three prompts have consistent format for batch processing
_sourced_prompt = nrg.EXTRACT_SOURCED_PROMPT
if "\n\n답변 근거가" in _sourced_prompt:
    _sourced_prompt = _sourced_prompt.split("\n\n답변 근거가")[0] + "\n"

SCHEMAS = {
    "sourced": (nrg.SourcedInsights, _sourced_prompt),
    "context": (nrg.SourcedContext, nrg.EXTRACT_CONTEXT_PROMPT),
    "aspect": (nrg.SourcedAspectVerdict, nrg.EXTRACT_ASPECT_VERDICT_PROMPT),
}


def response_format_for(schema_key: str) -> dict:
    model_cls, _ = SCHEMAS[schema_key]
    return type_to_response_format_param(model_cls)


def build_request_lines(ctlg_no: str, keyword: str, items: list, model: str) -> list:
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
