"""카테고리별 리뷰 차원(비정형 인사이트 추출/추천용) — 단일 출처.

비정형(리뷰·여론) 추출은 catalogs.csv 의 title_geo(canonical 모델) 단위로 돌리고,
그 모델의 product_type → 매크로 카테고리 → 아래 차원(dimension) 으로 LLM 추출한다.
`driver=True` 차원은 추천 로직의 근거(예: size_fit 리뷰가 '작게 나옴' → 한 사이즈 업 추천).

  from catalog_review_dims import dims_for
  dims_for("축구화")  # → footwear 차원 + 공통 차원
"""

# product_type → 매크로 카테고리
CATEGORY_OF = {
    # footwear
    "신발": "footwear", "축구화": "footwear", "러닝화": "footwear", "등산화": "footwear",
    "트레킹화": "footwear", "워킹화": "footwear", "트레일러닝화": "footwear", "테니스화": "footwear",
    "농구화": "footwear", "골프화": "footwear", "스니커즈": "footwear", "슬리퍼": "footwear",
    "샌들": "footwear", "부츠": "footwear", "클로그": "footwear", "실내화": "footwear", "슬립온": "footwear",
    # top
    "티셔츠": "top", "맨투맨": "top", "후드티": "top", "후드": "top", "니트": "top", "셔츠": "top",
    "폴로": "top", "베이스레이어": "top", "브라탑": "top", "탑": "top", "래쉬가드": "top", "래시가드": "top", "저지": "top",
    # bottom
    "팬츠": "bottom", "바지": "bottom", "반바지": "bottom", "쇼츠": "bottom", "레깅스": "bottom",
    "스커트": "bottom", "치마": "bottom", "트레이닝팬츠": "bottom", "조거팬츠": "bottom", "숏팬츠": "bottom",
    # outerwear
    "재킷": "outerwear", "자켓": "outerwear", "점퍼": "outerwear", "코트": "outerwear",
    "바람막이": "outerwear", "패딩": "outerwear", "플리스": "outerwear", "아노락": "outerwear",
    "집업": "outerwear", "다운재킷": "outerwear", "다운자켓": "outerwear",
    # dress
    "원피스": "dress",
    # bag
    "백팩": "bag", "토트백": "bag", "크로스백": "bag", "숄더백": "bag", "가방": "bag",
    "파우치": "bag", "지갑": "bag",
    # headwear
    "캡": "headwear", "모자": "headwear", "비니": "headwear", "햇": "headwear",
    # accessory
    "양말": "accessory", "장갑": "accessory", "머플러": "accessory", "벨트": "accessory",
    # swim
    "수영복": "swim", "수경": "swim",
}

# 매크로 카테고리 → 리뷰 차원 [(key, 한글 라벨, driver=추천 근거 여부)]
REVIEW_DIMS = {
    "footwear": [
        ("size_fit", "사이즈·핏(정사이즈/작게/크게)", True),
        ("width", "발볼·발등 편안함", True),
        ("comfort", "착화감·쿠셔닝", False),
        ("durability", "내구성(밑창 마모/마감)", False),
        ("weight", "무게", False),
        ("breathability", "통기성", False),
        ("use_fit", "용도 적합성(러닝/일상/코트)", False),
        ("looks", "디자인·실물 차이", False),
    ],
    "top": [
        ("size_fit", "사이즈·핏(정사이즈/오버핏)", True),
        ("material", "소재·촉감", False),
        ("stretch", "신축·활동성", False),
        ("warmth", "보온·통기(계절 적합)", False),
        ("care", "세탁·관리(수축/이염)", False),
        ("sheer", "비침", False),
        ("looks", "디자인·실물색", False),
    ],
    "bottom": [
        ("size_fit", "사이즈·핏(허리/기장)", True),
        ("material", "소재·촉감", False),
        ("stretch", "신축·활동성", False),
        ("care", "세탁·관리", False),
        ("looks", "디자인·실물색", False),
    ],
    "outerwear": [
        ("size_fit", "사이즈·핏(레이어링 여유)", True),
        ("warmth", "보온성", False),
        ("windproof", "방풍·방수", False),
        ("weight", "무게·수납성", False),
        ("material", "소재·마감", False),
        ("looks", "디자인·실물", False),
    ],
    "dress": [
        ("size_fit", "사이즈·핏", True),
        ("material", "소재·촉감", False),
        ("looks", "디자인·실물색", False),
        ("care", "세탁·관리", False),
    ],
    "bag": [
        ("capacity", "수납·용량", False),
        ("durability", "내구성(지퍼/봉제)", False),
        ("weight", "무게·휴대성", False),
        ("pockets", "수납 편의(포켓 구성)", False),
        ("waterproof", "방수·생활방수", False),
        ("looks", "디자인·실물", False),
    ],
    "headwear": [
        ("size_fit", "사이즈·조절(둘레)", True),
        ("material", "소재·착용감", False),
        ("looks", "디자인·실물색", False),
        ("durability", "내구성·변형", False),
    ],
    "accessory": [
        ("size_fit", "사이즈", True),
        ("material", "소재·착용감", False),
        ("durability", "내구성", False),
        ("looks", "디자인", False),
    ],
    "swim": [
        ("size_fit", "사이즈·핏(밀착감)", True),
        ("material", "소재·신축", False),
        ("durability", "내구성(염소 견딤)", False),
        ("looks", "디자인·실물", False),
    ],
}

# 전 카테고리 공통 차원(리뷰에 흔히 나오는 축)
COMMON_DIMS = [
    ("value", "가성비", False),
    ("authenticity", "정품·신뢰", False),
]

DEFAULT_CATEGORY = "top"   # 유형 미상 시 폴백(가장 흔한 의류 차원)


def macro_category(product_type):
    """product_type → 매크로 카테고리(미상이면 DEFAULT_CATEGORY)."""
    return CATEGORY_OF.get((product_type or "").strip(), DEFAULT_CATEGORY)


def dims_for(product_type):
    """product_type → [(key, label, driver)] 리뷰 차원(카테고리 고유 + 공통)."""
    return REVIEW_DIMS[macro_category(product_type)] + COMMON_DIMS
