"""OpenAI Batch API 백엔드 — 순수 로직(요청빌드·스키마변환·출력파싱·조립). I/O 없음."""
from openai.lib._parsing._completions import type_to_response_format_param
import sys, os

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
