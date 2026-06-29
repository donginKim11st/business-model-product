#!/usr/bin/env python3
"""Parallel finisher: resumes the 프로월드컵 full crawl from checkpoint.

Reuses extract_proworldcup_full (paths, get, parse_detail, HEADER, MAX_ROWS).
Reads META (order/label/gender), skips DONE ids, fetches remaining details with a
thread pool, appends rows to WORK (writes single-threaded in main), then does the
final dedup + canonical write to OUT and writes STAT.
"""
import json, csv, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import extract_proworldcup_full as M

WORKERS = 8


def main():
    with open(M.META, encoding="utf-8") as f:
        meta = json.load(f)
    order, label, gender = meta["order"], meta["label"], meta["gender"]
    pages_total = meta.get("pages_total", 0)

    done = set()
    if os.path.exists(M.DONE):
        with open(M.DONE, encoding="utf-8") as f:
            done = set(x.strip() for x in f if x.strip())

    rows_emitted = 0
    if os.path.exists(M.WORK):
        with open(M.WORK, encoding="utf-8-sig") as f:
            rows_emitted = max(sum(1 for _ in f) - 1, 0)

    new_file = not os.path.exists(M.WORK)
    wf = open(M.WORK, "a", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(wf, fieldnames=M.HEADER)
    if new_file:
        w.writeheader()
    df = open(M.DONE, "a", encoding="utf-8")

    todo = [p for p in order if p not in done]
    print(f"[finish] {len(todo)} to do, {len(done)} done, {rows_emitted} rows on disk", file=sys.stderr)

    def work(pid):
        try:
            return pid, M.parse_detail(pid, label.get(pid, ""), gender.get(pid, ""))
        except Exception as e:
            return pid, e

    capped = False
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, pid) for pid in todo]
        for fut in as_completed(futs):
            pid, res = fut.result()
            if isinstance(res, Exception):
                print(f"  pid {pid} ERR {res}", file=sys.stderr)
                rows = []
            else:
                rows = res
            for r in rows:
                w.writerow(r); rows_emitted += 1
            df.write(pid + "\n"); df.flush(); wf.flush()
            n += 1
            if n % 100 == 0:
                print(f"  {n}/{len(todo)} done, rows={rows_emitted}", file=sys.stderr)
            if rows_emitted >= M.MAX_ROWS:
                capped = True
                print(f"  !! MAX_ROWS {M.MAX_ROWS} reached", file=sys.stderr)
                break
    wf.close(); df.close()

    # recount done after run
    with open(M.DONE, encoding="utf-8") as f:
        done_n = sum(1 for x in f if x.strip())

    # final dedup + canonical write
    seen, final = set(), []
    with open(M.WORK, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (r["style_code"], r["color"], r["sizes"])
            if key in seen:
                continue
            seen.add(key); final.append(r)
    with open(M.OUT, "w", newline="", encoding="utf-8-sig") as f:
        ww = csv.DictWriter(f, fieldnames=M.HEADER)
        ww.writeheader(); ww.writerows(final)

    n_products = len(set(r["style_code"] for r in final))
    filled = {c: sum(1 for r in final if str(r.get(c, "")).strip()) for c in M.HEADER}
    stat = {"after_rows": len(final), "products": n_products, "list_pages": pages_total,
            "ids_collected": len(order), "details_done": done_n, "capped": capped,
            "filled": filled}
    with open(M.STAT, "w", encoding="utf-8") as f:
        json.dump(stat, f, ensure_ascii=False, indent=2)
    print("STAT " + json.dumps(stat, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
