#!/usr/bin/env python3
"""MongoDB 검색 인덱스 셋업 — Atlas Vector Search + (선택) 한국어 텍스트 검색.

관계형 DB의 'CREATE ... INDEX' DDL에 해당. chunks.embedding을 채운 뒤 실행.
※ $vectorSearch / Atlas Search 인덱스는 Atlas(클라우드) 또는 Atlas Local(=이 검증 환경)에서만 동작.
  순수 self-host Community 서버에는 없음 → RAG를 Mongo로 가려면 Atlas가 사실상 전제.

사용:
  MONGO_URI="mongodb://localhost:47017/?directConnection=true" python3 db/mongo_setup.py
"""
import os, time
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

DIM = 1024   # 픽스한 임베딩 모델 차원(한국어 강모델 bge-m3/e5=1024 권장)


def main():
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")).insights

    # 벡터 검색 인덱스: chunks.embedding(코사인 HNSW) + 메타데이터 사전필터 필드
    vec = SearchIndexModel(
        name="chunk_vec",
        type="vectorSearch",
        definition={"fields": [
            {"type": "vector", "path": "embedding", "numDimensions": DIM, "similarity": "cosine"},
            {"type": "filter", "path": "metadata.category"},
            {"type": "filter", "path": "metadata.dim"},
            {"type": "filter", "path": "kind"},
            {"type": "filter", "path": "product_uid"},
        ]},
    )
    existing = {ix["name"] for ix in db.chunks.list_search_indexes()}
    if "chunk_vec" not in existing:
        db.chunks.create_search_index(vec)
        print("chunk_vec(vectorSearch) 생성 요청 — 빌드까지 수십 초")
    else:
        print("chunk_vec 이미 존재")

    # (선택) 한국어 텍스트 검색: Atlas Search lucene.korean(nori) 분석기
    txt = SearchIndexModel(
        name="chunk_text",
        type="search",
        definition={"mappings": {"dynamic": False, "fields": {
            "content": {"type": "string", "analyzer": "lucene.korean"}}}},
    )
    if "chunk_text" not in existing:
        db.chunks.create_search_index(txt)
        print("chunk_text(search, 한국어 nori) 생성 요청")

    # 빌드 완료 대기
    for _ in range(60):
        ready = {ix["name"]: ix.get("status") for ix in db.chunks.list_search_indexes()}
        if ready.get("chunk_vec") == "READY":
            print("인덱스 상태:", ready); return
        time.sleep(3)
    print("타임아웃 — list_search_indexes로 상태 확인 필요")


if __name__ == "__main__":
    main()
