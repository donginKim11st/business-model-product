# 공식몰 정형 데이터 추출 파이프라인

30개 스포츠/아웃도어 브랜드 공식 한국몰에서 상품 정형 데이터(스타일코드·이름·컬러·가격·사이즈)
+ 상품정보제공고시(소재·제조국·제조년월)를 추출해 통합 CSV·대시보드로 만든다.

## 한 줄 실행

```bash
python3 extract_all.py            # 없는 브랜드만 추출 → 고시 OCR 보강 → 대시보드
python3 extract_all.py --force    # 전부 재추출
python3 extract_all.py --only fila,puma   # 특정 브랜드만
python3 extract_all.py --skip-ocr # 고시 이미지 OCR 생략(빠름)
python3 extract_all.py --dashboard # 추출 건너뛰고 병합+대시보드만
```

산출물: `outputs/all_brands.csv` (통합 정형 데이터), `outputs/all_brands_dashboard.html` (대시보드),
브랜드별 `outputs/extract_brand_<slug>.csv`.

## 키 설정 (자동 로드)

`~/Work/business-model/run.sh` 의 `export` 라인에서 자동으로 읽음:
- **OPENAI_API_KEY** — 고시가 이미지에 박힌 브랜드(미즈노·몽벨 등)의 소재/제조국 비전 OCR(gpt-4o-mini). 없으면 OCR 생략.
- **NAVER_CLIENT_ID / NAVER_CLIENT_SECRET** — 네이버 경유 도구용.

## 추출 방식 (플랫폼별)

봇차단 유무 + 데이터 후크에 따라 3계층:

| 계층 | 방식 | 브랜드 |
|---|---|---|
| 서버측 HTTP (urllib/curl_cffi) — JSON-LD | cafe24·Shopify·Demandware·Styleship | 휠라·푸마·크록스·언더아머·아레나·콜핑·르까프·프로월드컵·잔스포츠·네파·컬럼비아 등 |
| 서버측 HTTP — DOM/내부JSON | 자체몰·k-village·고도몰 | 노스페이스·스케쳐스·프로스펙스·월드컵·반스·블랙야크·몽벨·밀레·미즈노·웨스트우드·아이더·케이투·내셔널지오·아웃도어프로덕츠·스타스포츠 |
| 봇차단 우회 | 나이키(거주지IP urllib), **아디다스(언블로커 또는 patchright)** | 나이키·아디다스 |

curl_cffi(크롬 TLS지문 위장)는 TLS만 검사하는 Akamai(언더아머 418) 우회. 시스템 python3에 patchright 설치.

## 아디다스 — 언블로커 API (대량 안정화)

아디다스는 Akamai 행동챌린지라 PDP마다 재챌린지+레이트리밋이 걸려 patchright(헤드리스 크롬)로도 대량이 느림.
**언블로커 API 키**를 환경변수로 넣으면 `extract_adidas.py` 가 자동으로 그걸 쓴다(브라우저 불필요·cron 가능):

```bash
# 프로바이더 택1 (계정/키는 직접 발급)
export UNBLOCKER_PROVIDER=scraperapi    # 또는 zenrows / scrapingbee / brightdata / custom
export UNBLOCKER_KEY=<발급받은 키>
# brightdata 는 추가로: export UNBLOCKER_ZONE=<zone>
# custom 은: export UNBLOCKER_ENDPOINT='https://my.proxy/?url={url}'

python3 extract_all.py --only adidas --force
```

키 미설정이면 자동으로 **patchright headed 폴백**(시스템 python3, 화면 필요 — 서버는 xvfb 가상디스플레이).

## 고시(소재·제조국·제조년월) 채움

- **텍스트 노출몰**(JSON-LD additionalProperty 또는 고시 테이블): 추출 시 바로 채워짐(~100%).
- **이미지 박힘몰**(미즈노·몽벨·아웃도어·웨스트우드·프로월드컵·콜핑): `ocr_openai.py` 가 상세 통이미지를
  gpt-4o-mini 비전으로 읽어 채움. `extract_all.py` 2단계에서 자동 실행 → `gosi_<slug>.csv` → 병합.
- **크록스**: 고시가 렌더 텍스트(아코디언)라 텍스트 파싱.
- **미게재 브랜드**(잔스포츠 등): 공식몰이 고시를 안 올려 빈값(정상).

## 파일 구성

- `extract_all.py` — 통합 실행기(이 README의 진입점)
- `extract_<slug>.py` / `<slug>_extract.py` — 브랜드별 어댑터
- `official_extract.py` — 나이키/뉴발란스(+동원) 통합 엔진
- `ocr_openai.py` — 고시 이미지 비전 OCR(gpt-4o-mini)
- `ocr_gosi.py` — 고시 OCR 공용 유틸(이미지 다운로드/Tesseract 폴백)
- `unblocker.py` — 언블로커 API 프로바이더 무관 래퍼
- `extract_adidas.py` — 아디다스(언블로커/patchright)
- `all_brands_html.py` — 통합 CSV + 대시보드 빌더
