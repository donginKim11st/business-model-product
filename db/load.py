#!/usr/bin/env python3
"""insights_*.jsonl → PostgreSQL 적재기 (재개·재실행 안전).

각 최상위 레코드 = base product. tree.sizes의 각 변형 = parent_uid로 묶인 자식 product.
uid 단위로 먼저 DELETE(CASCADE) 후 재삽입 → 같은 파일을 다시 돌려도 중복 없음.
taxonomy는 재귀 워킹: list 값을 갖는 leaf만 point가 됨(빈 셀은 행 없음).

사용:
  export PGHOST=localhost PGPORT=55432 PGUSER=postgres PGPASSWORD=insight PGDATABASE=insights
  python3 db/load.py insights_1002.jsonl --category food [--work-units work_units.jsonl] [--limit N]
"""
import os, sys, json, hashlib, argparse
import psycopg2
from psycopg2.extras import execute_values, Json

# taxonomy에서 point로 만들지 않는 키(스칼라로 product에 별도 저장)
SKIP_LEAF = {"flags", "overall_recommendation"}


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def norm_date(s):
    """'YYYYMMDD' → date 또는 None."""
    if not s or not isinstance(s, str) or len(s) != 8 or not s.isdigit():
        return None
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def walk_points(node, path=""):
    """taxonomy 트리를 워킹하며 (dim_path, point_list) yield."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP_LEAF:
                continue
            yield from walk_points(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list) and node and isinstance(node[0], dict) and "point" in node[0]:
        yield path, node


def load_block(cur, product_uid, block):
    """한 block의 source_index/taxonomy/faqs를 적재. product 행은 호출자가 이미 넣음."""
    # 1) sources
    src_rows = []
    for sid, s in (block.get("source_index") or {}).items():
        src_rows.append((
            product_uid, sid, s.get("source"), s.get("kind"), s.get("is_ad"),
            Json(s.get("ad_signals") or []), s.get("author"), s.get("date"),
            norm_date(s.get("date")), s.get("url"), s.get("title"),
            s.get("rating"), s.get("text"),
        ))
    if src_rows:
        execute_values(cur, """
            INSERT INTO source (product_uid, local_id, source, kind, is_ad, ad_signals,
                                author, date, date_norm, url, title, rating, body)
            VALUES %s""", src_rows)

    # 2) points + evidence — 배치 삽입(라운드트립을 point 건수 → block당 2회로 축소)
    point_rows, point_ev = [], []      # 두 리스트는 입력 순서로 정렬됨
    for dim_path, items in walk_points(block.get("taxonomy") or {}):
        for ord_, it in enumerate(items):
            point_rows.append((product_uid, dim_path, it.get("point", ""),
                               it.get("cited_examples"), ord_))
            point_ev.append(it.get("evidence") or [])
    if point_rows:
        # execute_values + RETURNING(fetch=True): 반환 id는 입력 순서와 일치
        ids = execute_values(cur, """
            INSERT INTO point (product_uid, dim_path, text, cited_examples, ord)
            VALUES %s RETURNING id""", point_rows, fetch=True)
        ev_rows = [(pid[0], product_uid, ev.get("source_id"), ev.get("quote", ""), ev.get("match"), j)
                   for pid, evl in zip(ids, point_ev) for j, ev in enumerate(evl)]
        if ev_rows:
            execute_values(cur, """
                INSERT INTO evidence (point_id, product_uid, local_source_id, quote, match, ord)
                VALUES %s""", ev_rows)

    # 3) faqs + faq_evidence — 동일 배치 패턴
    faqs = block.get("faqs") or []
    if faqs:
        faq_rows = [(product_uid, f.get("question", ""), f.get("short_answer"),
                     f.get("cited_examples"), i) for i, f in enumerate(faqs)]
        fids = execute_values(cur, """
            INSERT INTO faq (product_uid, question, short_answer, cited_examples, ord)
            VALUES %s RETURNING id""", faq_rows, fetch=True)
        fe_rows = []
        for fid, f in zip(fids, faqs):
            for role, key in (("answer", "answer_evidence"), ("question", "question_evidence")):
                for j, ev in enumerate(f.get(key) or []):
                    fe_rows.append((fid[0], product_uid, role, ev.get("source_id"),
                                    ev.get("quote", ""), ev.get("match"), j))
        if fe_rows:
            execute_values(cur, """
                INSERT INTO faq_evidence (faq_id, product_uid, role, local_source_id, quote, match, ord)
                VALUES %s""", fe_rows)


def insert_product(cur, uid, parent_uid, type_, variant_value, keyword, category,
                   bndl_grp, block, n_items=None, elapsed=None):
    tax = block.get("taxonomy") or {}
    verdict = tax.get("verdict") or {}
    note_text = block.get("note")
    note_hash = None
    if note_text:
        note_hash = md5(note_text)
        cur.execute("INSERT INTO note (hash, text) VALUES (%s,%s) ON CONFLICT (hash) DO NOTHING",
                    (note_hash, note_text))
    cur.execute("""
        INSERT INTO product (uid, parent_uid, bndl_grp, type, variant_value, keyword, category,
                             analyzed_count, ad_flagged, sources, verification,
                             overall_recommendation, flags, note_hash, raw_block, n_items, elapsed)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uid, parent_uid, bndl_grp, type_, variant_value, keyword, category,
         block.get("analyzed_count"), block.get("ad_flagged"),
         Json(block.get("sources") or {}), Json(block.get("verification") or {}),
         verdict.get("overall_recommendation"), Json(tax.get("flags") or {}),
         note_hash,
         # raw_block에서 source_index 제외(source 테이블과 바이트 중복) — 무손실 재구성 가능
         Json({k: v for k, v in block.items() if k != "source_index"}),
         n_items, elapsed))
    load_block(cur, uid, block)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--category", default=None)
    ap.add_argument("--work-units", default=None, help="uid→bndl_grp 매핑 보강용")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    bndl = {}
    if args.work_units and os.path.exists(args.work_units):
        for line in open(args.work_units):
            if line.strip():
                w = json.loads(line)
                bndl[w["uid"]] = w.get("bndl_grp")

    conn = psycopg2.connect()  # PG* 환경변수 사용
    conn.autocommit = False
    n_base = n_var = n_proc = n_err = 0
    cur = conn.cursor()
    for line in open(args.jsonl):
        if not line.strip():
            continue
        if args.limit and n_proc >= args.limit:
            break
        n_proc += 1
        rec = json.loads(line)
        uid = rec["uid"]
        try:
            # idempotent: 기존 base 삭제(CASCADE로 변형·points·evidence·sources·faqs 전부 제거)
            cur.execute("DELETE FROM product WHERE uid = %s", (uid,))
            insert_product(cur, uid, None, rec.get("type", "package"), None,
                           rec.get("keyword", ""), args.category, bndl.get(uid),
                           rec["block"], rec.get("n_items"), rec.get("elapsed"))
            n_base += 1
            # 변형(자식)
            for sz in (rec.get("tree") or {}).get("sizes") or []:
                vb = sz.get("block")
                if not vb:            # block 없는 변형(미처리/single_size)은 건너뜀
                    continue
                val = sz.get("value")
                if not val or "::" in val:   # 변형키 가드: None/빈문자/구분자 충돌 차단
                    raise ValueError(f"{uid}: 불안정한 변형 value={val!r} (None/빈문자/'::' 불가)")
                insert_product(cur, f"{uid}::{val}", uid, "variant", val,
                               f"{rec.get('keyword','')} {val}".strip(),
                               args.category, bndl.get(uid), vb)
                n_var += 1
            conn.commit()            # 레코드 단위 커밋 → 중간 크래시 시 단순 재실행으로 재개(멱등)
        except Exception as e:
            conn.rollback()          # 이 uid만 롤백, 다음 레코드로 진행
            n_err += 1
            print(f"  [skip] {uid}: {str(e)[:120]}", file=sys.stderr)
    conn.close()
    print(f"적재 완료: base={n_base} variants={n_var} (처리 {n_proc}, 실패 {n_err})")


if __name__ == "__main__":
    main()
