#!/usr/bin/env python3
"""
스타스포츠 공식몰(starsportsmall.co.kr) **전수(全數)** 추출.
extract_starsports.py 어댑터의 파서를 재사용하고, 표본 캡(TARGET/PER_LEAF_CAP)·
페이지 상한(MAX_PAGES)을 제거해 전 카테고리 × 전 페이지를 크롤한다.

핵심 설계
  · 페이지 종료 판단: 리스트 페이지의 "총 N개의 제품" → pages=ceil(N/listsize).
    (사이트가 범위 밖 page를 마지막 페이지로 clamp 하므로 빈-페이지 감지는 불가.
     dup-guard: 직전 페이지와 guid 집합이 같으면 중단.)
  · 전 카테고리(220) 크롤 + 전역 guid dedup → 합집합 보장
    (parent 스포츠가 children을 일관되게 집계하지 않음: 축구=185 집계 / 야구=15 미집계).
  · 카테고리 라벨: leaf 우선(parent는 맨 마지막에 크롤해 leaf 라벨이 선점되게).
  · 재개: 리스트 단계 _starsports_guids.json(페이지마다 저장),
          상세 단계 _starsports_rows.jsonl(상세마다 append). 재실행 시 done guid skip.
  · >5000 이면 5000에서 collect 중단(notes 기록).
출력: outputs/extract_brand_starsports.csv (utf-8-sig, style_code 기준 dedup).
"""
import csv
import json
import math
import os
import sys
import time

import extract_starsports as ss

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
OUT_CSV = os.path.join(OUT, "extract_brand_starsports.csv")
GUIDS_JSON = os.path.join(OUT, "_starsports_guids.json")
ROWS_JSONL = os.path.join(OUT, "_starsports_rows.jsonl")

HEADER = ss.HEADER
LISTSIZE = ss.LISTSIZE          # 100
CAP = 5000


# ---------------------------------------------------------------- collect
def build_order(cmap):
    """nav 순서로 (cid,label,is_parent) 산출. 라벨은 leaf 우선, '스타'는 직전 leaf 상속."""
    order = []
    last_leaf = "기타"
    for cid, name in cmap.items():
        if not name or name == "스타":
            order.append((cid, last_leaf, False))     # 브랜드 서브탭 → leaf 상속
            continue
        if name in ss.PARENT_NAMES:
            order.append((cid, name, True))            # parent
            continue
        last_leaf = name
        order.append((cid, name, False))              # leaf(또는 브랜드 leaf)
    # parent 를 맨 뒤로: leaf 라벨이 guid 를 선점하도록
    non_parent = [t for t in order if not t[2]]
    parent = [t for t in order if t[2]]
    return non_parent + parent


def load_guids():
    if os.path.exists(GUIDS_JSON):
        with open(GUIDS_JSON, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("map", {}), set(map(int, d.get("cates_done", [])))
    return {}, set()


def save_guids(gmap, cates_done):
    tmp = GUIDS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"map": gmap, "cates_done": sorted(cates_done)}, f,
                  ensure_ascii=False)
    os.replace(tmp, GUIDS_JSON)


def collect(order):
    gmap, cates_done = load_guids()
    capped = False
    for cid, label, _is_parent in order:
        cid_i = int(cid)
        if cid_i in cates_done:
            continue
        if len(gmap) >= CAP:
            capped = True
            break
        try:
            guids1, total = ss.list_page(cid, 1)
        except Exception as e:  # noqa: BLE001
            print(f"[list] cate {cid} p1 실패: {e}", file=sys.stderr)
            continue
        pages = math.ceil(total / LISTSIZE) if total else 1
        prev = None
        for page in range(1, pages + 1):
            if page == 1:
                guids = guids1
            else:
                try:
                    guids, _ = ss.list_page(cid, page)
                except Exception as e:  # noqa: BLE001
                    print(f"[list] cate {cid} p{page} 실패: {e}", file=sys.stderr)
                    break
            cur = tuple(guids)
            if cur == prev:                 # clamp(범위 밖 → 마지막 페이지 반복) 중단
                break
            prev = cur
            for g in guids:
                if g not in gmap:
                    gmap[g] = label
                    if len(gmap) >= CAP:
                        capped = True
                        break
            if capped:
                break
            if len(guids) < LISTSIZE:       # 마지막 페이지
                break
            time.sleep(0.15)
        cates_done.add(cid_i)
        save_guids(gmap, cates_done)        # 카테고리(페이지 그룹)마다 저장
        if capped:
            break
        time.sleep(0.12)
    return gmap, capped


# ---------------------------------------------------------------- detail
def load_done():
    done = {}
    if os.path.exists(ROWS_JSONL):
        with open(ROWS_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("guid"):
                    done[str(r["guid"])] = r
    return done


def detail_phase(gmap):
    done = load_done()
    todo = [(g, lbl) for g, lbl in gmap.items() if g not in done]
    print(f"[detail] todo {len(todo)} / 전체 {len(gmap)} (이미 {len(done)})",
          file=sys.stderr)
    fail = 0
    with open(ROWS_JSONL, "a", encoding="utf-8") as f:
        for n, (guid, label) in enumerate(todo, 1):
            try:
                rec = ss.parse_detail(guid, label)
                rec["_gosi"] = bool(rec.get("_gosi"))
                rec["guid"] = guid
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"[detail] {guid} 실패: {e}", file=sys.stderr)
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if n % 50 == 0:
                print(f"[detail] {n}/{len(todo)} ...", file=sys.stderr)
            time.sleep(0.15)
    return load_done(), fail


# ---------------------------------------------------------------- write
def write_csv(rows_by_guid):
    rows = list(rows_by_guid.values())
    # style_code 기준 dedup(빈 코드는 guid/url 단위로 개별 유지)
    seen_style = set()
    out, dropped = [], 0
    for r in rows:
        sc = (r.get("style_code") or "").strip()
        if sc:
            if sc in seen_style:
                dropped += 1
                continue
            seen_style.add(sc)
        out.append(r)
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return out, dropped


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    cmap = ss.cate_map()
    order = build_order(cmap)
    print(f"[order] {len(order)} cates (non-parent first, parents last)",
          file=sys.stderr)

    gmap, capped = collect(order)
    print(f"[collect] unique guids={len(gmap)} capped={capped} "
          f"({time.time()-t0:.0f}s)", file=sys.stderr)

    rows_by_guid, fail = detail_phase(gmap)
    out, dropped = write_csv(rows_by_guid)

    # --- dedup 무결성 진단 ---
    n_guid = len(rows_by_guid)
    style_guids = [r for r in rows_by_guid.values()
                   if (r.get("style_code") or "").strip()]
    distinct_styles = {(r["style_code"]).strip() for r in style_guids}
    empty_style = n_guid - len(style_guids)
    gosi_n = sum(1 for r in out if r.get("_gosi"))

    print("\n=== 전수 완료 ===")
    print(f"unique guids(rows)      : {n_guid}")
    print(f"guids with style_code   : {len(style_guids)}")
    print(f"distinct style_codes    : {len(distinct_styles)}")
    print(f"empty style_code rows   : {empty_style}")
    print(f"CSV rows (after dedup)  : {len(out)}  (style-dup dropped {dropped})")
    print(f"detail fail             : {fail}")
    print(f"gosi(소재&제조국) 텍스트: {gosi_n}/{len(out)}")
    print(f"capped@5000             : {capped}")
    print(f"elapsed                 : {time.time()-t0:.0f}s")
    print(f"out                     : {OUT_CSV}")

    # 머신 판독용 요약
    summary = {
        "unique_guids": n_guid, "csv_rows": len(out),
        "style_dup_dropped": dropped, "guids_with_style": len(style_guids),
        "distinct_styles": len(distinct_styles), "empty_style": empty_style,
        "detail_fail": fail, "capped": capped, "cates": len(order),
    }
    with open(os.path.join(OUT, "_starsports_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
