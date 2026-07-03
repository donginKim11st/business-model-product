"""furniture_load_mongo.catalog_variant_doc — catalog_variants_furniture.csv 행 → Mongo 문서."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_load_mongo import catalog_variant_doc

ROW = {
    "catalog_key": "번개표|전기모기채usb충전식",
    "mall": "bflamp",
    "url": "https://www.kumholamp.com/product/detail.html?product_no=2882",
    "title_commerce": "번개표 전기모기채 USB 충전식 화이트",
    "variant_attrs": '{"color": "화이트"}',
    "price": "20000",
    "name": "번개표 전기모기채  (USB 충전식) - 번개표쇼핑몰",
}


def test_basic_fields():
    d = catalog_variant_doc(ROW)
    assert d["type"] == "furniture_catalog_variant"
    assert d["catalog_key"] == ROW["catalog_key"]
    assert d["mall"] == "bflamp"
    assert d["title_commerce"] == ROW["title_commerce"]
    assert d["price"] == 20000
    assert d["attributes"] == {"color": "화이트"}


def test_id_deterministic_and_distinct():
    a = catalog_variant_doc(ROW)
    b = catalog_variant_doc(dict(ROW))
    assert a["_id"] == b["_id"]  # 같은 행 → 같은 _id (재빌드 안정)
    other = dict(ROW, variant_attrs='{"color": "베이지"}')
    assert catalog_variant_doc(other)["_id"] != a["_id"]
    # 같은 카탈로그·같은 attrs라도 상품(url)이 다르면 별개 행
    other_url = dict(ROW, url=ROW["url"] + "&x=1")
    assert catalog_variant_doc(other_url)["_id"] != a["_id"]


def test_sparse_fields():
    r = dict(ROW, variant_attrs="{}", price="문의")
    d = catalog_variant_doc(r)
    assert "attributes" not in d  # 빈 attrs 생략
    assert "price" not in d  # 비숫자 가격 생략
