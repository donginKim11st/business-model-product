#!/usr/bin/env python3
"""
2번(OpenAI): 엔진이 한 덩어리로 묶은 클러스터 안의 잔여 sub-variant(리페어 vs 톤업
등)를 LLM이 분리. 클러스터당 1콜로 대표 제목들을 '진짜 다른 제품'으로 분할.

    export OPENAI_API_KEY=...           # business-model 와 동일 키
    python3 llm_split.py                # 기본: 혼합 의심 클러스터만, 최대 12개
    python3 llm_split.py 20             # 상위 20개까지

의존성 없음(stdlib urllib). 모델: env OPENAI_MODEL (기본 gpt-4o-mini).
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pig.blocking import HybridBlocker
from pig.resolve import resolve
from naver_resolve import load_records

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
URL = "https://api.openai.com/v1/chat/completions"


def openai_partition(titles, api_key):
    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(titles))
    prompt = (
        "다음은 한 제품으로 묶인 마켓 리스팅 제목들입니다(같은 브랜드/카테고리/용량). "
        "인덱스를 '같은 SKU'끼리 그룹으로 묶으세요.\n"
        "● 무시할 노이즈(같은 그룹 유지): 마케팅 문구(어워드위너·정품·베스트·기획·무료배송), "
        "수량(2개·3개세트·1+1·X2), 판매처, '/' 뒤 효능 설명, 띄어쓰기·중복 브랜드.\n"
        "● 분리 기준(다른 그룹): 세부 라인(리페어 vs 톤업, 슈퍼마일드 vs UV엑스퍼트), "
        "제형(에멀전 vs 스킨 vs 크림 vs 에센스), 용량, 'EX' 등 버전, '택1', "
        "여러 제품 묶음 세트(토너+로션 등).\n"
        "● 같은 라인+제형+용량이면 마케팅·수량 차이가 있어도 반드시 같은 그룹.\n\n"
        f"{numbered}\n\n"
        '오직 JSON만: {"groups": [[0,2],[1], ...]}'
    )
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read())
    text = payload["choices"][0]["message"]["content"].strip()
    groups = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("groups", [])
    # ensure every index is covered (leftovers -> singletons)
    seen = {i for g in groups for i in g}
    for i in range(len(titles)):
        if i not in seen:
            groups.append([i])
    return groups


def main():
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("✗ OPENAI_API_KEY 없음. export OPENAI_API_KEY=... 후 실행하세요.")
        sys.exit(1)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 12

    recs = load_records()
    by_id = {r["id"]: r for r in recs}
    run = resolve(recs, HybridBlocker(), cluster_guard=True)
    before_multi = [c for c in run["clusters"] if len(c) > 1]

    # 후보: 다중몰 + 대표 제목 2개 이상(혼합 가능). 대표 제목 많은 순(혼합 가능성↑).
    cands = []
    for cl in before_multi:
        reps = sorted({by_id[i]["title"] for i in cl})
        if len(reps) >= 2:
            cands.append((cl, reps))
    cands.sort(key=lambda x: -len(x[1]))
    processed = cands[:limit]
    print(f"엔진 클러스터 {len(run['clusters'])} (다중몰 {len(before_multi)}). "
          f"분할 후보 {len(cands)} 중 {len(processed)}개 LLM 검토 (모델 {MODEL})")
    print("=" * 70)

    new_clusters = []
    skipped = [cl for cl, _ in cands[limit:]]
    handled_ids = set()
    split_count = 0
    for cl, reps in processed:
        handled_ids.update(cl)
        rep_idx = {t: k for k, t in enumerate(reps)}
        try:
            groups = openai_partition(reps, key)
        except Exception as e:
            print(f"  (API 오류, 분할 건너뜀: {e})")
            new_clusters.append(cl)
            continue
        # rep -> group id
        rep_group = {}
        for gid, g in enumerate(groups):
            for ri in g:
                if 0 <= ri < len(reps):
                    rep_group[reps[ri]] = gid
        buckets = defaultdict(list)
        for mid in cl:
            buckets[rep_group.get(by_id[mid]["title"], 0)].append(mid)
        subs = list(buckets.values())
        new_clusters.extend(subs)
        if len(subs) > 1:
            split_count += 1
            print(f"\n[분할] {len(cl)}리스팅 → {len(subs)}제품")
            for sub in subs:
                ts = sorted({by_id[i]["title"] for i in sub})
                print(f"   • {ts[0][:46]}" + (f"  (+{len(ts)-1})" if len(ts) > 1 else ""))

    # 미처리 + 단일 클러스터 그대로
    for cl in run["clusters"]:
        if not (set(cl) & handled_ids):
            new_clusters.append(cl)

    print("\n" + "=" * 70)
    print(f"분할 전 {len(run['clusters'])} → 분할 후 {len(new_clusters)} 클러스터 "
          f"(LLM이 {split_count}개 클러스터를 쪼갬)")
    if skipped:
        print(f"※ 비용 한도로 {len(skipped)}개 후보 미검토 (python3 llm_split.py {len(cands)} 로 전체 실행)")
    with open(os.path.join(HERE, "outputs", "llm_split.json"), "w", encoding="utf-8") as f:
        json.dump({"model": MODEL, "before": len(run["clusters"]), "after": len(new_clusters),
                   "split_clusters": split_count, "skipped": len(skipped)}, f, ensure_ascii=False, indent=2)
    # persist full final clustering so the dossier can regenerate without re-calling the LLM
    with open(os.path.join(HERE, "outputs", "llm_clusters.json"), "w", encoding="utf-8") as f:
        json.dump({"model": MODEL, "clusters": [sorted(c) for c in new_clusters]},
                  f, ensure_ascii=False, indent=2)
    print("저장: outputs/llm_split.json, llm_clusters.json")


if __name__ == "__main__":
    main()
