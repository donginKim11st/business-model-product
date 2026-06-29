#!/bin/zsh
# Finisher: wait for in-flight crawl, then gentle low-concurrency resume passes
# to drain HTTP 429 failures, rebuild CSV, and verify colorway-uniqueness.
cd /Users/a1101417/Work/business-model/identity
LOG=outputs/_nf_finish.log
: > "$LOG"

echo "[finish] waiting for in-flight crawl to exit..." | tee -a "$LOG"
while pgrep -f northface_sitemap_full.py >/dev/null 2>&1; do sleep 5; done
echo "[finish] in-flight crawl exited. counts: jsonl=$(wc -l < outputs/_nf_rows.jsonl) done=$(wc -l < outputs/_nf_sitemap_done.txt) codes=$(wc -l < outputs/_nf_sitemap_codes.txt)" | tee -a "$LOG"

# Gentle resume passes: low concurrency, long backoff. Repeat until the todo
# (codes - done) stops shrinking or hits zero. 429-failed codes aren't marked
# done, so each pass retries them plus the 7 newly appended category-only codes.
prev_remaining=-1
for pass in 1 2 3 4 5; do
  codes=$(wc -l < outputs/_nf_sitemap_codes.txt)
  done=$(wc -l < outputs/_nf_sitemap_done.txt)
  remaining=$((codes - done))
  echo "[finish] pass $pass start: codes=$codes done=$done remaining=$remaining" | tee -a "$LOG"
  if [ "$remaining" -le 0 ]; then echo "[finish] nothing left" | tee -a "$LOG"; break; fi
  if [ "$remaining" -eq "$prev_remaining" ]; then echo "[finish] no progress, stop" | tee -a "$LOG"; break; fi
  prev_remaining=$remaining
  NF_WORKERS=3 NF_RETRIES=6 python3 -u northface_sitemap_full.py >> "$LOG" 2>&1
done

echo "[finish] FINAL counts: jsonl=$(wc -l < outputs/_nf_rows.jsonl) done=$(wc -l < outputs/_nf_sitemap_done.txt) csv=$(wc -l < outputs/extract_brand_northface.csv)" | tee -a "$LOG"
echo "[finish] remaining 429/fail codes (in codes file but not done):" | tee -a "$LOG"
comm -23 <(sort outputs/_nf_sitemap_codes.txt) <(sort outputs/_nf_sitemap_done.txt) | wc -l | tee -a "$LOG"
echo "[finish] DONE_MARKER" | tee -a "$LOG"
