import naver_review_geo as nrg
from insight_engine import batch_openai as bo

ITEMS = [{"title": "발볼 넉넉하고 좋아요", "desc": "쿠션 훌륭합니다"},
         {"title": "가볍고 편함", "desc": "장거리도 무난했어요"}]


def _fake_output_line(ctlg, key, model_cls):
    # 스키마의 빈 인스턴스를 JSON으로 (실제 Batch output.body 형태 모사)
    inst = model_cls.model_construct() if hasattr(model_cls, "model_construct") else model_cls()
    return {"custom_id": f"{ctlg}|{key}",
            "response": {"body": {"choices": [{"message": {"content": inst.model_dump_json()}}]}}}


def test_parse_output_line_returns_ctlg_key_model():
    # sourced 스키마: faqs 필수 → 빈 배열로 유효 JSON 구성
    content = nrg.SourcedInsights(faqs=[]).model_dump_json()
    line = {"custom_id": "CTLG9|sourced",
            "response": {"body": {"choices": [{"message": {"content": content}}]}}}
    ctlg, key, parsed = bo.parse_output_line(line)
    assert ctlg == "CTLG9" and key == "sourced"
    assert isinstance(parsed, nrg.SourcedInsights)


def test_regroup_by_sku_drops_incomplete():
    parsed = [("A", "sourced", object()), ("A", "context", object()),
              ("A", "aspect", object()), ("B", "sourced", object())]
    grouped = bo.regroup_by_sku(parsed)
    assert set(grouped) == {"A"}  # B는 2/3 미만 → 제외
    assert set(grouped["A"]) == {"sourced", "context", "aspect"}


def test_assemble_insight_produces_insight_with_run_meta():
    # SourcedContext와 SourcedAspectVerdict는 모든 List 필드 필수 — 명시적으로 빈 리스트 전달
    ctx = nrg.SourcedContext(
        who_age=[], who_gender=[], who_occupation=[], who_household=[], who_body_type=[],
        who_health=[], who_taste_pref=[], who_lifestyle=[],
        when_scene=[], when_season=[], when_event=[], when_time_of_day=[], when_frequency=[],
        where_place=[], why_positive_goal=[], why_negative_concern=[], why_workload=[],
        gift_recipient=[], compat_device=[], compat_os=[], compat_standard=[]
    )
    aspect = nrg.SourcedAspectVerdict(
        aspect_taste=[], aspect_texture=[], aspect_spec=[], aspect_size=[], aspect_care=[],
        aspect_price_range=[], aspect_routine=[], aspect_sensory=[],
        compare_spec=[], compare_brand=[], compare_alternative_when=[],
        trust_clinical=[], trust_authenticity=[], trust_origin=[], trust_certification=[],
        strengths=[], weaknesses=[], overall_recommendation="",
        is_direct_import=False, is_gift_set=False, is_premium=False, is_eco_friendly=False
    )
    trio = {"sourced": nrg.SourcedInsights(faqs=[]),
            "context": ctx,
            "aspect": aspect}
    ins = bo.assemble_insight(ITEMS, trio, "gpt-4o-mini")
    assert "dims" in ins and "faqs" in ins
    assert ins["run_meta"]["execution"] == "openai_batch"
    assert ins["run_meta"]["model"] == "gpt-4o-mini"
