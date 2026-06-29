#!/usr/bin/env python3
"""insights_*.jsonl → Oracle 23ai 적재기 (재실행 안전). db/load.py의 Oracle 판.

JSON 컬럼은 python-oracledb의 네이티브 DB_TYPE_JSON으로 바인딩(dict 직접 전송 → OSON).
IDENTITY는 RETURNING ... INTO 로 회수. uid 단위 DELETE(CASCADE)로 멱등.

사용:
  pip install oracledb
  ORA_USER=insight ORA_PW=insight ORA_DSN=localhost:41521/FREEPDB1 \
  python3 db/load_oracle.py insights_1002.jsonl --category food --work-units work_units.jsonl
"""
import os, sys, json, hashlib, argparse, datetime
import oracledb

SKIP_LEAF = {"flags", "overall_recommendation"}
JSON = oracledb.DB_TYPE_JSON


def md5(s): return hashlib.md5(s.encode("utf-8")).hexdigest()


def norm_date(s):
    if not s or not isinstance(s, str) or len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def walk_points(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP_LEAF:
                continue
            yield from walk_points(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list) and node and isinstance(node[0], dict) and "point" in node[0]:
        yield path, node


def new_id(cur):
    v = cur.var(int)
    return v


def load_block(cur, product_uid, block):
    # 1) sources (executemany)
    src_rows = []
    for sid, s in (block.get("source_index") or {}).items():
        src_rows.append(dict(
            product_uid=product_uid, local_id=sid, source=s.get("source"), kind=s.get("kind"),
            is_ad=bool(s.get("is_ad")) if s.get("is_ad") is not None else None,
            ad_signals=s.get("ad_signals") or [], author=(s.get("author") or "")[:1000],
            date_raw=s.get("date"), date_norm=norm_date(s.get("date")), url=s.get("url"),
            title=(s.get("title") or "")[:1000], rating=s.get("rating"), body=s.get("text")))
    if src_rows:
        cur.setinputsizes(ad_signals=JSON)
        cur.executemany("""
            INSERT INTO source (product_uid, local_id, source, kind, is_ad, ad_signals,
                                author, date_raw, date_norm, url, title, rating, body)
            VALUES (:product_uid,:local_id,:source,:kind,:is_ad,:ad_signals,
                    :author,:date_raw,:date_norm,:url,:title,:rating,:body)""", src_rows)

    # 2) points + evidence
    for dim_path, items in walk_points(block.get("taxonomy") or {}):
        for ord_, it in enumerate(items):
            idv = cur.var(int)
            cur.execute("""
                INSERT INTO point (product_uid, dim_path, text, cited_examples, ord)
                VALUES (:1,:2,:3,:4,:5) RETURNING id INTO :6""",
                [product_uid, dim_path, (it.get("point") or "")[:2000],
                 it.get("cited_examples"), ord_, idv])
            point_id = idv.getvalue()[0]
            ev = [dict(point_id=point_id, product_uid=product_uid,
                       local_source_id=e.get("source_id"), quote=(e.get("quote") or "")[:4000],
                       match=e.get("match"), ord=j)
                  for j, e in enumerate(it.get("evidence") or [])]
            if ev:
                cur.executemany("""
                    INSERT INTO evidence (point_id, product_uid, local_source_id, quote, match, ord)
                    VALUES (:point_id,:product_uid,:local_source_id,:quote,:match,:ord)""", ev)

    # 3) faqs + faq_evidence
    for ord_, f in enumerate(block.get("faqs") or []):
        idv = cur.var(int)
        cur.execute("""
            INSERT INTO faq (product_uid, question, short_answer, cited_examples, ord)
            VALUES (:1,:2,:3,:4,:5) RETURNING id INTO :6""",
            [product_uid, (f.get("question") or "")[:2000], (f.get("short_answer") or "")[:2000],
             f.get("cited_examples"), ord_, idv])
        faq_id = idv.getvalue()[0]
        fe = []
        for role, key in (("answer", "answer_evidence"), ("question", "question_evidence")):
            for j, e in enumerate(f.get(key) or []):
                fe.append(dict(faq_id=faq_id, product_uid=product_uid, role=role,
                               local_source_id=e.get("source_id"), quote=(e.get("quote") or "")[:4000],
                               match=e.get("match"), ord=j))
        if fe:
            cur.executemany("""
                INSERT INTO faq_evidence (faq_id, product_uid, role, local_source_id, quote, match, ord)
                VALUES (:faq_id,:product_uid,:role,:local_source_id,:quote,:match,:ord)""", fe)


def insert_product(cur, uid, parent_uid, type_, variant_value, keyword, category, bndl_grp,
                   block, n_items=None, elapsed=None):
    tax = block.get("taxonomy") or {}
    verdict = tax.get("verdict") or {}
    note_text = block.get("note")
    note_hash = None
    if note_text:
        note_hash = md5(note_text)
        cur.execute("""
            MERGE INTO note d USING (SELECT :h AS hash FROM dual) s ON (d.hash=s.hash)
            WHEN NOT MATCHED THEN INSERT (hash, text) VALUES (:h2, :t)""",
            dict(h=note_hash, h2=note_hash, t=note_text))
    cur.setinputsizes(sources=JSON, verification=JSON, flags=JSON, raw_block=JSON)
    cur.execute("""
        INSERT INTO product (product_uid, parent_uid, bndl_grp, type, variant_value, keyword, category,
                             analyzed_count, ad_flagged, sources, verification,
                             overall_recommendation, flags, note_hash, raw_block, n_items, elapsed)
        VALUES (:puid,:parent_uid,:bndl_grp,:ptype,:variant_value,:keyword,:category,
                :analyzed_count,:ad_flagged,:sources,:verification,
                :overall_recommendation,:flags,:note_hash,:raw_block,:n_items,:elapsed)""",
        dict(puid=uid, parent_uid=parent_uid, bndl_grp=bndl_grp, ptype=type_,
             variant_value=variant_value, keyword=keyword[:1000], category=category,
             analyzed_count=block.get("analyzed_count"), ad_flagged=block.get("ad_flagged"),
             sources=block.get("sources") or {}, verification=block.get("verification") or {},
             overall_recommendation=(verdict.get("overall_recommendation") or "")[:2000] or None,
             flags=tax.get("flags") or {}, note_hash=note_hash, raw_block=block,
             n_items=n_items, elapsed=elapsed))
    load_block(cur, uid, block)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--category", default=None)
    ap.add_argument("--work-units", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    bndl = {}
    if args.work_units and os.path.exists(args.work_units):
        for line in open(args.work_units):
            if line.strip():
                w = json.loads(line); bndl[w["uid"]] = w.get("bndl_grp")

    conn = oracledb.connect(user=os.environ["ORA_USER"], password=os.environ["ORA_PW"],
                            dsn=os.environ["ORA_DSN"])
    cur = conn.cursor()
    n_base = n_var = 0
    for i, line in enumerate(open(args.jsonl)):
        if not line.strip():
            continue
        if args.limit and i >= args.limit:
            break
        rec = json.loads(line)
        uid = rec["uid"]
        cur.execute("DELETE FROM product WHERE product_uid = :1", [uid])  # CASCADE
        insert_product(cur, uid, None, rec.get("type", "package"), None,
                       rec.get("keyword", ""), args.category, bndl.get(uid),
                       rec["block"], rec.get("n_items"), rec.get("elapsed"))
        n_base += 1
        for sz in (rec.get("tree") or {}).get("sizes") or []:
            vb = sz.get("block")
            if not vb:
                continue
            val = sz.get("value")
            if not val or "::" in val:        # 변형키 가드: None/빈문자/구분자 충돌 차단
                raise ValueError(f"{uid}: 불안정한 변형 value={val!r}")
            insert_product(cur, f"{uid}::{val}", uid, "variant", val,
                           f"{rec.get('keyword','')} {val}".strip(),
                           args.category, bndl.get(uid), vb)
            n_var += 1
        conn.commit()
    conn.close()
    print(f"적재 완료: base={n_base} variants={n_var}")


if __name__ == "__main__":
    main()
