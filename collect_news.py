from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import feedparser
from dateutil import parser as date_parser

KST = ZoneInfo("Asia/Seoul")
OUTPUT = Path("index.html")
STATE_FILE = Path("article_state.json")
ARCHIVE_FILE = Path("news_archive.json")
ARCHIVE_DAYS = 30
BACKFILL_DATES_PER_RUN = 2
MAX_PER_GROUP_PER_LANGUAGE = 12

GROUPS = [
    ("현대건설", [
        # 현대건설은 원전 분야로 한정하지 않고 회사 전체 동향을 수집
        '"현대건설"',
        '"현대건설" 건설',
        '"현대건설" 기술',
        '"현대건설" 로봇',
        '"현대건설" 드론',
        '"현대건설" 안전',
        '"현대건설" 수주',
        '"현대건설" 해외',
        '"현대건설" 원전',
        '"현대건설" 원자력',
        '"Hyundai Engineering & Construction"',
        '"Hyundai E&C"',
        '"Hyundai E&C" technology',
        '"Hyundai E&C" construction',
        '"Hyundai E&C" project',
        '"Hyundai E&C" nuclear',
        "HDEC construction",
        "HDEC nuclear",
    ]),
    ("한수원·한국수력원자력", [
        "한수원 원전", "한국수력원자력", "KHNP nuclear",
        "KHNP reactor", "KHNP nuclear project",
    ]),
    ("한전·한국전력", [
        "한전 원전", "한국전력 원자력", "KEPCO nuclear",
        "KEPCO reactor", "KEPCO nuclear project",
    ]),
    ("원전·원자력", [
        "원전", "원자력", "원자력발전", "원자력발전소",
        "대형원전", "신규 원전", "원전 건설", "원전 프로젝트", "원전 수출",
    ]),
    ("SMR", [
        "SMR", "소형모듈원자로", '"Small Modular Reactor"',
        "차세대원자로", '"Advanced Reactor"', "Microreactor",
    ]),
    ("Nuclear Power·Nuclear Energy", [
        '"Nuclear Power"', '"Nuclear Energy"', '"Nuclear Power Plant"',
        '"Nuclear Construction"', '"Nuclear Project"',
        '"Nuclear New Build"', '"New Nuclear Build"',
    ]),
    ("Holtec", [
        "Holtec nuclear", '"Holtec International"', "SMR-300",
        "Palisades nuclear", '"Oyster Creek" SMR',
    ]),
    ("TerraPower", [
        "TerraPower", "Natrium reactor", "Natrium nuclear",
        "Kemmerer nuclear", "TerraPower nuclear project",
    ]),
    ("Westinghouse", [
        "Westinghouse nuclear", '"Westinghouse Electric Company"',
        "AP1000", "AP300", "AP1000 construction",
    ]),
    ("Fermi America", [
        '"Fermi America"', '"Project Matador"', "HyperGrid nuclear",
        '"Fermi America" AP1000', "Amarillo nuclear", '"Carson County" nuclear',
    ]),
]

# 블로그·개인 게시물·커뮤니티성 출처 제외
BLOCKED_SOURCE_KEYWORDS = {
    "blog", "블로그", "tistory", "티스토리", "medium",
    "substack", "brunch", "브런치", "cafe", "카페",
    "wordpress", "tumblr", "reddit", "quora",
}

BLOCKED_HOST_KEYWORDS = {
    "blog.naver.com", "m.blog.naver.com", "tistory.com",
    "medium.com", "substack.com", "brunch.co.kr",
    "cafe.naver.com", "wordpress.com", "reddit.com",
    "quora.com",
}


# 광고·협찬·보도자료 배포성 콘텐츠 제외
BLOCKED_AD_KEYWORDS = {
    "광고", "협찬", "스폰서", "sponsored", "advertisement",
    "advertorial", "promoted", "유료광고", "paid content",
}

BLOCKED_PRESS_RELEASE_SOURCES = {
    "pr newswire", "business wire", "globe newswire",
    "ein presswire", "accesswire", "newsfile",
}


# 원자력 뉴스와 무관한 도박·복권·성인·불법 홍보성 콘텐츠 제외
BLOCKED_HARMFUL_KEYWORDS = {
    # 도박·복권·베팅
    "토토", "스포츠토토", "프로토", "로또", "복권", "카지노",
    "바카라", "슬롯", "경마", "경륜", "경정", "베팅", "배팅",
    "잭팟", "당첨번호", "파워볼", "사설토토", "먹튀",
    "gambling", "casino", "betting", "sportsbook", "lottery",
    "jackpot", "poker", "slot machine",

    # 성인·불법·유해 홍보
    "성인사이트", "성인 사이트", "야동", "조건만남", "불법대출",
    "불법 도박", "불법도박", "마약 판매", "해킹 판매",
    "adult site", "porn", "escort", "illegal gambling",

    # 이번에 확인된 무관 기사
    "세븐일레븐", "7-eleven", "7eleven",
}

BLOCKED_HARMFUL_SOURCE_KEYWORDS = {
    "토토", "카지노", "바카라", "베팅", "배팅", "로또", "복권",
    "성인", "먹튀", "gambling", "casino", "sportsbook",
    "betting", "lottery", "adult",
}


# 증권사 리포트·주가 전망·투자의견 관련 기사 제외
BLOCKED_STOCK_KEYWORDS = {
    "목표주가", "투자의견", "매수 유지", "매도 유지",
    "중립 유지", "보유 유지", "매수 의견", "매도 의견",
    "목표가", "적정주가", "주가 전망", "주가 상승",
    "주가 하락", "증권사", "리포트", "컨센서스",
    "실적 전망", "어닝", "밸류에이션", "시가총액",
    "주식", "종목", "코스피", "코스닥",
    "target price", "price target", "buy rating",
    "sell rating", "hold rating", "overweight",
    "underweight", "stock outlook", "equity research",
    "brokerage", "analyst report",
}


@dataclass
class Article:
    title: str
    link: str
    published: datetime
    language: str
    group: str
    publisher: str
    image: str
    source_url: str


def period(now: datetime) -> tuple[datetime, datetime]:
    """기존 금일 기사 구간을 반환합니다."""
    now = now.astimezone(KST)
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)

    if now < end:
        end -= timedelta(days=1)

    weekday = end.weekday()
    if weekday == 0:
        start = end - timedelta(days=3)
    elif weekday in (1, 2, 3, 4):
        start = end - timedelta(days=1)
    else:
        end -= timedelta(days=1 if weekday == 5 else 2)
        start = end - timedelta(days=1)

    return start, end


def brief_periods(now: datetime) -> dict[str, tuple[datetime, datetime]]:
    """전일·금일·익일 구간을 반환합니다."""
    today_start, today_end = period(now)
    return {
        "전일": (today_start - timedelta(days=1), today_start),
        "금일": (today_start, today_end),
        "익일": (today_end, today_end + timedelta(days=1)),
    }


def google_news_url(query: str, language: str) -> str:
    if language == "ko":
        return (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
        )
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )


def split_title_and_publisher(raw_title: str) -> tuple[str, str]:
    raw_title = html.unescape(raw_title or "").strip()
    match = re.match(r"^(.*)\s+-\s+([^-]{2,100})$", raw_title)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return raw_title, ""


def extract_image(entry) -> str:
    for field in ("media_content", "media_thumbnail"):
        values = getattr(entry, field, None)
        if values:
            for value in values:
                if isinstance(value, dict) and value.get("url"):
                    return value["url"]

    summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.I)
    return match.group(1) if match else ""


def normalized(title: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z가-힣]+", " ", title.lower()).split())


ENTITY_ALIASES = {
    "새울원전": "새울원자력",
    "새울 원전": "새울원자력",
    "새울본부": "새울원자력",
    "한국수력원자력": "한수원",
    "한국 전력": "한전",
    "한국전력": "한전",
    "현대건설": "hdec",
    "hyundai e c": "hdec",
    "hyundai engineering construction": "hdec",
}

ACTION_ALIASES = {
    "무상 교체": "교체지원",
    "무료 교체": "교체지원",
    "조명 교체 지원": "교체지원",
    "교체 지원": "교체지원",
    "교체해준다": "교체지원",
    "설치 지원": "설치지원",
    "업무협약": "협약",
    "양해각서": "협약",
    "mou": "협약",
    "체결": "협약",
    "착공": "건설시작",
    "첫 삽": "건설시작",
    "준공": "건설완료",
}

OBJECT_ALIASES = {
    "고효율 led 조명": "led조명",
    "고효율 조명": "led조명",
    "엘이디 조명": "led조명",
    "led 조명": "led조명",
    "소형 모듈 원자로": "smr",
    "small modular reactor": "smr",
    "원자력 발전소": "원전",
    "원자력발전소": "원전",
}

STOPWORDS = {
    "관련", "대한", "통해", "위해", "추진", "지원", "사업",
    "밝혀", "발표", "나서", "진행", "계획", "예정", "이번",
    "제공", "실시", "본격", "개최", "확대", "강화",
    "the", "a", "an", "and", "for", "to", "of", "in", "on",
}

# 제목 표현이 달라도 동일 사건으로 묶기 위한 개념 사전
EVENT_CONCEPTS = {
    "entity:새울원자력": {
        "새울원자력", "새울원전", "새울본부",
    },
    "entity:한수원": {
        "한수원", "한국수력원자력", "khnp",
    },
    "entity:한전": {
        "한전", "한국전력", "kepco",
    },
    "target:소상공인": {
        "소상공인", "소상공인들",
    },
    "location:울주군": {
        "울주군", "울산 울주",
    },
    "object:led조명": {
        "led조명", "led 조명", "고효율조명", "고효율 조명", "엘이디조명",
    },
    "action:교체지원": {
        "교체지원", "교체 지원", "무상교체", "무상 교체",
        "무료교체", "무료 교체", "교체해준다",
    },
    "action:협약": {
        "업무협약", "양해각서", "mou", "협약체결", "협약 체결",
    },
    "action:건설시작": {
        "착공", "첫삽", "첫 삽", "건설시작",
    },
    "action:건설완료": {
        "준공", "완공", "건설완료",
    },
    "entity:현대건설": {
        "현대건설", "hdec", "hyundaie&c", "hyundaiengineeringconstruction",
    },
    "object:살수드론": {
        "살수드론", "살수 드론", "물뿌리는드론", "물 뿌리는 드론",
        "드론살수", "드론 살수",
    },
    "object:살수로봇": {
        "살수로봇", "살수 로봇", "물분사로봇", "물 분사 로봇",
    },
    "location:철거현장": {
        "철거현장", "철거 현장", "해체현장", "해체 현장",
    },
    "action:투입": {
        "투입", "도입", "적용", "배치", "운영",
    },
    "topic:신규원전": {
        "신규원전", "신규 원전", "새원전", "새 원전",
        "원전신설", "원전 신설",
    },
    "topic:산업용전기요금": {
        "산업용전기요금", "산업용 전기요금", "산업용전력요금",
        "산업용 전력요금", "산업용전기", "산업용 전기",
    },
    "action:지역별차등요금": {
        "차등인하", "차등 인하", "차등화", "차등 요금",
        "지역별차등", "지역별 차등", "전국4등급", "전국 4등급",
        "권역별차등", "권역별 차등",
    },
    "action:신규원전추진": {
        "공론화착수", "공론화 착수", "속도낸다", "속도 낸다",
        "속도", "추진", "신설추진", "신설 추진",
    },
    "topic:살수드론": {
        "살수드론", "살수 드론", "방수드론", "방수 드론",
        "소방드론", "소방 드론", "water spraying drone",
        "firefighting drone", "water-spraying drone",
    },
}


def strip_korean_particle(token: str) -> str:
    """간단한 한국어 조사를 제거해 핵심 명사를 비교하기 쉽게 합니다."""
    particles = (
        "으로부터", "에게서", "에서는", "으로는", "까지도",
        "으로", "에서", "에게", "한테", "께서", "부터", "까지",
        "에는", "에도", "이라", "라고", "과의", "와의",
        "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "로",
    )
    for particle in particles:
        if token.endswith(particle) and len(token) > len(particle) + 1:
            return token[:-len(particle)]
    return token


def semantic_normalized(title: str) -> str:
    text = normalized(title)

    for source, target in ENTITY_ALIASES.items():
        text = text.replace(source, target)
    for source, target in ACTION_ALIASES.items():
        text = text.replace(source, target)
    for source, target in OBJECT_ALIASES.items():
        text = text.replace(source, target)

    tokens = [strip_korean_particle(token) for token in text.split()]
    return " ".join(tokens)


def keyword_set(title: str) -> set[str]:
    return {
        token
        for token in semantic_normalized(title).split()
        if len(token) >= 2 and token not in STOPWORDS
    }


def event_concepts(title: str) -> set[str]:
    """
    제목에서 주체·대상·지역·행위 개념을 추출합니다.
    문장 구조가 달라도 같은 사건이면 공통 개념이 남습니다.
    """
    raw = normalized(title).replace(" ", "")
    semantic = semantic_normalized(title).replace(" ", "")
    combined = f"{raw} {semantic}"

    concepts: set[str] = set()
    for concept, variants in EVENT_CONCEPTS.items():
        if any(variant.replace(" ", "") in combined for variant in variants):
            concepts.add(concept)
    return concepts


def compact_title(title: str) -> str:
    """띄어쓰기·문장부호 차이를 없앤 비교용 제목입니다."""
    return re.sub(r"[^0-9a-z가-힣]+", "", semantic_normalized(title))


def character_ngrams(title: str, size: int = 2) -> set[str]:
    compact = compact_title(title)
    if len(compact) < size:
        return {compact} if compact else set()
    return {
        compact[index:index + size]
        for index in range(len(compact) - size + 1)
    }


def character_ngram_similarity(title_a: str, title_b: str) -> float:
    grams_a = character_ngrams(title_a)
    grams_b = character_ngrams(title_b)
    if not grams_a or not grams_b:
        return 0.0

    intersection = len(grams_a & grams_b)
    return intersection / min(len(grams_a), len(grams_b))


def meaningful_keywords(title: str) -> set[str]:
    """
    기사 사건을 나타내는 핵심 단어를 추출합니다.
    언론사별 수식어·따옴표·조사가 달라도 같은 보도자료성 기사를 묶습니다.
    """
    generic = {
        "현대건설", "한수원", "한전", "원전", "원자력",
        "도입", "투입", "적용", "운영", "개발", "공개",
        "기술", "현장", "건설", "사업", "시스템",
    }
    return {
        token for token in keyword_set(title)
        if token not in generic and len(token) >= 2
    }


def same_press_release_event(title_a: str, title_b: str) -> bool:
    """
    동일 보도자료를 여러 언론사가 제목만 바꿔 보도한 경우를 판정합니다.
    """
    concepts_a = event_concepts(title_a)
    concepts_b = event_concepts(title_b)
    shared_concepts = concepts_a & concepts_b

    shared_entity = any(item.startswith("entity:") for item in shared_concepts)
    shared_object = any(item.startswith("object:") for item in shared_concepts)
    shared_location = any(item.startswith("location:") for item in shared_concepts)
    shared_action = any(item.startswith("action:") for item in shared_concepts)

    # 현대건설 + 살수드론/살수로봇 + 철거현장 + 투입처럼
    # 핵심 사건 요소가 3개 이상 같으면 동일 기사로 처리합니다.
    if shared_entity and len(shared_concepts) >= 3:
        if shared_object or shared_location or shared_action:
            return True

    keys_a = meaningful_keywords(title_a)
    keys_b = meaningful_keywords(title_b)
    common = keys_a & keys_b

    # 제목 표현이 달라도 핵심 사건 단어가 3개 이상 일치
    if len(common) >= 3:
        return True

    # 짧은 제목이나 따옴표형 제목은 문자 2-gram 포함률로 보완
    if character_ngram_similarity(title_a, title_b) >= 0.62:
        return True

    return False


def semantic_duplicate_score(title_a: str, title_b: str) -> float:
    norm_a = semantic_normalized(title_a)
    norm_b = semantic_normalized(title_b)

    sequence_score = SequenceMatcher(None, norm_a, norm_b).ratio()

    keys_a = keyword_set(title_a)
    keys_b = keyword_set(title_b)
    if not keys_a or not keys_b:
        return sequence_score

    intersection = len(keys_a & keys_b)
    union = len(keys_a | keys_b)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(keys_a), len(keys_b))

    concepts_a = event_concepts(title_a)
    concepts_b = event_concepts(title_b)
    concept_intersection = len(concepts_a & concepts_b)
    concept_containment = (
        concept_intersection / min(len(concepts_a), len(concepts_b))
        if concepts_a and concepts_b
        else 0.0
    )

    ngram_score = character_ngram_similarity(title_a, title_b)

    return max(
        sequence_score,
        jaccard,
        containment,
        concept_containment,
        ngram_score,
    )


def is_same_event(title_a: str, title_b: str) -> bool:
    """
    제목의 문구가 달라도 주체·대상·행위가 같으면 동일 사건으로 판단합니다.
    """
    concepts_a = event_concepts(title_a)
    concepts_b = event_concepts(title_b)
    shared = concepts_a & concepts_b

    has_entity = any(item.startswith("entity:") for item in shared)
    has_action = any(item.startswith("action:") for item in shared)
    has_subject_detail = any(
        item.startswith(("target:", "object:", "location:"))
        for item in shared
    )

    # 주체 + 행위 + 대상/목적물/지역이 겹치면 같은 사건
    if has_entity and has_action and has_subject_detail and len(shared) >= 3:
        return True

    # 신규 원전 추진과 산업용 전기요금 차등화가 함께 언급된
    # 동일 정책 발표·브리핑 기사는 제목 순서와 표현이 달라도 하나로 처리
    policy_bundle = {
        "topic:신규원전",
        "topic:산업용전기요금",
        "action:지역별차등요금",
    }
    if policy_bundle.issubset(shared):
        return True

    # '살수드론'처럼 희소성이 높은 핵심 기술·장비명이 동일하면
    # 언론사와 제목 표현이 달라도 같은 보도자료 기반 기사로 처리
    if "topic:살수드론" in shared:
        return True

    # 특정 개념이 4개 이상 겹치는 경우도 동일 사건
    if len(shared) >= 4:
        return True

    return False


def is_duplicate(article: Article, selected: list[Article]) -> bool:
    for existing in selected:
        time_gap = abs(
            (article.published - existing.published).total_seconds()
        )
        score = semantic_duplicate_score(article.title, existing.title)

        # 동일 보도자료를 여러 언론사가 제목만 바꿔 보도한 경우
        if time_gap <= 72 * 60 * 60 and same_press_release_event(
            article.title,
            existing.title,
        ):
            return True

        # 명확히 같은 사건이면 언론사와 제목 표현이 달라도 중복 처리
        if time_gap <= 72 * 60 * 60 and is_same_event(
            article.title,
            existing.title,
        ):
            return True

        # 제목 및 핵심 키워드 유사도 기준
        if score >= 0.82:
            return True

        # 72시간 이내 보도는 핵심 내용이 65% 이상 겹치면 중복 처리
        if time_gap <= 72 * 60 * 60 and score >= 0.65:
            common_keywords = (
                keyword_set(article.title)
                & keyword_set(existing.title)
            )
            if len(common_keywords) >= 3:
                return True

    return False

def order_similar_articles(articles: list[Article]) -> list[Article]:
    """Place related headlines next to each other while keeping newer clusters first."""
    if not articles:
        return []

    remaining = sorted(
        articles,
        key=lambda article: -article.published.timestamp(),
    )
    ordered: list[Article] = []

    while remaining:
        anchor = remaining.pop(0)
        cluster = [anchor]
        related: list[tuple[float, Article]] = []
        unrelated: list[Article] = []

        for article in remaining:
            score = semantic_duplicate_score(
                anchor.title,
                article.title,
            )

            # 85% 이상은 앞 단계에서 중복 제거됨.
            # 45% 이상이면 같은 이슈·유사 항목으로 보고 연속 배치.
            if score >= 0.45:
                related.append((score, article))
            else:
                unrelated.append(article)

        related.sort(
            key=lambda item: (
                -item[0],
                -item[1].published.timestamp(),
            )
        )

        cluster.extend(article for _, article in related)
        ordered.extend(cluster)
        remaining = unrelated

    return ordered


def is_news_source(
    publisher: str,
    source_url: str,
    title: str = "",
) -> bool:
    publisher_lower = publisher.lower()
    title_lower = title.lower()
    host = urlparse(source_url).netloc.lower()

    if any(keyword in publisher_lower for keyword in BLOCKED_SOURCE_KEYWORDS):
        return False
    if any(keyword in host for keyword in BLOCKED_HOST_KEYWORDS):
        return False
    if any(keyword in title_lower for keyword in BLOCKED_AD_KEYWORDS):
        return False
    if any(keyword in publisher_lower for keyword in BLOCKED_AD_KEYWORDS):
        return False
    if any(keyword in publisher_lower for keyword in BLOCKED_PRESS_RELEASE_SOURCES):
        return False
    if any(keyword in title_lower for keyword in BLOCKED_HARMFUL_KEYWORDS):
        return False
    if any(keyword in publisher_lower for keyword in BLOCKED_HARMFUL_SOURCE_KEYWORDS):
        return False
    if any(keyword in host for keyword in BLOCKED_HARMFUL_SOURCE_KEYWORDS):
        return False
    if any(keyword in title_lower for keyword in BLOCKED_STOCK_KEYWORDS):
        return False
    if any(keyword in publisher_lower for keyword in BLOCKED_STOCK_KEYWORDS):
        return False

    # 출처명이 없는 항목은 제외
    if not publisher.strip():
        return False

    return True


def parse_entry(entry, language: str, group: str) -> Article | None:
    raw_date = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if not raw_date:
        return None

    try:
        published = date_parser.parse(raw_date).astimezone(KST)
    except Exception:
        return None

    title, publisher_from_title = split_title_and_publisher(
        getattr(entry, "title", "")
    )
    link = getattr(entry, "link", "").strip()

    source = getattr(entry, "source", {})
    publisher = source.get("title", "") if isinstance(source, dict) else ""
    source_url = source.get("href", "") if isinstance(source, dict) else ""
    publisher = publisher.strip() or publisher_from_title

    if not title or not link or not is_news_source(publisher, source_url, title):
        return None

    return Article(
        title=title,
        link=link,
        published=published,
        language=language,
        group=group,
        publisher=publisher,
        image=extract_image(entry),
        source_url=source_url,
    )


def collect(start: datetime, end: datetime) -> list[Article]:
    all_selected: list[Article] = []

    for group, queries in GROUPS:
        for language in ("ko", "en"):
            found: list[Article] = []

            for query in queries:
                feed = feedparser.parse(google_news_url(query, language))
                for entry in feed.entries:
                    article = parse_entry(entry, language, group)
                    if article and start <= article.published < end:
                        found.append(article)

            found.sort(key=lambda article: -article.published.timestamp())

            selected_group: list[Article] = []

            # 현대건설은 원전뿐 아니라 기술·안전·로봇·수주 등
            # 회사 전체 동향을 보여주기 위해 기사 수를 더 넉넉하게 유지합니다.
            group_limit = (
                20 if group == "현대건설"
                else MAX_PER_GROUP_PER_LANGUAGE
            )

            for article in found:
                if len(selected_group) >= group_limit:
                    break
                if is_duplicate(article, all_selected + selected_group):
                    continue
                selected_group.append(article)

            all_selected.extend(selected_group)

    return all_selected


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def render_card(article: Article, number: int, is_new: bool = False) -> str:
    if article.image:
        image_html = (
            f'<img src="{escape(article.image)}" alt="" '
            'loading="lazy" referrerpolicy="no-referrer">'
        )
    else:
        image_html = '<div class="no-image">NUCLEAR<br>NEWS</div>'

    new_badge = '<span class="new-badge">NEW</span>' if is_new else ''
    translate_button = (
        '<button class="translate-button" type="button" aria-label="영문 기사 번역">번역</button>'
        if article.language == "en"
        else ""
    )
    search_text = ' '.join([article.title, article.publisher, article.group]).lower()

    return f"""
<article class="preview-card{' new-article' if is_new else ''}"
  data-url="{escape(article.link)}"
  data-title="{escape(article.title)}"
  data-publisher="{escape(article.publisher)}"
  data-group="{escape(article.group)}"
  data-language="{escape(article.language)}"
  data-search="{escape(search_text)}"
  tabindex="0" role="link">
  <div class="article-number">{number}</div>
  <div class="preview-copy">
    <div class="publisher">{escape(article.publisher)}</div>
    <div class="headline">{new_badge}{escape(article.title)}</div>
    <div class="status-line">
      <span class="unread-label">미확인</span>
      <span class="read-label">확인</span>
      <span class="important-label">중요</span>
      {translate_button}
    </div>
  </div>
  <div class="card-side">
    <button class="important-button" type="button" aria-label="중요 기사">★</button>
    <div class="preview-image">{image_html}</div>
  </div>
</article>
"""

def render_group(group: str, articles: list[Article]) -> str:
    if not articles:
        return ""

    cards = "".join(
        render_card(article, index)
        for index, article in enumerate(articles, start=1)
    )

    return f'''
<section class="news-group">
  <div class="group-title">{escape(group)}</div>
  <div class="article-stack">{cards}</div>
</section>
'''


def render_group_unified(
    group: str,
    articles: list[Article],
    new_urls: set[str] | None = None,
) -> str:
    if not articles:
        return ''
    new_urls = new_urls or set()
    korean_articles = order_similar_articles([a for a in articles if a.language == 'ko'])
    english_articles = order_similar_articles([a for a in articles if a.language == 'en'])
    ordered_articles = korean_articles + english_articles
    cards = ''.join(
        render_card(article, index, article.link in new_urls)
        for index, article in enumerate(ordered_articles, start=1)
    )
    return f"""
<section class="news-group" data-group="{escape(group)}">
  <div class="group-title"><span class="group-square"></span><span>{escape(group)}</span><span class="group-count">{len(ordered_articles)}건</span></div>
  <div class="article-stack">{cards}</div>
</section>
"""


def render_news_sections(articles: list[Article], new_urls: set[str] | None = None) -> str:
    grouped: dict[str, list[Article]] = {name: [] for name, _ in GROUPS}
    for article in articles:
        grouped[article.group].append(article)
    return ''.join(
        render_group_unified(group, grouped[group], new_urls)
        for group, _ in GROUPS
    )


def load_previous_urls() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return {str(url) for url in data.get('urls', [])}
    except (OSError, ValueError, TypeError):
        return set()


def save_current_urls(urls: set[str], generated_at: datetime) -> None:
    payload = {'updated_at': generated_at.isoformat(), 'urls': sorted(urls)}
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')



def article_to_dict(article: Article) -> dict:
    return {
        "title": article.title,
        "link": article.link,
        "published": article.published.isoformat(),
        "language": article.language,
        "group": article.group,
        "publisher": article.publisher,
        "image": article.image,
        "source_url": article.source_url,
    }


def article_from_dict(data: dict) -> Article | None:
    try:
        title = str(data.get("title", ""))
        publisher = str(data.get("publisher", ""))
        source_url = str(data.get("source_url", ""))

        # 과거 archive에 이미 저장된 무관·광고·유해 기사도 표시하지 않음
        if not is_news_source(publisher, source_url, title):
            return None

        return Article(
            title=title,
            link=str(data.get("link", "")),
            published=date_parser.parse(str(data.get("published", ""))).astimezone(KST),
            language=str(data.get("language", "")),
            group=str(data.get("group", "")),
            publisher=publisher,
            image=str(data.get("image", "")),
            source_url=source_url,
        )
    except Exception:
        return None


def load_archive() -> dict[str, dict]:
    if not ARCHIVE_FILE.exists():
        return {}
    try:
        data = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def update_archive(
    archive: dict[str, dict],
    periods: dict[str, tuple[datetime, datetime]],
    articles_by_period: dict[str, list[Article]],
    generated_at: datetime,
) -> dict[str, dict]:
    # 전일과 금일 구간을 날짜별로 저장합니다.
    for label in ("전일", "금일"):
        start, end = periods[label]
        key = end.strftime("%Y-%m-%d")
        archive[key] = {
            "label": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "updated_at": generated_at.isoformat(),
            "articles": [article_to_dict(a) for a in articles_by_period[label]],
        }

    # 최근 30개 날짜만 유지합니다.
    keep_keys = sorted(archive.keys(), reverse=True)[:ARCHIVE_DAYS]
    return {key: archive[key] for key in sorted(keep_keys)}



def archive_window_for_date(report_date) -> tuple[datetime, datetime]:
    """
    선택한 보고일의 기사 구간을 계산합니다.
    월요일은 금요일 06:00부터 월요일 06:00까지,
    화~금요일은 전일 06:00부터 당일 06:00까지입니다.
    """
    end = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        6, 0, 0,
        tzinfo=KST,
    )
    if report_date.weekday() == 0:
        start = end - timedelta(days=3)
    else:
        start = end - timedelta(days=1)
    return start, end


def backfill_missing_archive_dates(
    archive: dict[str, dict],
    generated_at: datetime,
) -> dict[str, dict]:
    """
    기능 적용 이전 날짜를 한 번에 너무 많이 수집하지 않도록
    실행할 때마다 최대 2개 보고일을 과거 방향으로 채웁니다.
    주말은 월요일 보고에 포함되므로 별도 보고일로 만들지 않습니다.
    """
    today = generated_at.astimezone(KST).date()
    candidates = []

    for offset in range(1, ARCHIVE_DAYS + 1):
        report_date = today - timedelta(days=offset)

        # 토요일·일요일은 월요일 보고에 포함
        if report_date.weekday() >= 5:
            continue

        key = report_date.isoformat()
        if key not in archive:
            candidates.append(report_date)

    # 가장 최근에 비어 있는 날짜부터 조금씩 채움
    for report_date in candidates[:BACKFILL_DATES_PER_RUN]:
        start, end = archive_window_for_date(report_date)
        articles = collect(start, end)
        key = report_date.isoformat()
        archive[key] = {
            "label": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "updated_at": generated_at.isoformat(),
            "articles": [article_to_dict(a) for a in articles],
        }
        print(f"Backfilled archive date {key}: {len(articles)} articles")

    keep_keys = sorted(archive.keys(), reverse=True)[:ARCHIVE_DAYS]
    return {key: archive[key] for key in sorted(keep_keys)}


def save_archive(archive: dict[str, dict]) -> None:
    ARCHIVE_FILE.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def archive_panels_html(archive: dict[str, dict], new_urls: set[str]) -> str:
    panels = []
    for key in sorted(archive.keys(), reverse=True):
        item = archive[key]
        try:
            start = date_parser.parse(item["start"]).astimezone(KST)
            end = date_parser.parse(item["end"]).astimezone(KST)
        except Exception:
            continue

        articles = []
        for raw in item.get("articles", []):
            article = article_from_dict(raw)
            if article:
                articles.append(article)

        sections = render_news_sections(articles, new_urls)
        panels.append(f"""
<section class="tab-panel archive-panel" id="archive-{escape(key)}" data-archive-date="{escape(key)}">
  <div class="period-card">
    <strong>{end:%Y. %-m. %-d.}</strong>
    <span>{start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)</span>
  </div>
  <div class="language-section">
    {sections or '<div class="empty">해당 날짜에 저장된 뉴스 기사가 없습니다.</div>'}
  </div>
</section>
""")
    return "".join(panels)


def build_html(
    periods: dict[str, tuple[datetime, datetime]],
    articles_by_period: dict[str, list[Article]],
    generated_at: datetime,
    new_urls: set[str],
    archive: dict[str, dict],
) -> str:
    buttons = "".join(
        f'<button class="tab-button{" active" if label == "금일" else ""}" data-tab="{escape(label)}">{escape(label)}</button>'
        for label in ("전일", "금일", "익일")
    )

    panels: list[str] = []
    for label in ("전일", "금일", "익일"):
        start, end = periods[label]
        sections = render_news_sections(articles_by_period[label], new_urls)
        panel_class = "tab-panel active" if label == "금일" else "tab-panel"

        note = ""
        if label == "익일" and generated_at < end:
            note = (
                '<div class="partial-note">'
                f'현재 {generated_at:%Y. %-m. %-d. %H:%M}까지 확인된 기사입니다. '
                '30분마다 새 기사가 추가됩니다.'
                '</div>'
            )

        panels.append(f'''
<section class="{panel_class}" id="tab-{escape(label)}">
  <div class="period-card">
    <strong>{escape(label)}</strong>
    <span>{start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)</span>
  </div>
  {note}
  <div class="language-section">
    {sections or '<div class="empty">해당 기간에 수집된 뉴스 기사가 없습니다.</div>'}
  </div>
</section>
''')

    panels_html = "".join(panels) + archive_panels_html(archive, new_urls)

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#b2c7d9">
<title>원자력 주요기사</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #b2c7d9; color: #111827; font-family: Arial, "Malgun Gothic", sans-serif; }}
.phone {{ width: min(100%, 520px); min-height: 100vh; margin: 0 auto; background: #b2c7d9; }}
.topbar {{ position: sticky; top: 0; z-index: 20; margin: 8px 8px 0; padding: 15px 16px 12px; background: #23395d; border: 1px solid rgba(255,255,255,.18); border-radius: 12px; box-shadow: 0 1px 5px rgba(17,24,39,.16); backdrop-filter: blur(8px); }}
.topbar-title-row {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
.topbar h1 {{ margin: 0; color: #ffffff; font-size: 19px; line-height: 1.25; font-weight: 900; letter-spacing: -.35px; text-shadow: none; }}
.header-toggle {{ flex: 0 0 auto; min-width: 54px; height: 24px; padding: 0 7px; border: 1px solid rgba(17,24,39,.14); border-radius: 7px; background: #fee500; color: #111827; font-size: 9px; font-weight: 800; letter-spacing: -.2px; cursor: pointer; box-shadow: none; }}
.header-toggle:hover {{ background: #f5d900; }}
.header-toggle:active {{ transform: translateY(1px); }}
.topbar.collapsed .header-toggle {{ background: #fee500; color: #111827; border-color: rgba(17,24,39,.14); box-shadow: none; }}
.header-controls {{ overflow: hidden; max-height: 210px; opacity: 1; transition: max-height .2s ease, opacity .15s ease, margin .2s ease; }}
.topbar.collapsed {{ padding-bottom: 9px; background: #23395d; }}
.topbar.collapsed .header-controls {{ max-height: 0; opacity: 0; margin: 0; pointer-events: none; }}
.updated {{ margin-top: 5px; color: rgba(255,255,255,.72); font-size: 10px; font-weight: 600; }}
.tabs {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 7px; margin-top: 11px; }}
.date-picker-row {{ display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 7px; margin-top: 7px; }}
.language-order-row {{ display: grid; grid-template-columns: auto minmax(0,1fr); align-items: center; gap: 7px; margin-top: 7px; }}
.language-order-label {{ color: rgba(255,255,255,.78); font-size: 10px; font-weight: 700; }}
.language-order-select {{ width: 100%; height: 32px; padding: 0 9px; border: 1px solid rgba(17,24,39,.13); border-radius: 8px; background: rgba(255,255,255,.94); color: #344054; font-size: 11px; font-weight: 700; }}

.date-input {{ width: 100%; height: 34px; padding: 0 9px; border: 1px solid rgba(17,24,39,.13); border-radius: 8px; background: rgba(255,255,255,.9); color: #344054; font-size: 11px; }}
.date-button {{ height: 34px; padding: 0 12px; border: 0; border-radius: 8px; background: #344054; color: white; font-size: 11px; font-weight: 800; cursor: pointer; }}
.search-wrap {{ position: relative; margin-top: 6px; }}
.search-input {{ width: 100%; height: 32px; padding: 0 32px 0 10px; border: 1px solid rgba(17,24,39,.13); border-radius: 8px; background: rgba(255,255,255,.9); font-size: 11px; }}
.search-clear {{ position: absolute; right: 5px; top: 4px; width: 24px; height: 24px; border: 0; background: transparent; color: #667085; cursor: pointer; }}
.favorites-panel {{ margin-bottom: 8px; padding: 8px; background: rgba(255,255,255,.82); border-radius: 9px; }}
.favorites-panel[hidden] {{ display: none; }}
.favorites-title {{ margin-bottom: 6px; font-size: 11px; font-weight: 800; }}
.favorites-list {{ display: grid; gap: 5px; }}
.favorite-item {{ display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 6px; padding: 7px 8px; border: 1px solid #f2c94c; border-radius: 7px; background: #fff9dc; cursor: pointer; }}
.favorite-publisher {{ color: #667085; font-size: 8px; }}
.favorite-headline {{ margin-top: 2px; font-size: 10px; font-weight: 700; line-height: 1.35; }}
.favorite-remove {{ border: 0; background: transparent; color: #d49a00; font-size: 16px; cursor: pointer; }}
.no-results {{ display: none; padding: 16px 12px; background: rgba(255,255,255,.82); border-radius: 9px; text-align: center; color: #667085; font-size: 11px; }}
.tab-button {{ padding: 9px 5px; border: 0; border-radius: 8px; color: #344054; background: rgba(255,255,255,.62); font: inherit; font-size: 13px; font-weight: 800; cursor: pointer; }}
.tab-button.active {{ color: #111827; background: #fee500; box-shadow: 0 1px 3px rgba(17,24,39,.18); }}
main {{ padding: 12px 12px 34px; }}
.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; }}
.period-card {{ display: flex; flex-direction: column; gap: 3px; margin-bottom: 10px; padding: 10px 12px; color: #344054; background: rgba(255,255,255,.68); border-radius: 9px; font-size: 11px; }}
.period-card strong {{ color: #111827; font-size: 14px; }}
.partial-note {{ margin-bottom: 10px; padding: 9px 11px; color: #475467; background: #fff7cc; border-radius: 8px; font-size: 10px; line-height: 1.45; }}
.language-section {{ margin-bottom: 30px; }}
.news-group {{ margin-bottom: 20px; }}
.group-title {{ display: inline-flex; align-items: center; gap: 6px; width: fit-content; max-width: 100%; margin: 0 0 8px 0; padding: 8px 11px; background: #fee500; border-radius: 4px 11px 11px 11px; font-size: 14px; font-weight: 800; text-align: left; box-shadow: 0 1px 2px rgba(17,24,39,.12); }}
.group-square {{ width: 9px; height: 9px; background: #111; border-radius: 1px; }}
.group-count {{ align-self: flex-end; margin-bottom: 1px; color: #5f5200; font-size: 9px; line-height: 1; white-space: nowrap; }}
.article-stack {{ display: grid; gap: 7px; }}
.preview-card {{ position: relative; display: grid; grid-template-columns: 26px minmax(0,1fr) 82px; height: 118px; min-height: 118px; overflow: hidden; color: inherit; background: white; border: 1px solid rgba(17,24,39,.08); border-radius: 10px; text-decoration: none; box-shadow: 0 1px 3px rgba(17,24,39,.15); transition: opacity .15s ease, background .15s ease; }}
.preview-card.read {{ background: #eef1f4; opacity: .72; }}
.preview-card.important {{ border: 2px solid #f2c94c; background: #fffdf3; opacity: 1; }}
.article-number {{ display: flex; align-items: flex-start; justify-content: center; padding-top: 12px; color: #344054; font-size: 12px; font-weight: 800; }}
.preview-copy {{ display: flex; flex-direction: column; min-width: 0; padding: 10px 9px 8px 0; }}
.publisher {{ overflow: hidden; color: #667085; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }}
.headline {{ display: -webkit-box; overflow: hidden; margin-top: 5px; overflow: hidden; color: #101828; font-size: 13px; font-weight: 700; line-height: 1.38; -webkit-line-clamp: 3; -webkit-box-orient: vertical; }}
.status-line {{ margin-top: auto; margin-top: auto; padding-top: 5px; font-size: 10px; }}
.translate-button {{ float: right; height: 20px; margin-top: -3px; padding: 0 7px; border: 1px solid rgba(35,57,93,.18); border-radius: 6px; background: #eef3f8; color: #23395d; font-size: 8px; font-weight: 800; cursor: pointer; }}
.translate-button:hover {{ background: #dfe9f2; }}

.unread-label {{ color: #17639f; font-weight: 700; }}
.read-label {{ display: none; color: #667085; font-weight: 700; }}
.important-label {{ display: none; color: #b77900; font-weight: 800; }}
.preview-card.read .unread-label {{ display: none; }}
.preview-card.read .read-label {{ display: inline; }}
.preview-card.important .unread-label, .preview-card.important .read-label {{ display: none; }}
.preview-card.important .important-label {{ display: inline; }}
.card-side {{ position: relative; align-self: stretch; width: 82px; height: 118px; min-height: 118px; overflow: hidden; background: linear-gradient(135deg,#173b67,#0b213d); }}
.important-button {{ position: absolute; z-index: 3; top: calc(43% - 8px); left: 50%; transform: translate(-50%,-50%); width: 30px; height: 30px; padding: 0; border: 0; border-radius: 50%; color: white; background: rgba(17,24,39,.62); font-size: 18px; line-height: 30px; text-align: center; cursor: pointer; box-shadow: 0 1px 4px rgba(0,0,0,.28); }}
.preview-card.important .important-button {{ color: #111; background: #fee500; }}
.preview-image {{ width: 82px; height: 118px; min-height: 118px; background: linear-gradient(135deg,#173b67,#0b213d); }}
.preview-image img {{ display: block; width: 100%; height: 118px; min-height: 118px; object-fit: cover; }}
.new-badge {{ display: inline-block; margin-right: 4px; padding: 1px 4px; border-radius: 4px; color: white; background: #e5484d; font-size: 8px; font-weight: 900; }}
.no-image {{ display: flex; align-items: center; justify-content: center; width: 100%; height: 118px; min-height: 118px; padding: 24px 4px 0; box-sizing: border-box; color: white; background: linear-gradient(135deg,#173b67,#0b213d); font-size: 9px; font-weight: 800; line-height: 1.25; text-align: center; }}
.empty {{ padding: 22px 15px; background: white; border-radius: 10px; text-align: center; color: #667085; }}
footer {{ padding: 0 12px 28px; color: #475467; font-size: 10px; text-align: center; }}
@media (max-width: 380px) {{ .preview-card {{ grid-template-columns: 24px minmax(0,1fr) 72px; }} .card-side, .preview-image {{ width: 72px; }} .headline {{ font-size: 12px; }} }}
</style>
</head>
<body>
<div class="phone">
  <header class="topbar" id="topbar">
    <div class="topbar-title-row">
      <h1>원자력 주요기사</h1>
      <button id="header-toggle" class="header-toggle" type="button" aria-expanded="true">접기 ▲</button>
    </div>
    <div class="header-controls" id="header-controls">
      <div class="updated">최종 업데이트: {generated_at:%Y. %-m. %-d. %H:%M} (KST)</div>
      <div class="search-wrap"><input id="article-search" class="search-input" type="search" placeholder="기사·언론사·기업·프로젝트·국가 검색"><button id="search-clear" class="search-clear" type="button">×</button></div>
      <div class="tabs">{buttons}</div>
      <div class="language-order-row">
        <span class="language-order-label">기사 순서</span>
        <select id="language-order" class="language-order-select" aria-label="기사 언어 우선순위">
          <option value="ko-en">KOR → ENG</option>
          <option value="en-ko">ENG → KOR</option>
        </select>
      </div>
      <div class="date-picker-row"><input id="archive-date" class="date-input" type="date"><button id="archive-open" class="date-button" type="button">날짜 보기</button></div>
    </div>
  </header>
  <main><section id="favorites-panel" class="favorites-panel" hidden><div class="favorites-title">★ 중요 기사 <span id="favorite-count"></span></div><div id="favorites-list" class="favorites-list"></div></section><div id="no-results" class="no-results">검색 결과가 없습니다.</div>{panels_html}</main>
  <footer>기사 카드를 누르면 원문으로 이동하며, 확인한 기사는 회색으로 표시됩니다.</footer>
</div>
<script>
const readKey = "nuclearDailyBriefReadArticles";
const importantKey = "nuclearDailyBriefImportantArticles";
const readArticles = new Set(JSON.parse(localStorage.getItem(readKey) || "[]"));
const importantArticles = new Set(JSON.parse(localStorage.getItem(importantKey) || "[]"));
const headerStateKey = "nuclearDailyBriefHeaderCollapsed";
const topbar = document.getElementById("topbar");
const headerToggle = document.getElementById("header-toggle");
function setHeaderCollapsed(collapsed){{
  topbar.classList.toggle("collapsed", collapsed);
  headerToggle.textContent = collapsed ? "펼치기 ▼" : "접기 ▲";
  headerToggle.setAttribute("aria-expanded", String(!collapsed));
  localStorage.setItem(headerStateKey, collapsed ? "1" : "0");
}}
setHeaderCollapsed(localStorage.getItem(headerStateKey) === "1");
headerToggle.addEventListener("click", () => setHeaderCollapsed(!topbar.classList.contains("collapsed")));
const languageOrderKey = "nuclearDailyBriefLanguageOrder";
const languageOrderSelect = document.getElementById("language-order");

function reorderLanguageArticles(order){{
  const languageRank = order === "en-ko"
    ? {{ en: 0, ko: 1 }}
    : {{ ko: 0, en: 1 }};

  document.querySelectorAll(".news-group").forEach(group => {{
    const stack = group.querySelector(".article-stack");
    if(!stack) return;

    const cards = [...stack.querySelectorAll(".preview-card")];
    cards.sort((a, b) => {{
      const rankA = languageRank[a.dataset.language] ?? 9;
      const rankB = languageRank[b.dataset.language] ?? 9;
      return rankA - rankB;
    }});

    cards.forEach((card, index) => {{
      stack.appendChild(card);
      const number = card.querySelector(".article-number");
      if(number) number.textContent = String(index + 1);
    }});
  }});

  localStorage.setItem(languageOrderKey, order);
}}

const savedLanguageOrder = localStorage.getItem(languageOrderKey) || "ko-en";
languageOrderSelect.value = savedLanguageOrder;
reorderLanguageArticles(savedLanguageOrder);
languageOrderSelect.addEventListener("change", () => {{
  reorderLanguageArticles(languageOrderSelect.value);
  renderFavorites();
}});
function saveState(){{ localStorage.setItem(readKey, JSON.stringify([...readArticles])); localStorage.setItem(importantKey, JSON.stringify([...importantArticles])); }}
function applyState(card){{
  const u=card.dataset.url;
  const isRead=readArticles.has(u);
  card.classList.toggle("read", isRead);
  card.classList.toggle("important", importantArticles.has(u));
  if(isRead){{
    card.querySelectorAll(".new-badge").forEach(badge=>badge.remove());
  }}
}}
function openArticle(card){{
  const u=card.dataset.url;
  readArticles.add(u);
  saveState();
  document.querySelectorAll(`.preview-card[data-url="${{CSS.escape(u)}}"]`).forEach(applyState);
  window.open(u,"_blank","noopener");
}}
document.querySelectorAll(".preview-card").forEach(card=>{{
  applyState(card);
  card.addEventListener("click",e=>{{ if(!e.target.closest(".important-button, .translate-button")) openArticle(card); }});
  card.addEventListener("keydown",e=>{{ if(e.key==="Enter"||e.key===" "){{ e.preventDefault(); openArticle(card); }}}});
  card.querySelector(".important-button").addEventListener("click",e=>{{
    e.stopPropagation(); const u=card.dataset.url; importantArticles.has(u)?importantArticles.delete(u):importantArticles.add(u); saveState(); document.querySelectorAll(`.preview-card[data-url="${{CSS.escape(u)}}"]`).forEach(applyState); renderFavorites();
  }});

  const translateButton = card.querySelector(".translate-button");
  if(translateButton){{
    translateButton.addEventListener("click", e => {{
      e.stopPropagation();
      const translatedUrl =
        "https://translate.google.com/translate?sl=en&tl=ko&u=" +
        encodeURIComponent(card.dataset.url);
      window.open(translatedUrl, "_blank", "noopener");
    }});
  }}
}});
function activePanel(){{ return document.querySelector(".tab-panel.active"); }}
function renderFavorites(){{
  const panel=activePanel(), box=document.getElementById("favorites-panel"), list=document.getElementById("favorites-list"), count=document.getElementById("favorite-count"); list.innerHTML="";
  if(!panel){{box.hidden=true;return;}}
  const cards=[...panel.querySelectorAll(".preview-card")].filter(c=>importantArticles.has(c.dataset.url));
  cards.forEach(card=>{{ const item=document.createElement("div"); item.className="favorite-item"; item.innerHTML=`<div><div class="favorite-publisher">${{card.dataset.publisher}}</div><div class="favorite-headline">${{card.dataset.title}}</div></div><button class="favorite-remove" type="button">★</button>`; item.addEventListener("click",e=>{{if(!e.target.closest(".favorite-remove"))openArticle(card)}}); item.querySelector(".favorite-remove").addEventListener("click",e=>{{e.stopPropagation();importantArticles.delete(card.dataset.url);saveState();document.querySelectorAll(`.preview-card[data-url="${{CSS.escape(card.dataset.url)}}"]`).forEach(applyState);renderFavorites();}}); list.appendChild(item); }});
  count.textContent=`${{cards.length}}건`; box.hidden=cards.length===0;
}}
function filterArticles(){{ const q=document.getElementById("article-search").value.trim().toLowerCase(), panel=activePanel(); if(!panel)return; let total=0; panel.querySelectorAll(".news-group").forEach(group=>{{let n=0;group.querySelectorAll(".preview-card").forEach(card=>{{const show=!q||card.dataset.search.includes(q);card.style.display=show?"":"none";if(show){{n++;total++;}}}});group.style.display=n?"":"none";}});document.getElementById("no-results").style.display=q&&total===0?"block":"none";}}
function activatePanel(panel, button=null){{
  document.querySelectorAll(".tab-button").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(x=>x.classList.remove("active"));
  if(button)button.classList.add("active");
  panel.classList.add("active");
  filterArticles();
  renderFavorites();
}}
document.querySelectorAll(".tab-button").forEach(button=>button.addEventListener("click",()=>{{
  const panel=document.getElementById(`tab-${{button.dataset.tab}}`);
  if(panel)activatePanel(panel,button);
}}));
const archiveDates=[...document.querySelectorAll(".archive-panel")].map(x=>x.dataset.archiveDate).sort();
const archiveInput=document.getElementById("archive-date");
if(archiveDates.length){{
  archiveInput.min=archiveDates[0];
  archiveInput.max=archiveDates[archiveDates.length-1];
  archiveInput.value=archiveDates[archiveDates.length-1];
}}
document.getElementById("archive-open").addEventListener("click",()=>{{
  const value=archiveInput.value;
  if(!value)return;
  const panel=document.getElementById(`archive-${{value}}`);
  if(!panel){{
    alert("선택한 날짜의 기사가 아직 저장되지 않았습니다. GitHub Actions가 실행될 때마다 과거 날짜를 순차적으로 채웁니다.");
    return;
  }}
  activatePanel(panel);
  window.scrollTo({{top:0,behavior:"smooth"}});
}});
archiveInput.addEventListener("change",()=>document.getElementById("archive-open").click());
document.getElementById("article-search").addEventListener("input",filterArticles);
document.getElementById("search-clear").addEventListener("click",()=>{{const input=document.getElementById("article-search");input.value="";input.focus();filterArticles();}});
filterArticles();renderFavorites();
</script>
</body>
</html>
'''


def main() -> int:
    now = datetime.now(KST)
    periods = brief_periods(now)
    previous_urls = load_previous_urls()
    articles_by_period = {
        label: collect(start, end)
        for label, (start, end) in periods.items()
    }
    current_urls = {article.link for items in articles_by_period.values() for article in items}
    new_urls = current_urls - previous_urls if previous_urls else set()
    archive = update_archive(load_archive(), periods, articles_by_period, now)
    archive = backfill_missing_archive_dates(archive, now)
    save_archive(archive)
    OUTPUT.write_text(
        build_html(periods, articles_by_period, now, new_urls, archive),
        encoding="utf-8",
    )
    save_current_urls(current_urls, now)
    total = sum(len(items) for items in articles_by_period.values())
    print(f"Generated {OUTPUT}: {total} news articles across 3 periods; {len(archive)} archive dates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
