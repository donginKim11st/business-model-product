"""1002 카탈로그 비정형 추출 — 재개 가능 배치 러너 (풀스테어).
패키지 = 1차(base) → 2차(용량 델타) → 3차(개수 델타), 단독 = 1차만.
입력: work_units.jsonl · 패키지 변형구조: trees_food.jsonl · 출력: insights_1002.jsonl (단위당 1줄 append).
이미 처리한 uid는 건너뜀(재개). 각 줄에 출처+인용(block.evidence) 포함.
YouTube는 디커플링됨 — 메인 배치는 네이버(+다나와)만, 무정지. 유튜브는 db/youtube_backfill.py가 별도 적재.
환경: INSIGHT_MODEL=gpt-4o-mini(기본) DANAWA_OFF=1 LIMIT=N ONLY=standalone|package OUT=..
       INSIGHT_INLINE_YT=1(예전처럼 인라인 유튜브 — 쿼터 소진 시 중단; 비권장)
사용: <키 export 후> python3 run_batch.py
"""
import os, sys, json, time
from collections import Counter

os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import naver_review_geo as nrg
from openai import OpenAI as RealOpenAI

USAGE = Counter()
PRICES = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}
YT_DEAD = False


class QuotaStop(Exception):
    """YouTube 일일 쿼터 소진 — 오늘 배치 중단(이 단위 미기록, 다음날 재개)."""


def _is_quota_err(e):
    r = getattr(e, "response", None)
    txt = (getattr(r, "text", "") or "") + " " + str(e)
    return ("quotaexceeded" in txt.lower() or "quota exceeded" in txt.lower()
            or "dailylimitexceeded" in txt.lower())


def make_client():
    c = RealOpenAI()
    orig = c.chat.completions.parse
    def wrapped(*a, **k):
        r = orig(*a, **k)
        u = getattr(r, "usage", None)
        if u:
            m = k.get("model", "?")
            USAGE[(m, "in")] += getattr(u, "prompt_tokens", 0) or 0
            USAGE[(m, "out")] += getattr(u, "completion_tokens", 0) or 0
        return r
    c.chat.completions.parse = wrapped
    return c


def usd():
    t = 0.0
    for (m, io), v in USAGE.items():
        pi, po = PRICES.get(m, (0, 0))
        t += v / 1e6 * (pi if io == "in" else po)
    return t


def _is_blog_quota(e):
    """네이버 블로그 검색 429(초당/일일 호출 제한). search_blog 의 raise_for_status 가 던짐."""
    r = getattr(e, "response", None)
    if r is not None and getattr(r, "status_code", None) == 429:
        return True
    s = str(e).lower()
    return "429" in s or "too many requests" in s


def collect(kw, nid, nsecret, ytk=None, use_yt=False, raise_blog_quota=False):
    """메인 추출 경로 = 네이버 블로그(+선택 다나와)만. YouTube 는 디커플링됐다.

    YouTube 는 일일 쿼터(search.list=100 units)가 작아 24k 번들엔 수개월이 걸린다 → 메인 배치를
    막으면 안 된다. 그래서 적재 product 는 youtube.status='pending' 으로 두고, db/youtube_backfill.py
    가 쿼터 한도 내에서 product.youtube 필드를 별도로 '천천히' 적재한다(출처+유튜브 전용 인사이트).

    (escape hatch) INSIGHT_INLINE_YT=1 이고 use_yt=True 이고 ytk 가 있을 때만 예전처럼 인라인으로
    YouTube 를 붙인다 — 이 경우에 한해 쿼터 소진 시 QuotaStop 으로 중단한다(기본 경로는 무정지)."""
    global YT_DEAD
    items = []
    try:
        items += nrg.search_blog(kw, nid, nsecret, display=50)
    except Exception as e:
        if raise_blog_quota and _is_blog_quota(e):
            raise QuotaStop()      # 호출자가 '쿼터 → 미기록(큐 유지)'로 처리
        print(f"    [blog 오류] {e}", flush=True)
    if os.environ.get("INSIGHT_INLINE_YT") and ytk and not YT_DEAD:
        try:
            items += nrg.collect_youtube(kw, ytk, n_videos=3, n_comments=50)
        except Exception as e:
            if _is_quota_err(e):
                YT_DEAD = True
                raise QuotaStop()
            print(f"    [yt 오류] {e}", flush=True)
    if not os.environ.get("DANAWA_OFF"):
        try:
            items += nrg.collect_danawa(kw)
        except Exception as e:
            print(f"    [danawa 오류] {e}", flush=True)
    for it in items:
        it["is_ad"] = nrg.is_ad(it)
        it["ad_signals"] = nrg.ad_signals(it)
    return items


def keyof(it):
    return (it.get("link", "") + "|" + (it.get("desc", "") or "")[:80]).strip()


def extract_full(kw, items, llm):
    if not items:
        return None
    sourced, context, aspverd, id_map, _ = nrg.extract_sourced_insights(kw, items, llm)
    return nrg.build_sourced_block(sourced, context, aspverd, id_map, items)


# ── 변형 델타(2차/3차) — build_tree.py 이식 ────────────────────────────────
def parent_points_text(blocks):
    seen, lines = set(), []
    for b in blocks:
        if not b:
            continue
        fb = nrg._flatten_block(b)
        cand = []
        if fb.get("overall_recommendation"):
            cand.append(fb["overall_recommendation"])
        cand += [f"강점: {s}" for s in fb.get("strengths", [])]
        cand += [f"약점: {s}" for s in fb.get("weaknesses", [])]
        for dim, ps in (fb.get("key_aspects") or {}).items():
            cand += [f"{dim}: {p}" for p in ps]
        cand += [f"FAQ: {f['question']}" for f in fb.get("faqs", [])]
        for c in cand:
            if c not in seen:
                seen.add(c); lines.append("- " + c)
    return "\n".join(lines[:40])


_AXIS_RULE = {
    "용량": """[이 노드의 축 = 용량] 오직 '이 용량값' 때문에 생기는 것만 뽑으세요(개수·묶음 관련은 하위 노드에서 다룸).
- 허용: 대용량/소용량성, 가정용·휴대용 적합, 보관 부피·냉장고 자리, 1회 소비량, 개봉 후 소진/변질.""",
    "개수": """[이 노드의 축 = 개수(묶음 수량)] 오직 '묶음 개수' 때문에 생기는 것만 뽑으세요.
- 허용: 개당 단가·묶음 가성비, 대량구매·비축 편의, 묶음 보관 공간·무게, 재구매·소진 주기, 배송 무게.
- 절대 금지(이미 상위 '용량' 노드 것): 용량값 자체, 대용량/소용량성, 가정용 적합, 물맛·품질·원산지. '대용량 생수', '가정에서 보관', '온 가족' 류는 전부 용량 노드 재탕이니 버리세요.""",
}


def delta_header(kw, parent_points, variant_value="", axis=""):
    vex = variant_value or "2L·12개"
    axis_rule = _AXIS_RULE.get(axis, "")
    return f"""[이 노드는 '변형(variant) 노드' — '{kw}']
'{kw}'는 상위 상품의 특정 변형(용량/개수/옵션)입니다. 임무는 이 변형 '때문에' 새로 생기는 차이(델타)만 뽑는 것. 모든 변형에 공통인 일반 내용을 여기서 또 뽑으면 '오답(누수)'입니다.

{axis_rule}

[상위 노드에 이미 있는 포인트 — 모든 변형 공통, 절대 재추출 금지]
{parent_points or '(상위 추출 결과 없음)'}

[배제 규칙 — 다른 모든 규칙보다 우선]
1) 위 목록의 어떤 항목과 '의미가 같으면'(단어·표현이 달라도, 동의어·재진술·요약·부분중복 포함) 버립니다.
2) 부모 내용에 변형값({vex})만 덧붙인 재진술은 델타가 아닙니다 → 버립니다.
3) [일반 내용 = 누수] 위 목록에 없더라도, '다른 용량/개수에도 똑같이 참'인 두루뭉술한 내용은 변형 특화가 아니라 누수입니다 → 버립니다.
4) [클레임 우선] 주제가 부모와 같아도, 이 변형 때문에 결론·정도·방향이 '달라지면' 진짜 델타이니 살립니다.
5) [중복 금지] 같은 내용을 표현만 바꿔 두 번 이상 넣지 마세요.
6) [동점이면 배제] 변형 특화인지 일반/부모인지 애매하면 버립니다.
7) [빈 배열 정상] 변형 특화 차이는 드뭅니다 — 특히 개수 노드는 비어 있는 게 흔합니다. 억지로 채우지 마세요.

[양성 테스트] "이 점은 이 축({axis})의 값이 달랐다면 성립하지 않거나 달라졌을 것"이 명백히 참일 때만 남깁니다."""


def _snip_section(snippets):
    return f"\n\n--- 수집 데이터 ---\n{snippets}\n--- 끝 ---\n"


ASPECT_TASK = """[이 호출] 객관속성(aspect)·평가(verdict)·flags 중 '이 변형 때문에 생기는 차이'만.
- 주 운반자: aspect_size(용량·묶음크기), aspect_price_range(1개당 단가·가성비), aspect_care(대용량 개봉후 보관/소용량 휴대보관).
- 맛·질감·향·스펙·원산지·성분 등 모든 변형 공통은 비웁니다(부모에 있음).
- strengths/weaknesses는 '변형 때문에 생긴' 강·약점만. overall_recommendation: 변형 특화 근거 없으면 빈 문자열."""

CONTEXT_TASK = """[이 호출] 누가/언제/어디서/왜/선물/호환 중 '이 변형이라서 달라지는 사용맥락'만.
- 대용량 → who_household/where_place/when_frequency. 소용량 → who/when_scene/where_place.
- 다개수 → why_positive_goal(비축)/when_frequency/where_place/gift_recipient. 공통 맥락은 비웁니다."""

FAQ_TASK = """[이 호출] '이 변형(용량/개수/옵션) 때문에 새로 생기는 질문'만 FAQ로.
- 허용: "2L는 한 박스에 몇 병?", "낱개 가격?", "다개입 묶음 보관?".
- [전면 금지 — 공통이라 누수] 유통기한, 보관법, 배송, 구매처, 맛, 원산지, 진위 등.
- answer_evidence는 단정적 서술에서만(질문 문장 금지). 근거 없으면 빈 배열."""


def _parse(schema, content, llm):
    resp = llm.chat.completions.parse(
        model=nrg.MODEL, temperature=0,
        messages=[{"role": "user", "content": content}], response_format=schema)
    return resp.choices[0].message.parsed


def extract_delta(kw, items, parent_points, variant_value, axis, llm):
    if not items:
        return None
    snippets, id_map, _ = nrg._build_sourced_snippets(items)
    hdr = delta_header(kw, parent_points, variant_value, axis)
    snip = _snip_section(snippets)
    sourced = _parse(nrg.SourcedInsights, hdr + "\n\n" + FAQ_TASK + snip, llm)
    context = _parse(nrg.SourcedContext, hdr + "\n\n" + CONTEXT_TASK + "\n\n" + nrg._EVIDENCE_RULES + snip, llm)
    aspverd = _parse(nrg.SourcedAspectVerdict, hdr + "\n\n" + ASPECT_TASK + "\n\n" + nrg._EVIDENCE_RULES + snip, llm)
    return nrg.build_sourced_block(sourced, context, aspverd, id_map, items)


# ── 단위 처리 ──────────────────────────────────────────────────────────────
def process_standalone(kw, nid, nsecret, ytk, llm):
    items = collect(kw, nid, nsecret, ytk)
    if not items:
        return {"status": "no_reviews"}
    block = extract_full(kw, items, llm)
    return {"block": block, "n_items": len(items), "verification": (block or {}).get("verification")}


def process_package(tree, nid, nsecret, ytk, llm):
    """패키지 풀스테어: 1차(base) → 2차(용량) → 3차(개수). 신선한 델타 트리 반환."""
    base = tree["base"]
    items1 = collect(base, nid, nsecret, ytk)
    if not items1:
        return {"status": "no_reviews"}
    keys1 = {keyof(it) for it in items1}
    block1 = extract_full(base, items1, llm)
    pp1 = parent_points_text([block1])
    total = len(items1)
    out_sizes = []
    for s in (tree.get("sizes") or []):
        sval = s.get("value") or ""
        if sval == "용량 단일":
            sval = ""
        block2, pp2 = None, pp1
        if sval:
            kw2 = f"{base} {sval}"
            items2 = collect(kw2, nid, nsecret, ytk, use_yt=False); total += len(items2)  # 2차: YT 검색 생략
            block2 = extract_delta(kw2, items2, pp1, sval, "용량", llm)
            pp2 = parent_points_text([block1, block2])
        out_counts = []
        for c in (s.get("counts") or []):
            cval = c.get("count") or ""
            block3 = None
            if cval and cval != "단품":
                kw3 = f"{base} {sval} {cval}".replace("  ", " ").strip()
                items3 = collect(kw3, nid, nsecret, ytk, use_yt=False); total += len(items3)  # 3차: YT 검색 생략
                block3 = extract_delta(kw3, items3, pp2, f"{sval} {cval}".strip(), "개수", llm)
            out_counts.append({"count": cval, "disp": c.get("disp"),
                               "ctlg_no": c.get("ctlg_no"), "block": block3})
        out_sizes.append({"value": s.get("value"), "block": block2, "counts": out_counts})
    return {"block": block1, "n_items": total,
            "verification": (block1 or {}).get("verification"),
            "tree": {"sizes": out_sizes}}


def main():
    nid = os.environ.get("NAVER_CLIENT_ID"); nsecret = os.environ.get("NAVER_CLIENT_SECRET")
    if not (nid and nsecret):
        sys.exit("NAVER_CLIENT_ID/SECRET 필요")
    ytk = None if os.environ.get("NO_YT") else os.environ.get("YOUTUBE_API_KEY")
    if not ytk and not os.environ.get("NO_YT"):
        print("⚠ YOUTUBE_API_KEY 없음 — 네이버만 수집됩니다.", flush=True)
    llm = make_client()
    OUT = os.environ.get("OUT", "insights_1002.jsonl")
    only = os.environ.get("ONLY"); limit = int(os.environ.get("LIMIT", "0"))

    units = [json.loads(l) for l in open("work_units.jsonl", encoding="utf-8")]
    if only:
        units = [u for u in units if u["type"] == only]
    trees = {}
    for l in open("trees_food.jsonl", encoding="utf-8"):
        d = json.loads(l); trees[f"P{d['bndl_grp']}"] = d
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try: done.add(json.loads(l)["uid"])
            except Exception: pass
    todo = [u for u in units if u["uid"] not in done]
    if limit: todo = todo[:limit]
    print(f"대상 {len(units):,} · 완료 {len(done):,} · 이번 처리 {len(todo):,}"
          f" · 모델 {os.environ['INSIGHT_MODEL']} · YT {'OFF' if not ytk else 'ON'}"
          f" · Danawa {'OFF' if os.environ.get('DANAWA_OFF') else 'ON'} · 풀스테어", flush=True)

    t0 = time.time(); n_ok = n_err = 0; quota_stop = False
    fout = open(OUT, "a", encoding="utf-8")
    for i, u in enumerate(todo, 1):
        kw = u["keyword"]; ts = time.time()
        rec = {"uid": u["uid"], "type": u["type"], "keyword": kw}
        try:
            if u["type"] == "package":
                tree = trees.get(u["uid"])
                res = process_package(tree, nid, nsecret, ytk, llm) if tree \
                    else process_standalone(kw, nid, nsecret, ytk, llm)
            else:
                res = process_standalone(kw, nid, nsecret, ytk, llm)
        except QuotaStop:
            quota_stop = True
            print(f"\n■ YouTube 일일 쿼터 소진 → 오늘 배치 중단. 처리 {i-1}건. 다음 실행 시 자동 재개.", flush=True)
            break
        except Exception as e:
            res = {"status": "error", "error": str(e)[:200]}; n_err += 1
        if res.get("status") == "error":
            pass
        else:
            n_ok += 1
        rec.update(res)
        rec["elapsed"] = round(time.time() - ts, 1)
        fout.write(json.dumps(rec, ensure_ascii=False) + "\n"); fout.flush()
        if i % 5 == 0:
            rate = i / (time.time() - t0) * 3600
            print(f"  [{i}/{len(todo)}] ok {n_ok} err {n_err} · {rate:,.0f}건/시 "
                  f"· 누적 ${usd():.3f} · 평균 {(time.time()-t0)/i:.1f}s/건", flush=True)
    fout.close()
    print(f"\n{'쿼터중단' if quota_stop else '배치완료'} · ok {n_ok} · 오류 {n_err} "
          f"· 총비용 ${usd():.4f} (≈{usd()*1380:,.0f}원) · {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
