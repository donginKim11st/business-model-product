# C1-data 설계 — OPT_NM(변형) 을 catalog/씨앗으로 싣는 경로

## 왜 (검증된 근거)
전수 A/B 검증(`eval_identity_full.py`): 이름만 매칭 precision **35%** → 색/사이즈 변별 시 **76%**(+41p).
변형충돌(같은 이름 다른 style_code)을 색/사이즈로 가리면 precision 2배. 그 변별자의 데이터 소스 =
Oracle `PD_CTLG.OPT_NM`(옵션명). C0 실측: 변형 카테고리(REG 501/100/801…)는 OPT_NM **100% 채움**.
(주의: REG_TYP_CD = 처리 대상 스코프 마커. OPT_NM 가용은 스코프에 변형 REG를 넣을 때.)

## 현재 흐름 (어디에 끼우나)
```
Oracle PD_CTLG ──build_all_bndl.py(archive/)──▶ trees_src.jsonl
   (ctlg_no, DISP_MODEL_NM, …)                    sizes[].counts[] = SKU {ctlg_no, disp, count}
        └─ ★여기서 OPT_NM/BAR_CODE 도 SELECT          └─ ★count 노드에 opt_nm/barcode 실음
                                                        │
   demo_load_trees.iter_catalogs ──▶ products.catalogs[] {ctlg_no, disp, size, count}
        └─ ★OPT_NM → color/size 파싱해 catalog 에 color/barcode 추가
                                                        │
   export_identity_seed ──▶ seed.csv {…, size, color, barcode}   ← C1 에서 이미 컬럼 준비됨 ✅
                                                        │
   identity_seed_match ──▶ (recall, color_match, size_match) tie-break   ← C1 에서 이미 구현됨 ✅
```
**매처·씨앗(하류)은 준비 끝.** 남은 건 상류 2곳에서 OPT_NM/BAR_CODE 를 실어 나르는 것뿐.

## 변경 지점 (3곳, 작음)

### 1) Oracle 추출 (build_all_bndl.py, archive/ — 원천 측)
PD_CTLG SELECT 에 `OPT_NM, BAR_CODE` 추가. 각 ctlg_no(=SKU) 행에 그대로 보유 → trees 의
`counts[]` 노드(SKU)에 `opt_nm`, `barcode` 키로 실음.
```
counts[i] = {ctlg_no, disp, count, opt_nm, barcode}   # opt_nm/barcode 신규
```

### 2) catalog 빌드 (demo_load_trees.iter_catalogs:57-61)
count 노드 → catalog dict 에 color/size/barcode 추가. OPT_NM 은 "색상:블랙/사이즈:270" 류 →
`parse_opt(opt_nm)` 로 color/size 분리(없으면 raw opt 보관).
```python
out.append({"ctlg_no": c.get("ctlg_no"), "disp": ..., "size": sval, "count": c.get("count"),
            "color": parse_opt(c.get("opt_nm")).get("color"),
            "opt": c.get("opt_nm"),
            "barcode": c.get("barcode"),          # 신규 3개
            "has_insight": ...})
```
`parse_opt`: 구분자(`/`,`,`,`:`)로 색/사이즈 추출하는 소형 파서. 매칭 robust 위해 색은 정규화(블랙=BLACK).

### 3) reload 보존
`catalogs[].color/barcode` 는 `catalogs[].price_summary`/`identity` 와 같은 경로로 보존
(demo_load_trees 증분 skip + backfill $set). 신규 보존 코드 불필요 — catalog 빌드에 포함되므로 자동.

## 효과 측정 (이미 만든 보정 DB)
변형 REG 유입 + OPT_NM 흐른 뒤 같은 `identity_calibrate recommend` → `identity_calib_runs` 의
precision 이 35%→76%대로 오르고, 의류 `status` 가 `needs_strong_key → effective` 로 전환되는 걸 이력으로 확인.

## BAR_CODE(C3) 는 보조
BAR_CODE 는 REG 1001(67%)만 — 그 스코프 한정 강키. 있으면 `strong_keys` 로 정확 매칭(매처 이미 지원),
없으면 OPT_NM 변별(C1)로 충분. 우선순위: **C1(OPT_NM) > C3(barcode, 니치).**

## 스코프 게이트
지금 대상 스코프(REG 1002)엔 OPT_NM 없음 → 효과 없음. **변형 REG(예: 801)를 SP_REG 스코프에 넣을 때**
1)~2)를 붙이면 즉시 +41p. 하류(매처/씨앗/보정DB/대시보드)는 그날을 위해 이미 준비됨.
