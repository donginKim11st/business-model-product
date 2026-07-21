import hashlib
import naver_review_geo as nrg
from insight_engine.types import EngineConfig
from insight_engine import versioning


def test_prompt_version_is_hash_of_three_prompts():
    combined = (nrg.EXTRACT_SOURCED_PROMPT + nrg.EXTRACT_CONTEXT_PROMPT
                + nrg.EXTRACT_ASPECT_VERDICT_PROMPT).encode("utf-8")
    expected = hashlib.sha256(combined).hexdigest()[:12]
    assert versioning.prompt_version() == expected


def test_build_run_meta_has_all_keys_and_is_deterministic():
    cfg = EngineConfig(model="gpt-4o-mini", lexicon_version="v1")
    m1 = versioning.build_run_meta(cfg)
    m2 = versioning.build_run_meta(cfg)
    assert set(m1) == {"engine_version", "prompt_version", "model",
                       "lexicon_version", "source_config", "extracted_at"}
    assert m1["prompt_version"] == m2["prompt_version"]
    assert m1["model"] == "gpt-4o-mini"
    assert m1["lexicon_version"] == "v1"


def test_config_change_changes_run_meta_model():
    a = versioning.build_run_meta(EngineConfig(model="gpt-4o-mini"))
    b = versioning.build_run_meta(EngineConfig(model="gpt-4o"))
    assert a["model"] != b["model"]
