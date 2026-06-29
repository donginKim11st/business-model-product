#!/usr/bin/env python3
"""
MCP 서버 (stdlib만, Python 3.9 호환) — 상품 정체성 그래프 엔진을 MCP 도구로 노출.
공식 SDK(3.10+) 없이 stdio JSON-RPC 2.0를 직접 구현.

도구:
  resolve_products  : 임의의 리스팅들을 '같은 제품'으로 묶음 (오프라인, 키 불필요)
  analyze_brand     : 네이버 쇼핑 API로 브랜드 실데이터 수집→통합→공식가 침해 리포트
  seller_footprint  : 판매처(셀러명)별로 그 브랜드를 뭘·얼마나 싸게 파는지

규칙: stdout = JSON-RPC 전용. 로그는 stderr. 메시지는 줄단위 JSON.
네이버 도구는 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 필요.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pig.blocking import HybridBlocker
from pig.resolve import resolve
from pig.normalize import extract_attributes


def log(*a):
    print("[MCP]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- engine tools
def _cluster(records):
    run = resolve(records, HybridBlocker(), cluster_guard=True)
    by = {r["id"]: r for r in records}
    out = []
    for cl in run["clusters"]:
        ms = [by[i] for i in cl]
        out.append({
            "members": [m.get("raw_title", m["title"]) for m in ms],
            "marketplaces": sorted({m.get("marketplace", "?") for m in ms}),
            "size": ms[0]["title"] and extract_attributes(ms[0])["size_token"],
            "category": extract_attributes(ms[0])["category"],
        })
    return out


def tool_resolve_products(args):
    listings = args.get("listings") or []
    recs = [{"id": f"L{i}", "title": l.get("title", ""), "raw_title": l.get("title", ""),
             "marketplace": l.get("marketplace", "?"), "gtin": l.get("gtin", ""),
             "price": l.get("price")} for i, l in enumerate(listings) if l.get("title")]
    clusters = _cluster(recs)
    return {"input_listings": len(recs), "resolved_products": len(clusters), "products": clusters}


def _naver_records():
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        raise RuntimeError("환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 가 필요합니다.")
    from naver_pull import search_shop
    from naver_resolve import parse_qty_and_clean

    def pull(brand):
        recs, seen = [], set()
        for it in search_shop(brand, cid, csec, display=100):
            if not it["lprice"]:
                continue
            key = (it["title"], it["mallName"], it["lprice"])
            if key in seen:
                continue
            seen.add(key)
            clean, qty = parse_qty_and_clean(it["title"])
            recs.append({"id": f"NV{len(recs):03d}", "title": clean, "raw_title": it["title"],
                         "marketplace": it["mallName"] or "?", "gtin": "", "price": it["lprice"],
                         "qty": qty, "unit_price": round(it["lprice"] / qty),
                         "mall": it["mallName"] or "?"})
        return recs
    return pull


def _resolve_naver(brand):
    recs = _naver_records()(brand)
    run = resolve(recs, HybridBlocker(), cluster_guard=True)
    by = {r["id"]: r for r in recs}
    return recs, run, by


def tool_analyze_brand(args):
    brand = args.get("brand", "").strip()
    if not brand:
        raise RuntimeError("brand 인자가 필요합니다.")
    recs, run, by = _resolve_naver(brand)
    products = []
    for cl in run["clusters"]:
        ms = [by[i] for i in cl]
        if len(ms) < 2:
            continue
        off = [m for m in ms if "공식" in m["mall"]]
        official = min((m["unit_price"] for m in off), default=None)
        low = min(ms, key=lambda m: m["unit_price"])
        a = extract_attributes(ms[0])
        products.append({
            "name": _name([m["raw_title"] for m in ms]),
            "size": a["size_token"], "n_malls": len({m["mall"] for m in ms}),
            "official_unit": official, "lowest_unit": low["unit_price"], "lowest_mall": low["mall"],
            "undercut_pct": round((official - low["unit_price"]) / official * 100)
            if official and low["unit_price"] < official else 0,
        })
    products.sort(key=lambda p: -p["undercut_pct"])
    under = [p for p in products if p["undercut_pct"] > 0]
    return {"brand": brand, "listings_pulled": len(recs), "products_resolved": len(products),
            "below_official_count": len(under), "products": products[:25],
            "note": "가격은 1개당 환산. 판매처=네이버 mallName. 쿠팡은 네이버 가격비교 경유 일부."}


def tool_seller_footprint(args):
    brand = args.get("brand", "").strip()
    if not brand:
        raise RuntimeError("brand 인자가 필요합니다.")
    recs, run, by = _resolve_naver(brand)
    prods = []
    for cl in run["clusters"]:
        ms = [by[i] for i in cl]
        if len(ms) < 2:
            continue
        off = min((m["unit_price"] for m in ms if "공식" in m["mall"]), default=None)
        prods.append((off, ms))
    sellers = {}
    for off, ms in prods:
        name = _name([m["raw_title"] for m in ms])
        for m in ms:
            s = sellers.setdefault(m["mall"], {"name": m["mall"], "skus": set(), "disc": [],
                                               "official": "공식" in m["mall"]})
            s["skus"].add(name)
            if off and m["unit_price"] < off:
                s["disc"].append((off - m["unit_price"]) / off)
    rows = []
    for s in sellers.values():
        rows.append({"seller": s["name"], "official": s["official"], "n_products": len(s["skus"]),
                     "avg_below_official_pct": round(sum(s["disc"]) / len(s["disc"]) * 100) if s["disc"] else 0})
    rows.sort(key=lambda r: (-r["n_products"], -r["avg_below_official_pct"]))
    return {"brand": brand, "sellers": rows[:25],
            "note": "판매처=네이버 mallName 기준. avg_below_official_pct = 공식가보다 평균 몇 % 싸게."}


def _name(titles):
    import re
    t = min(titles, key=len)
    t = re.sub(r"\[[^\]]*\]", " ", t).split("/")[0]
    t = re.sub(r"(싸이닉)\s+싸이닉", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


TOOLS = {
    "resolve_products": (tool_resolve_products,
        "여러 쇼핑 리스팅을 '같은 제품'으로 묶습니다(크로스마켓 엔티티 레졸루션). 키 불필요.",
        {"type": "object", "properties": {"listings": {"type": "array", "description":
            "리스팅 배열", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "marketplace": {"type": "string"},
                "price": {"type": "number"}, "gtin": {"type": "string"}}, "required": ["title"]}}},
         "required": ["listings"]}),
    "analyze_brand": (tool_analyze_brand,
        "네이버 쇼핑 API로 브랜드 실데이터를 수집→같은 제품으로 통합→공식가보다 싸게 팔리는 제품 리포트(1개당 가격). NAVER_CLIENT_ID/SECRET 필요.",
        {"type": "object", "properties": {"brand": {"type": "string", "description": "브랜드/검색어 (예: 싸이닉)"}},
         "required": ["brand"]}),
    "seller_footprint": (tool_seller_footprint,
        "판매처(셀러명)별로 그 브랜드 상품을 몇 개, 공식가보다 평균 몇 % 싸게 파는지. NAVER_CLIENT_ID/SECRET 필요.",
        {"type": "object", "properties": {"brand": {"type": "string", "description": "브랜드/검색어"}},
         "required": ["brand"]}),
}


# ------------------------------------------------------------ JSON-RPC plumbing
def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        reply(mid, {"protocolVersion": pv, "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "product-identity-graph", "version": "0.1.0"}})
    elif method == "notifications/initialized":
        pass  # notification, no reply
    elif method == "tools/list":
        reply(mid, {"tools": [{"name": n, "description": d, "inputSchema": s}
                              for n, (_, d, s) in TOOLS.items()]})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            reply(mid, error={"code": -32602, "message": f"unknown tool: {name}"})
            return
        try:
            out = TOOLS[name][0](args)
            reply(mid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}],
                        "isError": False})
        except Exception as e:
            log("tool error:", repr(e))
            reply(mid, {"content": [{"type": "text", "text": f"오류: {e}"}], "isError": True})
    elif mid is not None:
        reply(mid, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    log("product-identity-graph MCP server 시작 (stdio)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError as e:
            log("JSON 파싱 오류:", e)
        except Exception as e:
            log("처리 오류:", repr(e))


if __name__ == "__main__":
    main()
