# insight-engine 서버 배포 (직접 파이썬 · REST 서비스)

다른 서버에서 `insight_engine`의 REST 서비스(`/extract`·`/jobs`·`/metrics`)를 상시 구동하는 절차.
컨테이너 없이 venv + systemd. 배치(submit/status/fetch)도 같은 번들로 CLI 실행 가능.

## 왜 번들이 필요한가

`insight_engine`은 순수 코어지만, 실제 추출은 상위 워크스페이스의 `naver_review_geo.py`·`run_batch.py`
(그리고 배치는 `db/`의 모듈들)에 의존한다. `bundle.sh`가 이 의존 파일들을 **sibling import 구조 그대로**
모아 자족 배포 디렉토리를 만든다. 키·산출 데이터는 담지 않는다.

## 1. 번들 생성 (개발 머신)

```bash
# insight/ 에서
deploy/bundle.sh                      # → insight/deploy/bundle/
# 또는 경로 지정
deploy/bundle.sh /tmp/insight-engine-deploy
```

포함: `insight_engine/` · `naver_review_geo.py` · `run_batch.py` · `db/*`(배치) · `requirements.txt` · `.env.example` · `insight-engine.service`.
미포함(정상): `run.sh`(실키) · 산출 데이터 · 테스트 · playwright.

## 2. 서버에 배치

```bash
# 번들을 서버로 복사(예: /opt/insight-engine)
rsync -a insight/deploy/bundle/ user@server:/opt/insight-engine/

# 서버에서
cd /opt/insight-engine
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## 3. 환경 변수

```bash
cp .env.example .env
# .env 를 열어 키 채우기: NAVER_CLIENT_ID/SECRET, OPENAI_API_KEY
# 서버 바인딩: INSIGHT_HTTP_HOST=0.0.0.0 · INSIGHT_HTTP_PORT=8770
```

| 변수 | 필수 | 설명 |
|------|:---:|------|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ✔ | 네이버 검색 API |
| `OPENAI_API_KEY` | ✔ | OpenAI |
| `INSIGHT_HTTP_HOST` | — | 기본 `0.0.0.0`(외부 허용). 로컬만이면 `127.0.0.1` |
| `INSIGHT_HTTP_PORT` | — | 기본 `8770` |
| `INSIGHT_MODEL` | — | 기본 `gpt-4o-mini` |
| `MONGO_URI` · `INSIGHTS_DB` | 배치만 | 배치 적재 대상 Mongo |

> REST의 sync 경로(`/extract`·`/jobs`)는 Mongo를 쓰지 않는다(잡 상태는 파일). Mongo는 배치 적재에만 필요.

## 4. REST 서비스 기동

**수동 확인**

```bash
. .venv/bin/activate && python -m insight_engine.http_adapter
# → insight-engine REST on http://0.0.0.0:8770  (/extract · /jobs · /metrics)
curl -s localhost:8770/metrics
curl -s -XPOST localhost:8770/extract -d '{"keyword":"아식스 젤카야노"}'
```

**systemd 상시 구동**

```bash
sudo cp insight-engine.service /etc/systemd/system/
# .service 의 WorkingDirectory / EnvironmentFile / ExecStart 경로 확인
sudo systemctl daemon-reload
sudo systemctl enable --now insight-engine
sudo systemctl status insight-engine
journalctl -u insight-engine -f      # 로그
```

## 5. 배치도 같은 번들로 (선택)

```bash
. .venv/bin/activate
INSIGHTS_DB=insights db/run_batch_insight.sh submit 25000   # 네이버 쿼터만큼
db/run_batch_insight.sh status
db/run_batch_insight.sh fetch
```

배치는 `MONGO_URI`가 그 서버에서 닿는 Mongo를 가리켜야 한다. 크롤 결과·manifest는
번들 안 `db/insight_engine_batch/run_*/`에 남는다(재시작 후에도 유지하려면 그 경로를 영속 볼륨/디스크에 둘 것).

## 알려진 정리거리 (follow-up)

- `batch_openai.py`가 `to_insight` 하나 때문에 `catalog_insight_backfill`(Mongo 모듈)을 import한다.
  `to_insight`를 `insight_engine` 안으로 옮기면 sync-REST 번들에서 `db/` 의존을 완전히 뗄 수 있다.
- 데이터 경로(run-dir)를 `DATA_DIR` env로 빼면 볼륨 관리가 깔끔해진다.
