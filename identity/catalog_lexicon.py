"""스포츠/아웃도어 카탈로그명 추출 — 도메인 사전(단일 출처). 로직 없음, 데이터만.

brand 컬럼은 오염값(KIDS/INTIMO/OUTLET)이 있어 신뢰 불가 → source 슬러그 기준.
값은 all_brands.csv 실측(2026-07-01, 30 브랜드).
"""

BRAND_KO = {
    "adidas": "아디다스", "kolping": "콜핑", "natgeo": "내셔널지오그래픽",
    "nepa": "네파", "nike": "나이키", "montbell": "몽벨", "arena": "아레나",
    "skechers": "스케쳐스", "northface": "노스페이스", "eider": "아이더",
    "proworldcup": "프로월드컵", "mizuno": "미즈노", "k2": "케이투",
    "millet": "밀레", "nb": "뉴발란스", "underarmour": "언더아머",
    "blackyak": "블랙야크", "outdoorproducts": "아웃도어프로덕츠", "vans": "반스",
    "worldcup": "월드컵", "prospecs": "프로스펙스", "starsports": "스타스포츠",
    "columbia": "컬럼비아", "crocs": "크록스", "redface": "레드페이스",
    "puma": "푸마", "westwood": "웨스트우드", "jansport": "잔스포츠",
    "fila": "휠라", "lecaf": "르까프",
}

# 이름에서 제거할 브랜드 별칭(영문/복합 표기). 슬러그+한글명은 코드에서 자동 추가.
BRAND_ALIASES = {
    "nike": ["nike"], "adidas": ["adidas"], "puma": ["puma"],
    "nb": ["newbalance", "new balance", "nb"], "fila": ["fila"],
    "crocs": ["crocs"], "vans": ["vans"], "skechers": ["skechers"],
    "columbia": ["columbia"], "underarmour": ["under armour", "underarmour", "ua"],
    "natgeo": ["national geographic", "natgeo"], "mizuno": ["mizuno"],
}

GENDER_MAP = {
    "남성": "M", "men": "M", "male": "M", "남": "M", "mens": "M", "man": "M",
    "여성": "W", "women": "W", "female": "W", "여": "W", "womens": "W", "woman": "W",
    "공용": "U", "unisex": "U", "남녀공용": "U", "남여공용": "U", "공통": "U",
    "키즈": "K", "kids": "K", "아동": "K", "kid": "K", "주니어": "K", "junior": "K",
}

GENDER_NAME_TOKENS = ["남녀공용", "남여공용", "남성", "여성", "공용", "키즈", "아동",
                      "주니어", "junior", "women", "womens", "mens", "men",
                      "kids", "unisex"]

# 긴 것 우선(부분 문자열 매칭 오검출 방지). 신발/의류/가방/모자 계열.
PRODUCT_TYPES = [
    "트레킹화", "테니스화", "농구화", "골프화", "러닝화", "등산화", "워킹화",
    "축구화", "실내화", "스니커즈", "슬리퍼", "샌들", "부츠", "신발",
    "다운재킷", "다운자켓", "바람막이", "패딩", "재킷", "자켓", "점퍼", "코트",
    "베이스레이어", "트레이닝팬츠", "조거팬츠", "래시가드", "수영복",
    "맨투맨", "후드티", "후드", "티셔츠", "니트", "셔츠", "폴로",
    "레깅스", "반바지", "쇼츠", "팬츠", "바지", "원피스", "스커트", "치마",
    "브라탑", "탑", "크로스백", "숄더백", "토트백", "백팩", "파우치", "지갑", "가방",
    "비니", "캡", "모자", "양말", "장갑", "머플러", "벨트",
]

COLOR_TOKENS = [
    "black", "블랙", "white", "화이트", "그레이", "gray", "grey", "네이비", "navy",
    "레드", "red", "블루", "blue", "그린", "green", "옐로우", "yellow", "핑크", "pink",
    "퍼플", "purple", "오렌지", "orange", "브라운", "brown", "베이지", "beige",
    "카키", "khaki", "실버", "silver", "골드", "gold", "민트", "mint", "코랄", "coral",
    "버건디", "burgundy", "차콜", "charcoal", "아이보리", "ivory", "라벤더", "lavender",
]

# 브랜드별 style_code 색상 접미사 규칙(모델 base 분리). 신뢰 가능한 브랜드만 등록.
# 미등록 → 이름 기반 폴백(catalog_group 처리).
#   sep: 구분자로 마지막 세그먼트 절단
#   tail: 끝 N자 절단 | tail_alpha: 끝 N자가 전부 알파벳일 때 절단 | tail_digit: 끝 N자가 숫자일 때
STYLECODE_SUFFIX = {
    "nike": {"sep": "-"},
    "lecaf": {"sep": "-"},
    "puma": {"sep": "_"},
    "arena": {"tail_alpha": 3},
    "k2": {"tail": 2},
    "eider": {"tail": 2},
    "columbia": {"tail_digit": 3},
    "redface": {"tail_digit": 3},
    "prospecs": {"tail_digit": 3},
}
