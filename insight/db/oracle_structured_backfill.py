#!/usr/bin/env python3
"""Oracle PD_CTLG 정형 팩트 → insights_demo product 합류 (ctlg_no 정확 조인).

비정형(insight)이 쓰는 그 Oracle 을 정형 추출에도 같이 쓴다. insight catalog 와 PD_CTLG 는
둘 다 ctlg_no 를 키로 하므로 **퍼지 매칭/게이트/색상가드 없이 1:1 정확 합류**.
(brand-mall identity 매칭은 Oracle 에 없는 고시(소재/제조국/제조년월)·가격 보충용으로만.)

합류 모양(food_price_backfill 패턴 $set, resumable, reload 보존):
  · per-SKU → products.catalogs[i].identity = {name, brand_cd, model_cd, opt, color, size,
                barcode, base_amt, base_unt, ctgr_no, img, ref_url, source:"oracle", fetched_at}
  · 상품   → products.identity = {brand, status, n_facts, source:"oracle", fetched_at}
  status: done(팩트 합류) | empty(ctlg_no 가 PD_CTLG 에 없음) | pending

  INSIGHTS_DB=insights_demo MONGO_URI=.. ORA_USER/ORA_PW(~/.ora_creds) \
    python3 db/oracle_structured_backfill.py [--limit 500] [--refresh]
"""
import os
import sys
import time
import argparse
from datetime import datetime, timezone

# PD_CTLG → 정형 팩트 컬럼
COLS = ["CTLG_NO", "DISP_MODEL_NM", "CTLG_NM", "BRAND_CD", "MKR_NO", "MODEL_CD_LIST",
        "OPT_NM", "BAR_CODE", "BASI_AMT", "BASI_UNT_CD", "DISP_CTGR_NO", "IMG_PATH", "REF_PAGE_URL"]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def parse_opt(opt_nm):
    """OPT_NM(옵션명) → {color, size} 베스트에포트. '색상:블랙/사이즈:270' · '블랙/270' 등."""
    if not opt_nm:
        return {}
    out = {}
    parts = [p.strip() for seg in str(opt_nm).split("/") for p in seg.split(",")]
    for p in parts:
        if ":" in p:
            k, v = p.split(":", 1)
            k, v = k.strip(), v.strip()
            if any(t in k for t in ("색", "컬러", "color")):
                out.setdefault("color", v)
            elif any(t in k for t in ("사이즈", "size", "치수")):
                out.setdefault("size", v)
    return out


def row_to_identity(row, fetched_at):
    """PD_CTLG row(dict, 대문자키) → per-SKU identity 서브독."""
    g = lambda k: _clean(row.get(k))
    opt = parse_opt(g("OPT_NM"))
    d = {"name": g("DISP_MODEL_NM") or g("CTLG_NM"), "brand_cd": g("BRAND_CD"),
         "model_cd": g("MODEL_CD_LIST"), "opt": g("OPT_NM"),
         "color": opt.get("color"), "size": opt.get("size"),
         "barcode": g("BAR_CODE"), "base_amt": g("BASI_AMT"), "base_unt": g("BASI_UNT_CD"),
         "ctgr_no": g("DISP_CTGR_NO"), "img": g("IMG_PATH"), "ref_url": g("REF_PAGE_URL"),
         "source": "oracle", "fetched_at": fetched_at}
    return {k: v for k, v in d.items() if v is not None or k in ("source", "fetched_at")}


def fetch_pd_ctlg(cur, ctlg_nos):
    """ctlg_no 리스트 → {ctlg_no(str): row(dict)}. IN 절은 1000개씩 청크."""
    out = {}
    cols = ", ".join(COLS)
    nos = [int(c) for c in ctlg_nos if str(c).isdigit()]
    for i in range(0, len(nos), 1000):
        chunk = nos[i:i + 1000]
        ph = ", ".join(f":{j}" for j in range(len(chunk)))
        cur.execute(f"SELECT {cols} FROM pd_ctlg WHERE CTLG_NO IN ({ph})", chunk)
        names = [c[0] for c in cur.description]
        for r in cur.fetchall():
            row = dict(zip(names, r))
            out[str(row["CTLG_NO"])] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리 product 수(0=전체)")
    ap.add_argument("--refresh", action="store_true", help="identity.status 있어도 재합류")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    q = {"type": "package", "catalogs.0": {"$exists": True}}
    if not args.refresh:
        q["identity.status"] = {"$exists": False}
    pkgs = list(db.products.find(q, {"_id": 1, "keyword": 1, "catalogs": 1}))
    if args.limit:
        pkgs = pkgs[:args.limit]
    print(f"Oracle 정형 합류 대상 product {len(pkgs):,}개 (refresh={args.refresh}, dry_run={args.dry_run})")
    if not pkgs:
        print("  (대상 없음 — identity.status 부재 product 없음)")
        return

    import oracledb
    oracledb.init_oracle_client(lib_dir=os.environ.get("ORA_LIB", "/opt/homebrew/lib"))
    conn = oracledb.connect(user=os.environ["ORA_USER"], password=os.environ["ORA_PW"],
                            dsn=oracledb.makedsn(os.environ.get("ORA_HOST", "172.18.176.69"),
                                                 int(os.environ.get("ORA_PORT", "1528")),
                                                 sid=os.environ.get("ORA_SID", "TMALL")))
    cur = conn.cursor()
    t0 = time.time(); n_done = n_empty = n_sku = 0
    fetched_at = now_iso()
    for pkg in pkgs:
        cats = pkg.get("catalogs") or []
        ctlg_nos = [c.get("ctlg_no") for c in cats if c.get("ctlg_no")]
        pd = fetch_pd_ctlg(cur, ctlg_nos) if ctlg_nos else {}
        n_facts = 0; brand = None
        for c in cats:
            row = pd.get(str(c.get("ctlg_no")))
            if row:
                c["identity"] = row_to_identity(row, fetched_at)
                n_facts += 1; n_sku += 1
                brand = brand or c["identity"].get("brand_cd")
        ident = ({"brand": brand, "status": "done", "n_facts": n_facts, "source": "oracle", "fetched_at": fetched_at}
                 if n_facts else {"brand": None, "status": "empty", "n_facts": 0, "source": "oracle", "fetched_at": fetched_at})
        if args.dry_run:
            print(f"  {pkg['_id']} {pkg.get('keyword','')[:22]} → status={ident['status']} n_facts={n_facts}")
        else:
            setdoc = {"identity": ident}
            if n_facts:
                setdoc["catalogs"] = cats
            db.products.update_one({"_id": pkg["_id"]}, {"$set": setdoc})
        n_done += 1 if n_facts else 0
        n_empty += 0 if n_facts else 1
    if not args.dry_run:
        db.products.create_index("identity.status")
    conn.close()
    print(f"완료 · done={n_done} empty={n_empty} · SKU 합류 {n_sku} · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
