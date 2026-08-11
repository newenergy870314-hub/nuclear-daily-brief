# VERIFIED FINAL BUILD 2026-08-10
# Includes Hyundai volleyball exclusion (배구/여자배구/김연경), same-event dedup,
# article preview fallback, thumbnail caching/centering, newspaper-style UI,
# and removes the old periodic-update notice from the UI.
# Final media/dedup build v7 - 2026-08-10
from __future__ import annotations

import html
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse, parse_qsl, urlunparse, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import feedparser
from dateutil import parser as date_parser

KST = ZoneInfo("Asia/Seoul")
OUTPUT = Path("index.html")
STATE_FILE = Path("article_state.json")
ARCHIVE_FILE = Path("news_archive.json")
ARCHIVE_DAYS = 90
BACKFILL_DATES_PER_RUN = 1
SKIP_BACKFILL = os.getenv("SKIP_BACKFILL", "0") == "1"
# 검토용 원본 수집 모드
# 기사량을 먼저 확인하기 위해 개수 제한/중복 제거를 적용하지 않습니다.
RAW_REVIEW_MODE = True
MAX_PER_GROUP_PER_LANGUAGE = None

# 언론사 직접 RSS는 여러 매체를 병렬로 조회합니다.
# 개별 피드가 응답하지 않아도 전체 작업이 오래 멈추지 않도록 timeout을 둡니다.
DIRECT_RSS_WORKERS = 10
DIRECT_RSS_TIMEOUT_SECONDS = 10

# 기사 카드에 대표 이미지와 본문 미리보기를 보완하기 위한 원문 메타데이터 조회 설정
# 이미 RSS에 값이 있는 경우에는 추가 조회하지 않으며, 필요한 기사만 병렬 조회합니다.
ARTICLE_META_WORKERS = 12
ARTICLE_META_TIMEOUT_SECONDS = 4
ARTICLE_META_MAX_BYTES = 800_000

# 대표 이미지를 직접 저장해 외부 이미지 차단(hotlink) 문제를 줄입니다.
THUMBNAIL_DIR = Path("assets/thumbnails")
THUMBNAIL_KEEP_DAYS = 7
THUMBNAIL_DOWNLOAD_WORKERS = 10
THUMBNAIL_TIMEOUT_SECONDS = 5
THUMBNAIL_MAX_BYTES = 5_000_000

ALWAYS_SHOW_GROUPS = {
    "현대건설",
    "타 건설사",
    "한국수력원자력",
    "한국전력",
    "원전 관계부처",
    "원전 대미투자",
    "원자력",
    "SMR",
    "Nuclear Power·Nuclear Energy",
    "Holtec",
    "TerraPower",
    "Westinghouse",
    "Fermi America",
}

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
    ("타 건설사", [
        # 국내 주요 건설사 동향을 하나의 항목으로 통합
        '"삼성물산 건설부문"', '"Samsung C&T" construction',
        '"대우건설"', '"Daewoo E&C"',
        '"DL이앤씨"', '"DL E&C"',
        '"GS건설"', '"GS E&C"',
        '"SK에코플랜트"', '"SK ecoplant"',
        '"포스코이앤씨"', '"POSCO E&C"',
        '"롯데건설"', '"Lotte E&C"',
        '"현대엔지니어링"', '"Hyundai Engineering"',
        '"HDC현대산업개발"', '"HDC Hyundai Development"',
        '"한화 건설부문"', '"Hanwha construction"',
        '"두산에너빌리티" 건설', '"Doosan Enerbility" construction',
    ]),
    ("한국수력원자력", [
        "한수원 원전", "한국수력원자력", "KHNP nuclear",
        "KHNP reactor", "KHNP nuclear project",
        "한수원 인사", "한국수력원자력 인사",
        "한수원 임명", "한수원 취임", "한수원 승진",
        "한수원 보직", "한수원 인사발령",
        "KHNP appointment", "KHNP personnel", "KHNP executive",
    ]),
    ("한국전력", [
        "한전 원전", "한국전력 원자력", "KEPCO nuclear",
        "KEPCO reactor", "KEPCO nuclear project",
        "한전 인사", "한국전력 인사",
        "한전 임명", "한전 취임", "한전 승진",
        "한전 보직", "한전 인사발령",
        "KEPCO appointment", "KEPCO personnel", "KEPCO executive",
    ]),
    ("원전 관계부처", [
        # 산업통상부·기후에너지환경부·과학기술정보통신부의
        # 장관·차관급 이상 인사 및 정책 관련 기사
        '"산업통상부" 장관',
        '"산업통상부" 차관',
        '"산업통상부" 통상교섭본부장',
        '"산업통상자원부" 장관',
        '"산업통상자원부" 통상교섭본부장',
        '"산업통상자원부" 차관',
        '"기후에너지환경부" 장관',
        '"기후에너지환경부" 차관',
        '"기후부" 장관',
        '"기후부" 차관',
        '"과학기술정보통신부" 장관',
        '"과학기술정보통신부" 차관',
        '"과기정통부" 장관',
        '"과기정통부" 차관',
        '"과기부" 장관',
        '"과기부" 차관',
        '"Ministry of Trade, Industry and Energy" minister',
        '"Ministry of Climate, Energy and Environment" minister',
        '"Ministry of Science and ICT" minister',
        '"Ministry of Science and ICT" vice minister',
        '"산업통상부" 인사',
        '"산업통상자원부" 인사',
        '"기후에너지환경부" 인사',
        '"과학기술정보통신부" 인사',
        '"과기정통부" 인사',
        '"산업통상부" 임명',
        '"기후에너지환경부" 임명',
        '"과학기술정보통신부" 임명',
        '"Ministry of Trade, Industry and Energy" appointment',
        '"Ministry of Climate, Energy and Environment" appointment',
        '"Ministry of Science and ICT" appointment',
        '"김정관" 장관',
        '"문신학" 차관',
        '"양기욱" 산업자원안보실장',
        '"여한구" 통상교섭본부장',
        '"강감찬" 무역투자실장',
        '"김창희" 원전전략기획관',
        '"김정관"',
        '"문신학"',
        '"양기욱"',
        '"여한구"',
        '"강감찬"',
        '"김창희"',
    ]),
    ("원전 대미투자", [
        '"대미투자" 원전',
        '"대미 투자" 원전',
        '"미국 투자" 원전',
        '"대미투자" 원자력',
        '"대미 투자" 원자력',
        '"미국 투자" 원자력',
        '"대미투자펀드" 원전',
        '"대미 투자 펀드" 원전',
        '"한미 투자" 원전',
        '"한미 정상회담" 원전 투자',
        '"미국 원전" 투자',
        '"미국 원자력" 투자',
        '"U.S. investment" nuclear',
        '"US investment" nuclear',
        '"Korea investment" U.S. nuclear',
        '"Korea-US investment" nuclear',
        '"nuclear investment fund" Korea U.S.',
    ]),
    ("원자력", [
        "원전", "원자력", "원자력발전", "원자력발전소",
        "대형원전", "신규 원전", "원전 건설", "원전 프로젝트", "원전 수출",
        "신한울 원전", "신한울원전",
        "신한울 1호기", "신한울 2호기",
        "신한울 3호기", "신한울 4호기",
        "신한울 1·2호기", "신한울 3·4호기",
        "Shin Hanul nuclear", "Shin Hanul NPP",
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
        "Holtec nuclear", '"Holtec International"', "홀텍",
        "SMR-300", "Palisades nuclear", '"Oyster Creek" SMR',
    ]),
    ("TerraPower", [
        "TerraPower", "테라파워", "Natrium reactor", "Natrium nuclear",
        "Kemmerer nuclear", "TerraPower nuclear project",
    ]),
    ("Westinghouse", [
        "Westinghouse nuclear", '"Westinghouse Electric Company"',
        "AP1000", "AP300", "AP1000 construction",
    ]),
    ("Fermi America", [
        '"Fermi America"',
        "페르미 아메리카", "페르미아메리카", "페르미",
        "퍼미 아메리카", "퍼미아메리카", "퍼미",
        '"Project Matador"', "프로젝트 마타도르",
        "HyperGrid nuclear", "하이퍼그리드",
        '"Fermi America" AP1000', "Amarillo nuclear", "애머릴로 원전",
        '"Carson County" nuclear', "카슨 카운티 원전",
    ]),
]

GROUP_TAB_LABELS = {
    "현대건설": "현대건설",
    "타 건설사": "타건설사",
    "한국수력원자력": "한수원",
    "한국전력": "한전",
    "원전 관계부처": "관계부처",
    "원전 대미투자": "대미투자",
    "원자력": "원자력",
    "SMR": "SMR",
    "Nuclear Power·Nuclear Energy": "Nuclear",
    "Holtec": "Holtec",
    "TerraPower": "Terra",
    "Westinghouse": "WEC",
    "Fermi America": "Fermi America",
}


# 기사 수집원: Google News RSS를 사용하지 않고 언론사 자체 RSS/공식 뉴스 페이지에서 직접 수집합니다.
# RSS가 있는 언론사는 자체 RSS를 우선 사용하여 제목·원문 URL·description·대표이미지를 최대한 원형 그대로 확보합니다.
DIRECT_RSS_FEEDS = [
    # ─────────────────────────────────────────────
    # 국내 통신·종합·경제·산업 매체의 공식 RSS
    # ─────────────────────────────────────────────
    ("뉴시스", "https://www.newsis.com/RSS/sokbo.xml"),
    ("뉴시스", "https://www.newsis.com/RSS/politics.xml"),
    ("뉴시스", "https://www.newsis.com/RSS/economy.xml"),
    ("뉴시스", "https://www.newsis.com/RSS/industry.xml"),
    ("뉴시스", "https://www.newsis.com/RSS/international.xml"),

    ("전자신문", "https://rss.etnews.com/Section901.xml"),
    ("전자신문", "https://rss.etnews.com/Section902.xml"),
    ("전자신문", "https://rss.etnews.com/02.xml"),
    ("전자신문", "https://rss.etnews.com/06065.xml"),
    ("전자신문", "https://rss.etnews.com/22210.xml"),

    # 공식 RSS가 확인되는 주요 경제·방송 매체
    ("매일경제", "https://www.mk.co.kr/rss/40300001/"),
    ("매일경제", "https://www.mk.co.kr/rss/30100041/"),
    ("매일경제", "https://www.mk.co.kr/rss/50100032/"),
    ("매일경제", "https://www.mk.co.kr/rss/30300018/"),
    ("한국경제", "https://www.hankyung.com/feed/all-news"),
    ("한국경제", "https://www.hankyung.com/feed/economy"),
    ("한국경제", "https://www.hankyung.com/feed/it"),
    ("한국경제", "https://www.hankyung.com/feed/international"),
    ("MBN", "https://www.mbn.co.kr/rss/"),
    ("MBN", "https://www.mbn.co.kr/rss/economy/"),
    ("MBN", "https://www.mbn.co.kr/rss/politics/"),

    # 국내 원전·전력·에너지 전문매체
    ("전기신문", "https://www.electimes.com/rss/allArticle.xml"),
    ("에너지신문", "https://www.energy-news.co.kr/rss/allArticle.xml"),
    ("에너지타임즈", "https://www.energytimes.kr/rss/allArticle.xml"),
    ("전력경제신문", "https://www.epetimes.com/rss/allArticle.xml"),

    # 해외 원자력 전문매체
    ("World Nuclear News", "https://www.world-nuclear-news.org/?rss=feed"),
]

# RSS가 없거나 RSS만으로는 누락 가능성이 있는 매체는 공식 뉴스 페이지를 직접 훑습니다.
# 각 페이지에서 제목이 원전/에너지/현대건설/한전/한수원/관계부처 등 키워드에 걸리는 기사만
# 상세 원문까지 들어가므로 일반 기사 전체를 다운로드하지 않습니다.
DIRECT_NEWS_PAGES = [
    ("MTN 머니투데이방송", "https://news.mtn.co.kr/", "ko"),
    ("뉴스필드", "https://www.newsfield.net/", "ko"),
    # ─────────────────────────────────────────────
    # 국내 통신·종합 일간지
    # ─────────────────────────────────────────────
    ("연합뉴스", "https://www.yna.co.kr/industry/all", "ko"),
    ("연합뉴스", "https://www.yna.co.kr/economy/all", "ko"),
    ("연합뉴스", "https://www.yna.co.kr/politics/all", "ko"),
    ("뉴스1", "https://www.news1.kr/", "ko"),
    ("조선일보", "https://www.chosun.com/", "ko"),
    ("중앙일보", "https://www.joongang.co.kr/", "ko"),
    ("동아일보", "https://www.donga.com/", "ko"),
    ("한겨레", "https://www.hani.co.kr/", "ko"),
    ("경향신문", "https://www.khan.co.kr/", "ko"),
    ("한국일보", "https://www.hankookilbo.com/", "ko"),
    ("국민일보", "https://www.kmib.co.kr/", "ko"),
    ("서울신문", "https://www.seoul.co.kr/", "ko"),
    ("세계일보", "https://www.segye.com/", "ko"),
    ("문화일보", "https://www.munhwa.com/", "ko"),

    # ─────────────────────────────────────────────
    # 국내 경제·산업·비즈니스 매체
    # ─────────────────────────────────────────────
    ("서울경제", "https://www.sedaily.com/", "ko"),
    ("머니투데이", "https://www.mt.co.kr/", "ko"),
    ("이데일리", "https://www.edaily.co.kr/", "ko"),
    ("아시아경제", "https://www.asiae.co.kr/", "ko"),
    ("헤럴드경제", "https://biz.heraldcorp.com/", "ko"),
    ("파이낸셜뉴스", "https://www.fnnews.com/", "ko"),
    ("아주경제", "https://www.ajunews.com/", "ko"),
    ("조선비즈", "https://biz.chosun.com/", "ko"),
    ("비즈워치", "https://news.bizwatch.co.kr/", "ko"),
    ("매일경제", "https://www.mk.co.kr/", "ko"),
    ("한국경제", "https://www.hankyung.com/", "ko"),

    # ─────────────────────────────────────────────
    # 국내 방송·보도 채널
    # ─────────────────────────────────────────────
    ("KBS", "https://news.kbs.co.kr/", "ko"),
    ("MBC", "https://imnews.imbc.com/", "ko"),
    ("SBS", "https://news.sbs.co.kr/", "ko"),
    ("YTN", "https://www.ytn.co.kr/", "ko"),
    ("JTBC", "https://news.jtbc.co.kr/", "ko"),
    ("MBN", "https://www.mbn.co.kr/news/", "ko"),
    ("TV조선", "https://news.tvchosun.com/", "ko"),
    ("채널A", "https://www.ichannela.com/news/main/news_main.do", "ko"),

    # ─────────────────────────────────────────────
    # 국내 IT·과학·산업 전문매체
    # ─────────────────────────────────────────────
    ("전자신문", "https://www.etnews.com/", "ko"),
    ("디지털타임스", "https://www.dt.co.kr/", "ko"),
    ("디지털데일리", "https://www.ddaily.co.kr/", "ko"),
    ("ZDNet Korea", "https://zdnet.co.kr/", "ko"),
    ("블로터", "https://www.bloter.net/", "ko"),
    ("산업일보", "https://kidd.co.kr/", "ko"),

    # ─────────────────────────────────────────────
    # 국내 원전·전력·에너지 전문매체
    # ─────────────────────────────────────────────
    ("전기신문", "https://www.electimes.com/", "ko"),
    ("에너지신문", "https://www.energy-news.co.kr/", "ko"),
    ("에너지타임즈", "https://www.energytimes.kr/", "ko"),
    ("전력경제신문", "https://www.epetimes.com/", "ko"),
    ("투데이에너지", "https://www.todayenergy.kr/", "ko"),
    ("에너지경제신문", "https://www.ekn.kr/", "ko"),
    ("이투뉴스", "https://www.e2news.com/", "ko"),

    # ─────────────────────────────────────────────
    # 해외 글로벌 통신·경제·종합 언론
    # ─────────────────────────────────────────────
    ("Reuters", "https://www.reuters.com/business/energy/", "en"),
    ("Reuters", "https://www.reuters.com/world/", "en"),
    ("Associated Press", "https://apnews.com/hub/business", "en"),
    ("BBC", "https://www.bbc.com/news/business", "en"),
    ("Financial Times", "https://www.ft.com/energy", "en"),
    ("Bloomberg", "https://www.bloomberg.com/energy", "en"),
    ("The Wall Street Journal", "https://www.wsj.com/business/energy-oil", "en"),
    ("CNBC", "https://www.cnbc.com/energy/", "en"),
    ("CNN", "https://edition.cnn.com/business", "en"),
    ("The Guardian", "https://www.theguardian.com/environment/energy", "en"),
    ("POLITICO", "https://www.politico.com/energy-and-environment", "en"),
    ("EURACTIV", "https://www.euractiv.com/sections/energy-environment/", "en"),

    # ─────────────────────────────────────────────
    # 해외 원자력·전력·에너지 전문매체
    # ─────────────────────────────────────────────
    ("World Nuclear News", "https://www.world-nuclear-news.org/", "en"),
    ("Nuclear Engineering International", "https://www.neimagazine.com/news/", "en"),
    ("NucNet", "https://www.nucnet.org/search", "en"),
    ("POWER Magazine", "https://www.powermag.com/", "en"),
    ("Power Engineering", "https://www.power-eng.com/", "en"),
    ("Utility Dive", "https://www.utilitydive.com/", "en"),
    ("Energy Monitor", "https://www.energymonitor.ai/", "en"),
    ("S&P Global Commodity Insights", "https://www.spglobal.com/commodity-insights/en/news-research/latest-news", "en"),
    ("Argus Media", "https://www.argusmedia.com/en/news-and-insights", "en"),

    # ─────────────────────────────────────────────
    # 사용자 요청 추가 매체
    # ─────────────────────────────────────────────
    ("이투데이", "https://www.etoday.co.kr/", "ko"),
    ("뉴스토마토", "https://www.newstomato.com/", "ko"),
    ("데일리안", "https://www.dailian.co.kr/", "ko"),
    ("뉴스웨이", "https://www.newsway.co.kr/", "ko"),
    ("비즈니스포스트", "https://www.businesspost.co.kr/", "ko"),
    ("시사저널", "https://www.sisajournal.com/", "ko"),
    ("시사오늘", "https://www.sisaon.co.kr/", "ko"),
    ("미디어오늘", "https://www.mediatoday.co.kr/", "ko"),
    ("노컷뉴스", "https://www.nocutnews.co.kr/", "ko"),
    ("더구루", "https://www.theguru.co.kr/", "ko"),
    ("딜사이트", "https://dealsite.co.kr/", "ko"),
    ("더벨", "https://www.thebell.co.kr/", "ko"),
    ("한국원자력신문", "http://www.knpnews.com/", "ko"),
    ("원자력신문", "https://www.atomicenergy.co.kr/", "ko"),
    ("인더스트리뉴스", "https://www.industrynews.co.kr/", "ko"),
    ("헬로티", "https://www.hellot.net/", "ko"),
    ("대한경제", "https://www.daehannews.kr/", "ko"),
    ("건설경제", "https://www.cnews.co.kr/", "ko"),
    ("건설타임즈", "https://www.constimes.co.kr/", "ko"),
    ("오피니언뉴스", "https://www.opinionnews.co.kr/", "ko"),
    ("녹색경제신문", "https://www.greened.kr/", "ko"),
    ("ESG경제", "https://www.esgeconomy.com/", "ko"),
    ("뉴스펭귄", "https://www.newspenguin.com/", "ko"),
    ("한국경제TV", "https://www.wowtv.co.kr/", "ko"),
    ("서울경제TV", "https://www.sentv.co.kr/", "ko"),
    ("포브스코리아", "https://www.forbes.com/sites/forbeskorea/", "ko"),
    ("한경ESG", "https://www.hankyung.com/esg", "ko"),
    ("인베스트조선", "https://www.investchosun.com/", "ko"),
    ("머니S", "https://www.moneys.co.kr/", "ko"),
    ("KBS 뉴스", "https://news.kbs.co.kr/", "ko"),
    ("MBC 뉴스", "https://imnews.imbc.com/", "ko"),
    ("SBS 뉴스", "https://news.sbs.co.kr/", "ko"),
    ("연합뉴스TV", "https://www.yonhapnewstv.co.kr/", "ko"),
    ("BBC 코리아", "https://www.bbc.com/korean/", "ko"),
    ("부산일보", "https://www.busan.com/", "ko"),
    ("국제신문", "https://www.kookje.co.kr/", "ko"),
    ("매일신문", "https://www.imaeil.com/", "ko"),
    ("경북일보", "https://www.kyongbuk.co.kr/", "ko"),
    ("경북매일", "https://www.kbmaeil.com/", "ko"),
    ("뉴스탑코리아", "https://www.newstopkorea.com/", "ko"),
    ("데일리대구경북뉴스", "https://www.dailydgnews.com/", "ko"),
    ("경기일보", "https://www.kyeonggi.com/", "ko"),
    ("전북일보", "https://www.jjan.kr/", "ko"),
    ("대전일보", "https://www.daejonilbo.com/", "ko"),
    ("충청투데이", "https://www.cctoday.co.kr/", "ko"),
    ("BBC News", "https://www.bbc.com/", "en"),
    ("The New York Times", "https://www.nytimes.com/", "en"),
    ("Nikkei Asia", "https://asia.nikkei.com/", "en"),
    ("The Japan Times", "https://www.japantimes.co.jp/", "en"),
    ("NHK WORLD-JAPAN", "https://www3.nhk.or.jp/nhkworld/", "en"),
    ("Nuclear Energy Institute", "https://www.nei.org/news", "en"),
    ("World Nuclear Association", "https://world-nuclear.org/news", "en"),
    ("IAEA News", "https://www.iaea.org/newscenter/news", "en"),
    ("NRC News", "https://www.nrc.gov/reading-rm/doc-collections/news/", "en"),
    ("Power Engineering International", "https://www.powerengineeringint.com/", "en"),
    ("Energy Intelligence", "https://www.energyintel.com/", "en"),
    ("E&E News", "https://www.eenews.net/", "en"),
    ("Nuclear Street", "https://nuclearstreet.com/", "en"),
    ("Power Technology", "https://www.power-technology.com/", "en"),
    ("World Energy News", "https://worldenergynews.com/", "en"),
    ("The Engineer", "https://www.theengineer.co.uk/", "en"),
    ("Engineering News-Record", "https://www.enr.com/", "en"),
    ("Construction Dive", "https://www.constructiondive.com/", "en"),

    # ─────────────────────────────────────────────
    # 해외 통신사·종합 뉴스 추가
    # ─────────────────────────────────────────────
    ("Agence France-Presse", "https://www.afp.com/en/news-hub", "en"),
    ("UPI", "https://www.upi.com/", "en"),
    ("Anadolu Agency", "https://www.aa.com.tr/en/", "en"),
    ("Xinhua", "https://english.news.cn/", "en"),
    ("Kyodo News", "https://english.kyodonews.net/", "en"),

    # ─────────────────────────────────────────────
    # 미국 신문·방송·경제 매체
    # ─────────────────────────────────────────────
    ("The Washington Post", "https://www.washingtonpost.com/business/", "en"),
    ("USA Today", "https://www.usatoday.com/news/", "en"),
    ("Los Angeles Times", "https://www.latimes.com/business", "en"),
    ("ABC News", "https://abcnews.go.com/Business", "en"),
    ("CBS News", "https://www.cbsnews.com/moneywatch/", "en"),
    ("NBC News", "https://www.nbcnews.com/business", "en"),
    ("NPR", "https://www.npr.org/sections/business/", "en"),
    ("Fox Business", "https://www.foxbusiness.com/energy", "en"),
    ("Forbes", "https://www.forbes.com/energy/", "en"),
    ("Fortune", "https://fortune.com/section/energy/", "en"),
    ("Business Insider", "https://www.businessinsider.com/energy", "en"),

    # ─────────────────────────────────────────────
    # 영국·유럽 신문·방송
    # ─────────────────────────────────────────────
    ("Sky News", "https://news.sky.com/topic/nuclear-10137", "en"),
    ("The Independent", "https://www.independent.co.uk/climate-change/news", "en"),
    ("The Telegraph", "https://www.telegraph.co.uk/business/energy/", "en"),
    ("Euronews", "https://www.euronews.com/green/energy", "en"),
    ("Deutsche Welle", "https://www.dw.com/en/business/s-1431", "en"),
    ("France 24", "https://www.france24.com/en/europe/", "en"),
    ("POLITICO Europe", "https://www.politico.eu/policy-area/energy/", "en"),
    ("Yle News", "https://yle.fi/t/18-209644/en", "en"),
    ("Bulgarian News Agency", "https://www.bta.bg/en/news", "en"),
    ("Romania Insider", "https://www.romania-insider.com/", "en"),
    ("Radio Prague International", "https://english.radio.cz/", "en"),
    ("Notes from Poland", "https://notesfrompoland.com/", "en"),
    ("ERR News", "https://news.err.ee/", "en"),

    # ─────────────────────────────────────────────
    # 캐나다·호주
    # ─────────────────────────────────────────────
    ("CBC News", "https://www.cbc.ca/news/business", "en"),
    ("CTV News", "https://www.ctvnews.ca/business", "en"),
    ("Global News Canada", "https://globalnews.ca/business/", "en"),
    ("The Globe and Mail", "https://www.theglobeandmail.com/business/", "en"),
    ("ABC Australia", "https://www.abc.net.au/news/business/", "en"),
    ("The Guardian Australia", "https://www.theguardian.com/australia-news", "en"),

    # ─────────────────────────────────────────────
    # 인도·일본·아시아
    # ─────────────────────────────────────────────
    ("The Hindu", "https://www.thehindu.com/business/", "en"),
    ("The Indian Express", "https://indianexpress.com/section/business/", "en"),
    ("The Times of India", "https://timesofindia.indiatimes.com/business", "en"),
    ("The Economic Times", "https://economictimes.indiatimes.com/industry/energy", "en"),
    ("Business Standard India", "https://www.business-standard.com/industry/news", "en"),
    ("NDTV", "https://www.ndtv.com/business", "en"),
    ("Hindustan Times", "https://www.hindustantimes.com/business", "en"),
    ("VnExpress International", "https://e.vnexpress.net/nuclear-power/tag-760379.html", "en"),
    ("Vietnam News", "https://vietnamnews.vn/", "en"),
    ("VietnamPlus", "https://en.vietnamplus.vn/", "en"),
    ("VietnamNet Global", "https://vietnamnet.vn/en", "en"),
    ("Channel NewsAsia", "https://www.channelnewsasia.com/business", "en"),
    ("The Straits Times", "https://www.straitstimes.com/business", "en"),

    # ─────────────────────────────────────────────
    # 중동: UAE·사우디 포함
    # ─────────────────────────────────────────────
    ("The National", "https://www.thenationalnews.com/tags/nuclear-energy/", "en"),
    ("Gulf News", "https://gulfnews.com/business/energy", "en"),
    ("Khaleej Times", "https://www.khaleejtimes.com/business/energy", "en"),
    ("Arab News", "https://www.arabnews.com/tags/nuclear-energy", "en"),
    ("Saudi Gazette", "https://saudigazette.com.sa/", "en"),
    ("Al Arabiya English", "https://english.alarabiya.net/business/energy", "en"),
    ("Al Jazeera", "https://www.aljazeera.com/economy/", "en"),

    # ─────────────────────────────────────────────
    # 유럽·글로벌 에너지/전력 전문매체 추가
    # ─────────────────────────────────────────────
    ("Energy Central", "https://www.energycentral.com/", "en"),
    ("Energy Live News", "https://www.energylivenews.com/", "en"),
    ("Montel News", "https://montelnews.com/", "en"),
    ("Offshore Energy", "https://www.offshore-energy.biz/", "en"),
]

# 매체 수가 늘어난 만큼 목록 페이지는 병렬 처리하되, 각 매체에서 관련 가능성이 있는 기사만 원문 조회합니다.
DIRECT_PAGE_WORKERS = 16
DIRECT_PAGE_TIMEOUT_SECONDS = 8
DIRECT_PAGE_MAX_LINKS = 400

# 영문 일반매체는 기사 제목에 아래 원전·원자력 후보어가 있으면 원문까지 확인합니다.
# 최종 기사 포함 여부는 원문 description까지 읽은 뒤 classify_direct_article()에서 다시 판단합니다.
PRIORITY_NUCLEAR_MARKET_TERMS = {
    # 미국
    "united states", "u.s.", "usa", "american nuclear",
    "nrc", "department of energy", "doe",
    "palisades", "fermi america", "project matador", "amarillo",
    "vogtle", "diablo canyon", "three mile island",
    "westinghouse", "holtec", "ap1000", "ap300",

    # 영국
    "united kingdom", "uk nuclear", "britain nuclear",
    "great british nuclear", "gbn", "sizewell", "hinkley point",
    "wylfa", "oldbury", "onr",

    # 핀란드
    "finland", "finnish nuclear", "olkiluoto", "loviisa",
    "fennovoima", "tvo", "fortum nuclear",

    # 불가리아
    "bulgaria", "bulgarian nuclear", "kozloduy", "belene",

    # 루마니아
    "romania", "romanian nuclear", "cernavoda", "nuclearelectrica",

    # 인도
    "india", "indian nuclear", "kudankulam", "jaitapur",
    "kaiga", "npcil",

    # 베트남
    "vietnam", "vietnamese nuclear", "베트남 원전",
    "ninh thuan", "ninh thuận", "ninh-thuan",
    "닌투언", "닌투안", "evn", "vinatom", "vaea", "varans",

    # UAE
    "uae nuclear", "united arab emirates nuclear",
    "barakah", "바라카", "enec", "nawah energy", "fanr",

    # 사우디아라비아
    "saudi nuclear", "saudi nuclear power", "사우디 원전",
    "k.a.care", "kacare",
    "king abdullah city for atomic and renewable energy",

    # 주요 유럽 확장
    "czech", "czechia", "dukovany", "temelin",
    "poland nuclear", "lubiatowo",
    "slovenia nuclear", "krsko",
    "sweden nuclear", "ringhals", "forsmark",
    "france nuclear", "flamanville", "edf", "framatome",
}


def is_priority_nuclear_market_candidate(title: str, summary: str = "") -> bool:
    haystack = html.unescape(f"{title} {summary}").lower()
    # 우선시장 국가/프로젝트 용어가 잡히고 원전 관련성이 있거나,
    # 자체적으로 원전 프로젝트를 특정하는 고유명사인 경우 후보로 엽니다.
    return any(term in haystack for term in PRIORITY_NUCLEAR_MARKET_TERMS)


ENGLISH_NUCLEAR_CANDIDATE_TERMS = {
    "nuclear", "reactor", "smr", "small modular reactor",
    "advanced reactor", "microreactor", "atomic energy",
    "nuclear power", "nuclear energy", "nuclear plant",
    "nuclear station", "nuclear project", "nuclear construction",
    "new nuclear", "new build", "decommissioning",
    "spent fuel", "nuclear fuel", "fuel cycle",
    "uranium", "enrichment", "radioactive waste", "nuclear waste",
    "waste repository", "life extension", "restart", "uprate",
    "licensing", "regulator", "ap1000", "ap300", "natrium", "smr-300",
    "bwr", "pwr", "vver", "epr", "candu",
    "westinghouse", "holtec", "terrapower", "fermi america",
    "palisades", "dukovany", "kozloduy", "barakah",
    "rosatom", "framatome", "khnp", "iaea", "nrc",
    "united states", "u.s.", "usa", "american nuclear",
    "united kingdom", "uk nuclear", "great british nuclear", "gbn",
    "finland", "finnish nuclear", "olkiluoto", "loviisa",
    "bulgaria", "bulgarian nuclear", "kozloduy", "belene",
    "romania", "romanian nuclear", "cernavoda", "nuclearelectrica",
    "india", "indian nuclear", "kudankulam", "jaitapur", "npcil",
    "ninh thuan", "ninh thuận", "vietnam nuclear", "vietnamese nuclear",
    "barakah", "enec", "fanr", "nawah",
    "saudi nuclear", "k.a.care", "kacare",
    "sizewell c", "hinkley point c", "wylfa",
    "kozloduy 7", "kozloduy 8", "cernavoda 3", "cernavoda 4",
    "dukovany ii", "olkiluoto", "loviisa", "darlington", "pickering",
}

# 이 매체/기관은 원자력 전문 페이지이므로 제목이 짧거나 일반적인 표현이어도
# 기사 URL처럼 보이면 원문을 열어 실제 내용을 확인합니다.
ENGLISH_ENERGY_PAGE_HINTS = {
    "/energy", "energy-", "/power", "power-", "commodity",
    "utility", "nuclear", "engineering",
}


def _english_energy_page(page_url: str) -> bool:
    lower = page_url.lower()
    dedicated_hosts_or_paths = (
        "yle.fi/t/18-209644", "vnexpress.net/nuclear-power",
        "thenationalnews.com/tags/nuclear-energy", "arabnews.com/tags/nuclear-energy",
        "news.sky.com/topic/nuclear", "ans.org/news",
    )
    return (
        any(hint in lower for hint in ENGLISH_ENERGY_PAGE_HINTS)
        or any(token in lower for token in dedicated_hosts_or_paths)
    )


def _looks_like_article_candidate_url(url: str) -> bool:
    lower = url.lower()
    return bool(
        re.search(r"/20\\d{2}/\\d{1,2}/\\d{1,2}/", lower)
        or re.search(r"/20\\d{2}/\\d{1,2}/", lower)
        or any(token in lower for token in (
            "/article/", "/articles/", "/news/", "/story/", "/stories/",
            "/analysis/", "/features/", ".html",
        ))
    )


NUCLEAR_SPECIALIST_PUBLISHERS = {
    "World Nuclear News",
    "Nuclear Engineering International",
    "NucNet",
    "Nuclear Energy Institute",
    "World Nuclear Association",
    "IAEA News",
    "NRC News",
    "Nuclear Street",
}


# 언론사 직접 수집 기사 분류 시 너무 넓게 잡히지 않도록 핵심어를 사용합니다.
DIRECT_GROUP_KEYWORDS = {
    "현대건설": [
        "현대건설", "hyundai e&c", "hyundai engineering & construction", "hdec",
    ],
    "타 건설사": [
        "삼성물산 건설부문", "samsung c&t", "대우건설", "daewoo e&c",
        "dl이앤씨", "dl e&c", "gs건설", "gs e&c", "sk에코플랜트",
        "sk ecoplant", "포스코이앤씨", "posco e&c", "롯데건설",
        "lotte e&c", "현대엔지니어링", "hyundai engineering",
        "hdc현대산업개발", "hanwha construction", "한화 건설부문",
        "두산에너빌리티", "doosan enerbility",
    ],
    "한국수력원자력": [
        "한국수력원자력", "한수원", "khnp",
    ],
    "한국전력": [
        "한국전력", "한전", "kepco",
    ],
    "원전 관계부처": [
        "산업통상부", "산업통상자원부", "기후에너지환경부",
        "과학기술정보통신부", "과기정통부", "김정관", "문신학",
        "양기욱", "여한구", "강감찬", "김창희",
    ],
    "원전 대미투자": [
        "대미투자", "대미 투자", "대미투자펀드", "대미 투자 펀드",
        "한미 투자", "u.s. investment", "us investment",
        "korea-us investment", "nuclear investment fund",
    ],
    "Holtec": [
        "holtec", "홀텍", "smr-300", "palisades", "oyster creek",
    ],
    "TerraPower": [
        "terrapower", "테라파워", "natrium", "kemmerer",
    ],
    "Westinghouse": [
        "westinghouse", "웨스팅하우스", "ap1000", "ap300",
    ],
    "Fermi America": [
        "fermi america", "페르미 아메리카", "페르미아메리카",
        "퍼미 아메리카", "퍼미아메리카", "project matador",
        "프로젝트 마타도르", "hypergrid", "하이퍼그리드",
        "amarillo nuclear", "애머릴로 원전", "carson county nuclear",
        "카슨 카운티 원전",
    ],
    "SMR": [
        "smr", "소형모듈원자로", "소형 모듈 원자로",
        "small modular reactor", "차세대원자로", "advanced reactor",
        "microreactor",
    ],
    "Nuclear Power·Nuclear Energy": [
        "nuclear power", "nuclear energy", "nuclear power plant",
        "nuclear construction", "nuclear project", "nuclear new build",
        "new nuclear build",
    ],
    "원자력": [
        "원전", "원자력", "원자로", "핵발전", "신한울", "새울원전",
        "새울원자력", "고리원전", "한빛원전", "한울원전", "월성원전",
        "nuclear", "reactor",
    ],
}

CIVIL_NUCLEAR_RELEVANCE_TERMS = {
    "원전", "원자력", "원자로", "원전건설", "원전 건설",
    "원전해체", "원전 해체", "소형모듈원자로", "소형 모듈 원자로",
    "차세대원자로", "차세대 원자로",
    "nuclear power", "nuclear energy", "nuclear plant",
    "nuclear power plant", "nuclear station", "nuclear reactor",
    "reactor", "smr", "small modular reactor", "advanced reactor",
    "microreactor", "new nuclear", "nuclear new build",
    "nuclear construction", "nuclear project",
    "핵연료", "사용후핵연료", "사용후 핵연료", "방사성폐기물",
    "고준위폐기물", "고준위 폐기물", "원전 계속운전", "계속운전",
    "수명연장", "원전 재가동",
    "uranium", "uranium mining", "uranium enrichment",
    "nuclear fuel", "fuel cycle", "spent fuel",
    "radioactive waste", "nuclear waste", "waste repository",
    "decommissioning", "nuclear decommissioning",
    "life extension", "plant life extension",
    "reactor restart", "nuclear restart", "capacity uprate",
    "nuclear licensing", "reactor licensing", "nuclear regulator",
    "ap1000", "ap300", "natrium", "smr-300",
    "bwr", "pwr", "vver", "epr", "candu",
    "westinghouse", "holtec", "terrapower", "rosatom",
    "khnp", "edf", "framatome",
}


def is_civil_nuclear_relevant(title: str, summary: str = "") -> bool:
    haystack = html.unescape(f"{title} {summary}").lower()
    return any(term in haystack for term in CIVIL_NUCLEAR_RELEVANCE_TERMS)


DIRECT_GROUP_PRIORITY = [
    "원전 대미투자",
    "Fermi America",
    "Holtec",
    "TerraPower",
    "Westinghouse",
    "현대건설",
    "타 건설사",
    "한국수력원자력",
    "한국전력",
    "원전 관계부처",
    "SMR",
    "Nuclear Power·Nuclear Energy",
    "원자력",
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
    "gambling", "casino", "bet", "betting", "sportsbook", "lottery",
    "jackpot", "poker", "slot machine",

    # 성인·불법·유해 홍보
    "성인사이트", "성인 사이트", "야동", "조건만남", "불법대출",
    "불법 도박", "불법도박", "마약 판매", "해킹 판매",
    "adult site", "porn", "escort", "illegal gambling",

    # 이번에 읽음된 무관 기사
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


EXCLUDED_PUBLISHERS = {"Nuclear Newswire", "Nuclear Newswire (ANS)"}

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
    description: str = ""


def period(now: datetime) -> tuple[datetime, datetime]:
    """
    실행 시점의 한국시간을 기준으로 '금일' 구간을 매일 자동 갱신합니다.

    예)
    2026-08-05 06:00 이후 실행:
      2026-08-04 06:00 ~ 2026-08-05 06:00
    2026-08-06 06:00 이후 실행:
      2026-08-05 06:00 ~ 2026-08-06 06:00

    월요일은 주말을 포함하여 금요일 06:00 ~ 월요일 06:00으로 계산합니다.
    """
    now_kst = now.astimezone(KST)
    report_end = now_kst.replace(hour=6, minute=0, second=0, microsecond=0)

    if now_kst < report_end:
        report_end -= timedelta(days=1)

    if report_end.weekday() == 5:
        report_end -= timedelta(days=1)
    elif report_end.weekday() == 6:
        report_end -= timedelta(days=2)

    if report_end.weekday() == 0:
        report_start = report_end - timedelta(days=3)
    else:
        report_start = report_end - timedelta(days=1)

    return report_start, report_end


def _previous_report_boundary(boundary: datetime) -> datetime:
    """
    직전 보고 기준시각(06:00)을 반환합니다.
    월요일 06:00의 직전 기준시각은 금요일 06:00입니다.
    """
    previous = boundary - timedelta(days=1)
    while previous.weekday() >= 5:  # 토(5), 일(6) 건너뜀
        previous -= timedelta(days=1)
    return previous


def _next_report_boundary(boundary: datetime) -> datetime:
    """
    다음 보고 기준시각(06:00)을 반환합니다.
    금요일 06:00의 다음 기준시각은 월요일 06:00입니다.
    """
    following = boundary + timedelta(days=1)
    while following.weekday() >= 5:  # 토(5), 일(6) 건너뜀
        following += timedelta(days=1)
    return following


def brief_periods(now: datetime) -> dict[str, tuple[datetime, datetime]]:
    """
    전일·금일·익일을 '보고구간' 기준으로 반환합니다.

    예) 화요일:
      전일 = 금요일 06:00 ~ 월요일 06:00
      금일 = 월요일 06:00 ~ 화요일 06:00
      익일 = 화요일 06:00 ~ 수요일 06:00

    예) 금요일:
      금일 = 목요일 06:00 ~ 금요일 06:00
      익일 = 금요일 06:00 ~ 월요일 06:00
    """
    today_start, today_end = period(now)

    previous_end = today_start
    previous_start = _previous_report_boundary(previous_end)

    next_start = today_end
    next_end = _next_report_boundary(next_start)

    return {
        "전일": (previous_start, previous_end),
        "금일": (today_start, today_end),
        "익일": (next_start, next_end),
    }


def split_title_and_publisher(raw_title: str) -> tuple[str, str]:
    raw_title = html.unescape(raw_title or "").strip()
    match = re.match(r"^(.*)\s+-\s+([^-]{2,100})$", raw_title)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return raw_title, ""


def extract_image(entry) -> str:
    """
    RSS 단계에서 대표 이미지를 최대한 확보합니다.
    우선순위:
    1) media:content / media:thumbnail
    2) enclosure
    3) RSS link의 image enclosure
    4) content/summary/description 내부 img(src, data-src, srcset)
    """

    # 1. Media RSS
    for field in ("media_content", "media_thumbnail"):
        values = getattr(entry, field, None)
        if not values:
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            url = (
                value.get("url")
                or value.get("href")
                or value.get("src")
                or ""
            ).strip()
            if url:
                return html.unescape(url)

    # 2. RSS enclosure
    enclosures = getattr(entry, "enclosures", None) or []
    for enclosure in enclosures:
        if not isinstance(enclosure, dict):
            continue
        content_type = str(
            enclosure.get("type")
            or enclosure.get("medium")
            or ""
        ).lower()
        url = (
            enclosure.get("href")
            or enclosure.get("url")
            or ""
        ).strip()
        if url and (
            content_type.startswith("image/")
            or content_type == "image"
            or re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", url, re.I)
        ):
            return html.unescape(url)

    # 3. feedparser가 links 배열로 제공하는 enclosure
    for link_info in getattr(entry, "links", None) or []:
        if not isinstance(link_info, dict):
            continue
        rel = str(link_info.get("rel", "")).lower()
        content_type = str(link_info.get("type", "")).lower()
        href = str(link_info.get("href", "")).strip()
        if not href:
            continue
        if (
            rel == "enclosure"
            and (
                content_type.startswith("image/")
                or re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", href, re.I)
            )
        ):
            return html.unescape(href)

    # 4. RSS content / summary / description HTML 내부 이미지
    html_candidates: list[str] = []

    content_values = getattr(entry, "content", None) or []
    for content in content_values:
        if isinstance(content, dict):
            value = content.get("value", "")
            if value:
                html_candidates.append(str(value))

    summary = (
        getattr(entry, "summary", "")
        or getattr(entry, "description", "")
        or ""
    )
    if summary:
        html_candidates.append(str(summary))

    for raw_html in html_candidates:
        match = re.search(
            r'<img[^>]+(?:src|data-src|data-original|data-lazy-src)\s*=\s*["\']([^"\']+)["\']',
            raw_html,
            re.I,
        )
        if match:
            return html.unescape(match.group(1).strip())

        srcset_match = re.search(
            r'<img[^>]+srcset\s*=\s*["\']([^"\']+)["\']',
            raw_html,
            re.I,
        )
        if srcset_match:
            candidates = []
            for part in srcset_match.group(1).split(","):
                url = part.strip().split()[0] if part.strip() else ""
                if url:
                    candidates.append(url)
            if candidates:
                return html.unescape(candidates[-1])

    return ""


def clean_description(raw: str, title: str = "", publisher: str = "") -> str:
    """RSS/HTML에서 가져온 설명을 카드용 짧은 문장으로 정리합니다."""
    if not raw:
        return ""

    cleaned = re.sub(r"<[^>]+>", " ", raw)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return ""

    # RSS summary가 단순히 '기사제목 + 언론사'만 반복하는 경우 제외
    normalized_cleaned = normalized(cleaned)
    normalized_title = normalized(title)
    normalized_publisher = normalized(publisher)

    if normalized_title and normalized_title in normalized_cleaned:
        remainder = normalized_cleaned.replace(normalized_title, " ", 1).strip()
        if not remainder or remainder == normalized_publisher:
            return ""

    # 너무 짧은 문자열은 기사 미리보기로 사용하지 않음
    if len(cleaned) < 20:
        return ""

    # 카드에는 과도하게 긴 문장이 필요하지 않음
    return cleaned[:320].rstrip()


class _MetaTagParser(HTMLParser):
    """원문 HTML에서 메타정보, 대표 이미지 후보, 본문 문단 후보를 추출합니다."""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}
        self.base_url = base_url
        self.image_candidates: list[tuple[int, str]] = []
        self.jsonld_image_candidates: list[str] = []
        self.paragraphs: list[str] = []
        self._in_p = False
        self._p_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attr_map = {
            str(key).lower(): str(value)
            for key, value in attrs
            if key and value is not None
        }

        if tag.lower() == "meta":
            key = (
                attr_map.get("property")
                or attr_map.get("name")
                or attr_map.get("itemprop")
                or ""
            ).lower()
            content = attr_map.get("content", "").strip()
            if key and content and key not in self.values:
                self.values[key] = content
            return

        if tag.lower() in ("img", "source"):
            src = (
                attr_map.get("src")
                or attr_map.get("data-src")
                or attr_map.get("data-original")
                or attr_map.get("data-lazy-src")
                or ""
            ).strip()

            srcset = attr_map.get("srcset", "").strip()
            if not src and srcset:
                srcset_items = []
                for part in srcset.split(","):
                    token = part.strip()
                    if not token:
                        continue
                    src_url = token.split()[0]
                    descriptor = token.split()[1] if len(token.split()) > 1 else ""
                    score_hint = 0
                    match = re.search(r"(\d+)(w|x)", descriptor)
                    if match:
                        score_hint = int(match.group(1))
                    srcset_items.append((score_hint, src_url))
                if srcset_items:
                    srcset_items.sort(key=lambda item: item[0], reverse=True)
                    src = srcset_items[0][1]

            if not src:
                return

            src_lower = src.lower()
            blocked = (
                "logo", "icon", "sprite", "avatar", "profile", "banner",
                "advert", "ads.", "tracking", "pixel", "favicon",
            )
            if any(token in src_lower for token in blocked):
                return

            try:
                width = int(re.sub(r"[^0-9]", "", attr_map.get("width", "")) or "0")
            except Exception:
                width = 0
            try:
                height = int(re.sub(r"[^0-9]", "", attr_map.get("height", "")) or "0")
            except Exception:
                height = 0

            class_text = f"{attr_map.get('class', '')} {attr_map.get('id', '')} {attr_map.get('itemprop', '')}".lower()
            score = width * height
            if tag.lower() == "source":
                score += 100_000
            if any(word in class_text for word in ("article", "news", "content", "photo", "image", "thumb", "figure", "lead", "main")):
                score += 500_000

            self.image_candidates.append((score, urljoin(self.base_url, src)))
            return

        if tag.lower() == "p":
            self._in_p = True
            self._p_chunks = []

    def handle_endtag(self, tag: str):
        if tag.lower() == "p" and self._in_p:
            paragraph = re.sub(r"\s+", " ", " ".join(self._p_chunks)).strip()
            if paragraph:
                self.paragraphs.append(paragraph)
            self._in_p = False
            self._p_chunks = []

    def handle_data(self, data: str):
        if self._in_p and data.strip():
            self._p_chunks.append(data.strip())



def _extract_jsonld_image_candidates(decoded_html: str, base_url: str) -> list[str]:
    """
    기사 페이지 JSON-LD에 들어있는 image 필드를 별도로 추출합니다.
    미리보기(description)와 무관하게 대표 이미지가 있는 경우가 많아
    썸네일 확보율을 높이는 데 사용합니다.
    """
    results: list[str] = []

    def add_candidate(value):
        if not value:
            return
        if isinstance(value, str):
            results.append(urljoin(base_url, value.strip()))
            return
        if isinstance(value, list):
            for item in value:
                add_candidate(item)
            return
        if isinstance(value, dict):
            for key in ("url", "contentUrl", "thumbnailUrl"):
                if value.get(key):
                    results.append(urljoin(base_url, str(value[key]).strip()))
            if value.get("image"):
                add_candidate(value.get("image"))

    for match in re.finditer(
        r'<script[^>]+type=["\\\']application/ld\\+json["\\\'][^>]*>(.*?)</script>',
        decoded_html,
        re.I | re.S,
    ):
        raw_json = html.unescape(match.group(1)).strip()
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except Exception:
            continue

        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@graph") and isinstance(item["@graph"], list):
                graph_items = [x for x in item["@graph"] if isinstance(x, dict)]
            else:
                graph_items = [item]

            for graph_item in graph_items:
                add_candidate(graph_item.get("image"))

    # 중복 제거
    unique: list[str] = []
    seen = set()
    for url in results:
        normalized = url.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _best_jsonld_image_candidate(candidates: list[str]) -> str:
    blocked = ("logo", "icon", "sprite", "avatar", "profile", "banner", "pixel", "favicon")
    for candidate in candidates:
        lower = candidate.lower()
        if any(token in lower for token in blocked):
            continue
        return candidate
    return ""



def _best_html_image_candidate(parser: _MetaTagParser) -> str:
    if not parser.image_candidates:
        return ""
    parser.image_candidates.sort(key=lambda item: item[0], reverse=True)
    return parser.image_candidates[0][1]


def _best_paragraph_description(
    parser: _MetaTagParser,
    title: str,
    publisher: str,
) -> str:
    """
    메타 description이 없을 때 본문 첫 문단 중 기사다운 문장을 선택합니다.
    메뉴/저작권/기자정보 같은 짧은 문장은 제외합니다.
    """
    blocked_terms = (
        "무단전재", "재배포", "저작권", "기자", "입력", "수정",
        "구독", "로그인", "copyright", "all rights reserved",
    )
    for paragraph in parser.paragraphs[:30]:
        cleaned = clean_description(paragraph, title, publisher)
        if not cleaned:
            continue
        lower = cleaned.lower()
        if any(term in lower for term in blocked_terms):
            continue
        if len(cleaned) >= 35:
            return cleaned
    return ""


def _fetch_article_metadata(article: Article) -> tuple[str, str]:
    """
    RSS에 이미지/설명이 부족한 경우 원문 페이지 메타태그에서 보완합니다.
    원문 접근 실패는 빈 값으로 처리하여 전체 수집을 중단하지 않습니다.
    """
    if article.image and article.description:
        return article.image, article.description

    try:
        request = Request(
            article.link,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        with urlopen(request, timeout=ARTICLE_META_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            payload = response.read(ARTICLE_META_MAX_BYTES)

        # 검색 중간 페이지 등 원문이 아닌 호스트에서는 잘못된 대표이미지를 쓰지 않습니다.
        final_host = urlparse(final_url).netloc.lower()
        if not final_host:
            return article.image, article.description

        decoded = payload.decode("utf-8", errors="ignore")
        parser = _MetaTagParser(final_url)
        parser.feed(decoded)
        parser.jsonld_image_candidates = _extract_jsonld_image_candidates(decoded, final_url)

        image = article.image
        if not image:
            image = (
                parser.values.get("og:image")
                or parser.values.get("twitter:image")
                or parser.values.get("twitter:image:src")
                or parser.values.get("image")
                or _best_jsonld_image_candidate(parser.jsonld_image_candidates)
                or _best_html_image_candidate(parser)
                or ""
            ).strip()
            if image:
                image = urljoin(final_url, image)

        description = article.description
        if not description:
            raw_description = (
                parser.values.get("og:description")
                or parser.values.get("twitter:description")
                or parser.values.get("description")
                or ""
            )
            description = clean_description(
                raw_description,
                article.title,
                article.publisher,
            )

        # 메타 설명이 없으면 실제 본문 첫 문단에서 보완
        if not description:
            description = _best_paragraph_description(
                parser,
                article.title,
                article.publisher,
            )

        return image, description
    except Exception:
        return article.image, article.description



def _image_extension(content_type: str, url: str) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if content_type in mapping:
        return mapping[content_type]

    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def _download_thumbnail(image_url: str) -> str:
    if not image_url:
        return ""
    if image_url.startswith("assets/thumbnails/"):
        return image_url

    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(
        image_url.encode("utf-8", errors="ignore")
    ).hexdigest()[:24]

    for ext in (".jpg", ".png", ".webp", ".gif"):
        existing = THUMBNAIL_DIR / f"{digest}{ext}"
        if existing.exists() and existing.stat().st_size > 0:
            return existing.as_posix()

    try:
        parsed = urlparse(image_url)
        referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else image_url
        request = Request(
            image_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36"
                ),
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=THUMBNAIL_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/"):
                return ""
            payload = response.read(THUMBNAIL_MAX_BYTES + 1)

        if not payload or len(payload) > THUMBNAIL_MAX_BYTES:
            return ""

        ext = _image_extension(content_type, image_url)
        target = THUMBNAIL_DIR / f"{digest}{ext}"
        target.write_bytes(payload)
        return target.as_posix()
    except Exception:
        return ""


def cache_article_thumbnails(
    articles_by_period: dict[str, list[Article]],
) -> None:
    """표시될 기사 이미지만 병렬 다운로드하여 로컬 경로로 바꿉니다."""
    by_image_url: dict[str, list[Article]] = {}

    for articles in articles_by_period.values():
        for article in articles:
            if article.image:
                by_image_url.setdefault(article.image, []).append(article)

    if not by_image_url:
        return

    workers = min(THUMBNAIL_DOWNLOAD_WORKERS, len(by_image_url))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_download_thumbnail, image_url): image_url
            for image_url in by_image_url
        }

        for future in as_completed(future_map):
            original_url = future_map[future]
            try:
                local_path = future.result()
            except Exception:
                continue
            if not local_path:
                continue

            for article in by_image_url.get(original_url, []):
                article.image = local_path


def cleanup_old_thumbnails(now: datetime) -> None:
    if not THUMBNAIL_DIR.exists():
        return

    cutoff = now.timestamp() - THUMBNAIL_KEEP_DAYS * 24 * 60 * 60
    for path in THUMBNAIL_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except Exception:
            continue


def enrich_article_metadata(articles_by_period: dict[str, list[Article]]) -> None:
    """
    전일/금일/익일에서 실제 표시될 기사만 대상으로 대표 이미지와 설명을 보완합니다.
    동일 URL은 한 번만 조회하고 결과를 모든 기간의 동일 기사에 재사용합니다.
    """
    articles_by_url: dict[str, list[Article]] = {}

    for articles in articles_by_period.values():
        for article in articles:
            articles_by_url.setdefault(article.link, []).append(article)

    targets = [
        items[0]
        for items in articles_by_url.values()
        if not (items[0].image and items[0].description)
    ]

    if not targets:
        return

    workers = min(ARTICLE_META_WORKERS, len(targets))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_fetch_article_metadata, article): article.link
            for article in targets
        }

        for future in as_completed(future_map):
            link = future_map[future]
            try:
                image, description = future.result()
            except Exception:
                continue

            for article in articles_by_url.get(link, []):
                if image and not article.image:
                    article.image = image
                if description and not article.description:
                    article.description = description


HYUNDAI_VOLLEYBALL_TERMS = {
    # 종목/리그 직접 표현
    "배구", "여자배구", "프로배구", "v리그", "v-리그",
    "volleyball", "v-league", "v league", "배구단",

    # 현대건설 배구단 관련 인물/경기 문맥
    "김연경", "신인감독", "격돌", "맞대결",
    "세트", "득점", "리시브", "블로킹", "스파이크",
    "코트", "홈경기", "원정경기",
}


def is_hyundai_volleyball_article(title: str, summary: str = "") -> bool:
    """현대건설 힐스테이트 배구단 관련 기사를 회사 뉴스에서 제외합니다."""
    haystack = html.unescape(f"{title} {summary}").lower()

    # 명확한 배구 단서가 하나라도 있으면 제외
    if any(term in haystack for term in HYUNDAI_VOLLEYBALL_TERMS):
        return True

    # '감독/선수/경기/승리/패배'는 일반 기사에도 나올 수 있으므로
    # 스포츠 문맥 단서가 2개 이상 함께 있을 때만 제외
    weak_terms = {"감독", "선수", "선수단", "경기", "승리", "패배"}
    weak_hits = sum(1 for term in weak_terms if term in haystack)
    return weak_hits >= 2


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
    "두산에너빌리티": "두산에너빌리티",
    "두산 에너빌리티": "두산에너빌리티",
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
    "epc 낙찰": "epc수주",
    "epc 낙찰자 선정": "epc수주",
    "낙찰자로 선정": "epc수주",
    "낙찰자 선정": "epc수주",
    "우선협상대상자 선정": "epc수주",
    "사업자로 선정": "epc수주",
    "시공사로 선정": "epc수주",
    "짓는다": "epc수주",
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
    "하동복합": "하동복합발전",
    "하동 복합": "하동복합발전",
    "하동복합화력": "하동복합발전",
    "하동 복합화력": "하동복합발전",
    "하동 가스발전소": "하동복합발전",
    "하동석탄 화력 대체 가스발전소": "하동복합발전",
    "하동 석탄화력 대체 가스발전소": "하동복합발전",
}

# 언론사마다 같은 사건을 다른 표현으로 쓰는 경우를 일반화해 비교하기 위한 동의어 사전
EVENT_PHRASE_ALIASES = {
    "공동 주택": "공동주택",
    "주거 단지": "공동주택",
    "주택 단지": "공동주택",
    "아파트": "공동주택",
    "주차 난": "주차난",
    "주차난": "주차문제",
    "주차 공간": "주차공간",
    "주차공간": "주차문제",
    "주차 로봇": "주차로봇",
    "로봇 주차": "주차로봇",
    "실증 시작": "실증",
    "실증 착수": "실증",
    "시범 운영": "실증",
    "시범운영": "실증",
    "검증": "실증",
    "테스트": "실증",
    "투입": "도입",
    "배치": "도입",
    "적용": "도입",
    "도입": "도입",
    "협약 체결": "협약",
    "업무 협약": "협약",
    "양해 각서": "협약",
    "양해각서": "협약",
    "mou": "협약",
    "착공": "건설시작",
    "첫 삽": "건설시작",
    "첫삽": "건설시작",
    "준공": "건설완료",
    "완공": "건설완료",
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
    "entity:두산에너빌리티": {
        "두산에너빌리티", "두산 에너빌리티",
    },
    "location:하동": {
        "하동", "경남 하동", "하동군",
    },
    "object:하동복합발전": {
        "하동복합", "하동 복합", "하동복합화력", "하동 복합화력",
        "하동가스발전소", "하동 가스발전소",
        "하동석탄화력대체가스발전소", "하동 석탄화력 대체 가스발전소",
        "하동석탄 화력 대체 가스발전소",
    },
    "action:epc수주": {
        "epc낙찰", "epc 낙찰", "낙찰자선정", "낙찰자 선정",
        "낙찰자로선정", "낙찰자로 선정", "사업자로선정", "사업자로 선정",
        "시공사선정", "시공사 선정", "짓는다",
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
    "target:공동주택": {
        "공동주택", "아파트", "주거단지", "주택단지",
    },
    "topic:주차로봇": {
        "주차로봇", "주차 로봇", "주차난", "주차공간",
        "주차 공간", "parking robot", "robot parking",
    },
    "action:실증": {
        "실증", "실증시작", "실증 시작", "시범운영", "시범 운영",
        "검증", "테스트", "pilot", "demonstration",
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
    for source, target in EVENT_PHRASE_ALIASES.items():
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
    has_topic = any(item.startswith("topic:") for item in shared)
    has_subject_detail = any(
        item.startswith(("target:", "object:", "location:"))
        for item in shared
    )

    # 주체 + 행위 + 대상/목적물/지역이 겹치면 같은 사건
    if has_entity and has_action and has_subject_detail and len(shared) >= 3:
        return True

    # 언론사마다 회사명을 생략하더라도
    # '공동주택 + 주차로봇/주차난 + 실증'처럼 대상·주제·행위가 모두 같으면
    # 동일 사건으로 처리합니다.
    if has_action and has_topic and has_subject_detail and len(shared) >= 3:
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



def article_event_text(article: Article) -> str:
    """
    중복 판정용 텍스트.
    제목을 가장 중요하게 보고, 원문/RSS 미리보기는 보조 정보로 사용합니다.
    """
    description = (article.description or "").strip()
    if description:
        # 설명 전체를 쓰면 서로 다른 후속기사까지 합쳐질 수 있어 앞부분만 사용합니다.
        return f"{article.title} {description[:220]}"
    return article.title


def event_signature_tokens(article: Article) -> set[str]:
    """
    일반적인 보도자료/동일 사건 판정을 위한 핵심 토큰 집합.
    너무 흔한 단어는 제외하고 회사·대상·기술·지역·행위 표현을 남깁니다.
    """
    generic = {
        "관련", "대한", "통해", "위해", "추진", "지원", "사업", "발표",
        "밝혀", "나서", "진행", "계획", "예정", "이번", "제공", "실시",
        "본격", "확대", "강화", "해결", "시작", "최대", "더", "한다",
        "현대", "그룹", "기술", "건설", "회사", "기업", "업계",
        "the", "a", "an", "and", "for", "to", "of", "in", "on",
    }
    tokens = keyword_set(article_event_text(article))
    return {
        token for token in tokens
        if len(token) >= 2 and token not in generic
    }


def article_ngram_similarity(article: Article, existing: Article) -> float:
    """
    제목만 다르더라도 설명에 같은 사건 문구가 있으면 잡을 수 있도록
    제목+설명 앞부분의 문자 2-gram 포함률을 계산합니다.
    """
    a = compact_title(article_event_text(article))
    b = compact_title(article_event_text(existing))
    if not a or not b:
        return 0.0

    def grams(value: str, size: int = 2) -> set[str]:
        if len(value) < size:
            return {value}
        return {value[i:i + size] for i in range(len(value) - size + 1)}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def same_event_general(article: Article, existing: Article) -> bool:
    """
    특정 기사명을 하드코딩하지 않고 같은 날의 동일 사건을 일반적으로 판정합니다.

    - 제목 핵심어
    - 제목+기사설명 핵심어
    - 사건 개념(entity/target/object/topic/action/location)
    - 문자 유사도
    를 조합합니다.

    기사 누락 방지를 위해 단일 조건 하나만으로는 삭제하지 않습니다.
    """
    article_day = article.published.astimezone(KST).date()
    existing_day = existing.published.astimezone(KST).date()
    if article_day != existing_day:
        return False

    title_keys_a = meaningful_keywords(article.title)
    title_keys_b = meaningful_keywords(existing.title)
    shared_title = title_keys_a & title_keys_b

    event_keys_a = event_signature_tokens(article)
    event_keys_b = event_signature_tokens(existing)
    shared_event = event_keys_a & event_keys_b

    title_ngram = character_ngram_similarity(article.title, existing.title)
    full_ngram = article_ngram_similarity(article, existing)

    title_seq = SequenceMatcher(
        None,
        semantic_normalized(article.title),
        semantic_normalized(existing.title),
    ).ratio()

    concepts_a = event_concepts(article_event_text(article))
    concepts_b = event_concepts(article_event_text(existing))
    shared_concepts = concepts_a & concepts_b

    shared_entity = any(x.startswith("entity:") for x in shared_concepts)
    shared_action = any(x.startswith("action:") for x in shared_concepts)
    shared_subject = any(
        x.startswith(("target:", "object:", "topic:", "location:"))
        for x in shared_concepts
    )

    # 1) 사건 구조가 명확히 동일: 주체/행위/대상 중 3축 이상
    if len(shared_concepts) >= 3 and (
        (shared_action and shared_subject)
        or (shared_entity and shared_subject)
    ):
        return True

    # 1-1) 동일 회사 + 동일 지역 + 동일 프로젝트/목적물이 명확하면
    # 'EPC 낙찰자로 선정' / '짓는다'처럼 행위 표현이 달라도 같은 사건으로 처리
    shared_location = any(x.startswith("location:") for x in shared_concepts)
    shared_object = any(x.startswith("object:") for x in shared_concepts)
    if shared_entity and shared_location and shared_object:
        return True

    # 2) 제목 핵심어 3개만 같다고 바로 중복으로 처리하지 않습니다.
    # 같은 프로젝트의 서로 다른 후속 기사까지 삭제되는 것을 방지하기 위해
    # 제목 유사도 또는 사건구조 일치가 함께 있어야 합니다.
    if len(shared_title) >= 3 and (
        title_ngram >= 0.30
        or title_seq >= 0.52
        or (shared_action and shared_subject)
    ):
        return True

    # 3) 제목 공통어는 적어도, 설명까지 보면 같은 사건 핵심어가 충분히 겹침
    if len(shared_event) >= 4 and (full_ngram >= 0.34 or title_ngram >= 0.25):
        return True

    # 4) 제목 핵심어 2개 + 제목/설명 유사도가 함께 읽음되는 경우
    if len(shared_title) >= 2 and (
        title_ngram >= 0.42
        or title_seq >= 0.56
        or full_ngram >= 0.48
    ):
        return True

    # 5) 제목은 크게 바뀌었지만 설명문까지 합치면 동일성이 매우 높은 경우
    if len(shared_event) >= 3 and full_ngram >= 0.58:
        return True

    return False


def same_day_duplicate(article: Article, existing: Article) -> bool:
    """
    KST 기준 같은 날짜에 올라온 기사 중 핵심 내용이 같은 경우
    언론사·언어·제목 표현이 달라도 동일 기사로 처리합니다.

    판정 기준
    1) 의미 있는 핵심 단어가 2개 이상 일치
    2) 제목 문자 유사도가 높음
    3) 동일 주체가 읽음되고 핵심 단어 1개 이상 + 제목 유사도가 일정 수준 이상
    """
    article_day = article.published.astimezone(KST).date()
    existing_day = existing.published.astimezone(KST).date()
    if article_day != existing_day:
        return False

    shared_keywords = (
        meaningful_keywords(article.title)
        & meaningful_keywords(existing.title)
    )

    ngram_score = character_ngram_similarity(
        article.title,
        existing.title,
    )
    sequence_score = SequenceMatcher(
        None,
        semantic_normalized(article.title),
        semantic_normalized(existing.title),
    ).ratio()

    # 기사 누락을 줄이기 위해 단순 공통 키워드 2개만으로는 중복 처리하지 않습니다.
    # 핵심 단어가 3개 이상 같거나, 2개가 같으면서 제목 유사도도 읽음될 때만 중복 처리합니다.
    if (
        len(shared_keywords) >= 3
        and (ngram_score >= 0.34 or sequence_score >= 0.54)
    ):
        return True
    if (
        len(shared_keywords) >= 2
        and (ngram_score >= 0.50 or sequence_score >= 0.66)
    ):
        return True

    # 제목 표현만 조금 바뀐 동일 기사
    if ngram_score >= 0.66 or sequence_score >= 0.80:
        return True

    concepts_a = event_concepts(article.title)
    concepts_b = event_concepts(existing.title)
    shared_concepts = concepts_a & concepts_b
    shared_entity = any(
        concept.startswith("entity:")
        for concept in shared_concepts
    )

    # 같은 주체의 같은 사건을 제목만 다르게 쓴 경우
    if (
        shared_entity
        and len(shared_keywords) >= 1
        and (ngram_score >= 0.46 or sequence_score >= 0.62)
    ):
        return True

    return False


def is_duplicate(article: Article, selected: list[Article]) -> bool:
    for existing in selected:
        time_gap = abs(
            (article.published - existing.published).total_seconds()
        )
        score = semantic_duplicate_score(article.title, existing.title)

        # 같은 날 핵심 내용이 반복되면
        # 언론사가 달라도 동일 기사로 보고 최신 기사 1건만 유지
        if same_day_duplicate(article, existing):
            return True

        # 특정 사례 하드코딩이 아니라 제목+미리보기+사건개념을 함께 비교해
        # 같은 날의 동일 사건을 일반적으로 중복 제거합니다.
        if same_event_general(article, existing):
            return True

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
        if time_gap <= 72 * 60 * 60 and score >= 0.72:
            common_keywords = (
                keyword_set(article.title)
                & keyword_set(existing.title)
            )
            if len(common_keywords) >= 4:
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


HYUNDAI_EC_TERMS = {
    "현대건설", "hyundai e&c", "hyundai e c",
    "hyundai engineering & construction",
    "hyundai engineering and construction", "hdec",
}

OTHER_CONSTRUCTION_TERMS = {
    "삼성물산", "samsung c&t",
    "대우건설", "daewoo e&c", "daewoo e c",
    "dl이앤씨", "dl e&c", "dl e c",
    "gs건설", "gs e&c", "gs e c",
    "sk에코플랜트", "sk ecoplant",
    "포스코이앤씨", "posco e&c", "posco e c",
    "롯데건설", "lotte e&c", "lotte e c",
    "현대엔지니어링", "hyundai engineering",
    "hdc현대산업개발", "hdc hyundai development",
    "한화 건설부문", "한화건설", "hanwha construction",
    "두산에너빌리티", "doosan enerbility",
}

# 타 건설사는 원전·원자력 관련 기사만 노출합니다.
# 현대건설은 당사이므로 이 제한을 적용하지 않습니다.
BLOCKED_CAMPAIGN_SLOGAN_TERMS = {
    "당당히 행동에 나섭시다",
    "함께 행동에 나섭시다",
}


def is_blocked_campaign_slogan(title: str, summary: str = "") -> bool:
    """
    캠페인·구호성 문구가 '기사 제목 자체'에 들어간 경우만 제외합니다.
    본문/요약에 인용된 경우까지 제외하면 정상 원전 기사가 과도하게 빠질 수 있으므로
    summary는 판정에 사용하지 않습니다.
    """
    title_text = html.unescape(title).lower()
    return any(term in title_text for term in BLOCKED_CAMPAIGN_SLOGAN_TERMS)


GLOBAL_NUCLEAR_WEAPONS_STRONG_TERMS = {
    # 명백한 핵무기/폭탄/군사 핵 이슈
    "원자폭탄", "원폭", "핵폭탄", "핵무기", "핵탄두", "핵실험",
    "핵공격", "핵전쟁", "핵억제", "핵보복", "핵미사일",
    "atomic bomb", "nuclear bomb", "nuclear weapon", "nuclear weapons",
    "nuclear warhead", "nuclear warheads",
    "nuclear test", "nuclear strike", "nuclear deterrence",
    "nuclear retaliation", "nuclear warfare", "nuclear missile",
}

GLOBAL_NUCLEAR_WEAPONS_TITLE_ONLY_TERMS = {
    # 기사 요약에서 배경 설명으로 잠깐 등장할 수 있어 제목에 있을 때만 제외
    "핵 잠수함", "핵잠수함", "원자력 잠수함", "원자력잠수함",
    "ballistic missile", "icbm", "slbm", "nuclear submarine",
}

OTHER_CONSTRUCTION_NUCLEAR_TERMS = {
    "원전", "원자력", "원자로", "핵발전",
    "원전 건설", "원전건설", "원전 사업", "원전사업",
    "원전 수주", "원전수주", "원전 시공", "원전시공",
    "원전 epc", "원전epc",
    "원전 해체", "원전해체", "원자력 해체", "원자력해체",
    "smr", "소형모듈원자로", "소형 모듈 원자로",
    "차세대원자로", "차세대 원자로",
    "nuclear", "reactor", "nuclear power", "nuclear energy",
    "nuclear power plant", "nuclear project", "nuclear construction",
    "nuclear new build", "new nuclear build",
    "nuclear epc", "nuclear decommissioning",
    "small modular reactor", "advanced reactor",
    "ap1000", "ap300",
}


def is_excluded_military_nuclear_article(title: str, summary: str = "") -> bool:
    """
    군사·무기성 핵 이슈만 제외합니다.
    - 명백한 핵무기 용어는 제목/요약에서 확인
    - 잠수함·미사일류처럼 정상 원전 기사 배경 설명에 섞일 수 있는 용어는 제목에 있을 때만 제외
    """
    title_text = html.unescape(title).lower()
    full_text = html.unescape(f"{title} {summary}").lower()

    if any(term in full_text for term in GLOBAL_NUCLEAR_WEAPONS_STRONG_TERMS):
        return True
    if any(term in title_text for term in GLOBAL_NUCLEAR_WEAPONS_TITLE_ONLY_TERMS):
        return True
    return False


def is_other_construction_nuclear_article(title: str, summary: str = "") -> bool:
    """
    타 건설사 기사에만 적용하는 원전·원자력 관련성 필터입니다.
    일반 주택/토목/철도/도로/화력/가스발전 등은 제외하고,
    원전·원자력·SMR·원전해체·원전 EPC 관련 기사만 남깁니다.
    """
    haystack = html.unescape(f"{title} {summary}").lower()
    return any(term in haystack for term in OTHER_CONSTRUCTION_NUCLEAR_TERMS)



SHIN_HANUL_TERMS = {
    "신한울 원전", "신한울원전",
    "신한울 1호기", "신한울 2호기",
    "신한울 3호기", "신한울 4호기",
    "신한울 1·2호기", "신한울 3·4호기",
    "shin hanul nuclear", "shin hanul npp",
}

HOLTEC_TERMS = {
    "holtec", "holtec international", "홀텍", "smr-300", "smr 300",
    "palisades smr", "palisades nuclear", "oyster creek smr",
}

TERRAPOWER_TERMS = {
    "terrapower", "테라파워", "natrium reactor", "natrium nuclear",
    "kemmerer nuclear", "케머러 원전",
}

NUCLEAR_US_INVESTMENT_NUCLEAR_TERMS = {
    "원전", "원자력", "원자로", "smr",
    "nuclear", "reactor", "ap1000",
}

NUCLEAR_US_INVESTMENT_TERMS = {
    "대미투자", "대미 투자", "대미투자펀드", "대미 투자 펀드",
    "미국 투자", "한미 투자", "대미 투자금", "대미 투자액",
    "u.s. investment", "us investment", "korea-us investment",
    "korea u.s. investment", "investment fund", "투자 펀드",
}

FERMI_AMERICA_TERMS = {
    "fermi america",
    "페르미 아메리카", "페르미아메리카", "페르미",
    "퍼미 아메리카", "퍼미아메리카", "퍼미",
    "project matador", "프로젝트 마타도르",
    "hypergrid", "하이퍼그리드",
    "amarillo nuclear", "애머릴로 원전",
    "carson county nuclear", "카슨 카운티 원전",
}


def classify_priority_company_group(group: str, title: str, summary: str) -> str:
    """
    원전 대미투자, Holtec, TerraPower 및 Fermi America 관련 기사는 검색된 원래 항목과 관계없이
    각각의 전용 항목으로 분류합니다.
    """
    haystack = html.unescape(f"{title} {summary}").lower()

    has_nuclear_term = any(
        term in haystack for term in NUCLEAR_US_INVESTMENT_NUCLEAR_TERMS
    )
    has_us_investment_term = any(
        term in haystack for term in NUCLEAR_US_INVESTMENT_TERMS
    )
    if has_nuclear_term and has_us_investment_term:
        return "원전 대미투자"

    if any(term in haystack for term in SHIN_HANUL_TERMS):
        return "원자력"

    if any(term in haystack for term in HOLTEC_TERMS):
        return "Holtec"

    if any(term in haystack for term in TERRAPOWER_TERMS):
        return "TerraPower"

    if any(term in haystack for term in FERMI_AMERICA_TERMS):
        return "Fermi America"

    return group


def classify_construction_group(group: str, title: str, summary: str) -> str | None:
    """
    건설사 분류 기준:
    - 현대건설: 당사 관련 기사이므로 원전 여부와 관계없이 현대건설 항목에 유지
    - 타 건설사: 원전·원자력·SMR·원전해체·원전 EPC 관련 기사만 유지
    """
    if group != "타 건설사":
        return group

    haystack = html.unescape(f"{title} {summary}").lower()

    # 타 건설사 검색 결과 안에 현대건설이 함께 잡힌 경우에는
    # 당사 기사로 재분류하며 원자력 관련성 제한을 적용하지 않습니다.
    if any(term in haystack for term in HYUNDAI_EC_TERMS):
        return "현대건설"

    if any(term in haystack for term in OTHER_CONSTRUCTION_TERMS):
        if is_other_construction_nuclear_article(title, summary):
            return "타 건설사"
        return None

    return None


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

    summary = (
        getattr(entry, "summary", "")
        or getattr(entry, "description", "")
        or ""
    )

    if is_blocked_campaign_slogan(title, summary):
        return None

    priority_group = classify_priority_company_group(group, title, summary)
    classified_group = classify_construction_group(priority_group, title, summary)
    if classified_group is None:
        return None

    if classified_group == "현대건설" and is_hyundai_volleyball_article(title, summary):
        return None

    description = clean_description(summary, title, publisher)

    return Article(
        title=title,
        link=link,
        published=published,
        language=language,
        group=classified_group,
        publisher=publisher,
        image=extract_image(entry),
        source_url=source_url,
        description=description,
    )


GOVERNMENT_MINISTRY_TERMS = {
    "산업통상부", "산업통상자원부", "산업부",
    "기후에너지환경부", "기후부",
    "과학기술정보통신부", "과기정통부", "과기부",
    "ministry of trade, industry and energy",
    "ministry of climate, energy and environment",
    "ministry of science and ict",
}

GOVERNMENT_SENIOR_RANK_TERMS = {
    "장관", "차관", "1차관", "2차관", "제1차관", "제2차관",
    "부총리", "대통령", "국무총리", "통상교섭본부장",
    "산업자원안보실장", "무역투자실장", "원전전략기획관",
    "minister", "vice minister", "deputy prime minister",
    "minister for trade", "trade minister",
    "president", "prime minister",
}


GOVERNMENT_TRACKED_PEOPLE = {
    "김정관", "문신학", "양기욱", "여한구", "강감찬", "김창희",
}

PERSONNEL_NEWS_TERMS = {
    "인사", "인사발령", "임명", "선임", "취임", "승진", "전보",
    "보직", "부임", "내정", "연임", "사장 선임", "대표 선임",
    "appointment", "appointed", "personnel", "executive appointment",
    "promotion", "named as", "takes office", "inauguration",
}


def is_government_senior_article(article: Article) -> bool:
    """
    원전 관계부처 그룹에는 다음 기사를 포함합니다.
    1) 장관·차관급 이상 인사 관련 기사
    2) 관계부처의 인사발령·임명·취임·승진 등 인사 기사
    """
    title = normalized(article.title)
    has_ministry = any(term in title for term in GOVERNMENT_MINISTRY_TERMS)
    has_senior_rank = any(term in title for term in GOVERNMENT_SENIOR_RANK_TERMS)
    has_personnel_news = any(term in title for term in PERSONNEL_NEWS_TERMS)
    has_tracked_person = any(term in title for term in GOVERNMENT_TRACKED_PEOPLE)

    return (
        has_tracked_person
        or (has_ministry and (has_senior_rank or has_personnel_news))
    )



def classify_direct_article(title: str, summary: str) -> str | None:
    """언론사 직접 수집 기사를 기존 웹페이지 그룹 중 하나로 분류합니다."""
    haystack = html.unescape(f"{title} {summary}").lower()

    if is_blocked_campaign_slogan(title, summary):
        return None
    if is_excluded_military_nuclear_article(title, summary):
        return None

    priority_group = classify_priority_company_group("원자력", title, summary)
    if priority_group != "원자력":
        return priority_group

    for group in DIRECT_GROUP_PRIORITY:
        terms = DIRECT_GROUP_KEYWORDS.get(group, [])
        if any(term.lower() in haystack for term in terms):
            if group == "원전 관계부처":
                temp = Article(
                    title=title, link="", published=datetime.now(KST),
                    language="ko", group=group, publisher="", image="", source_url="",
                )
                if not is_government_senior_article(temp):
                    continue
            if group == "타 건설사":
                classified = classify_construction_group(group, title, summary)
                if classified is None:
                    continue
                return classified
            return group

    # 기존 그룹 키워드에 딱 맞지 않아도 민수 원전/원자력 관련성이 있으면 유지
    if is_civil_nuclear_relevant(title, summary):
        if re.search(r"[가-힣]", f"{title} {summary}"):
            return "원자력"
        return "Nuclear Power·Nuclear Energy"

    return None


def parse_direct_rss_entry(entry, publisher: str, feed_url: str) -> Article | None:
    """언론사 자체 RSS 항목을 Article로 변환합니다."""
    raw_date = (
        getattr(entry, "published", None)
        or getattr(entry, "updated", None)
        or getattr(entry, "created", None)
    )

    published = None
    if raw_date:
        try:
            parsed_date = date_parser.parse(raw_date)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            published = parsed_date.astimezone(KST)
        except Exception:
            published = None

    if published is None:
        parsed_struct = (
            getattr(entry, "published_parsed", None)
            or getattr(entry, "updated_parsed", None)
            or getattr(entry, "created_parsed", None)
        )
        if parsed_struct:
            try:
                published = datetime(
                    *parsed_struct[:6],
                    tzinfo=timezone.utc,
                ).astimezone(KST)
            except Exception:
                published = None

    if published is None:
        return None

    title = html.unescape(getattr(entry, "title", "") or "").strip()
    link = (getattr(entry, "link", "") or "").strip()
    summary = (
        getattr(entry, "summary", "")
        or getattr(entry, "description", "")
        or ""
    )

    if not title or not link:
        return None

    group = classify_direct_article(title, summary)
    if group is None:
        return None

    if group == "현대건설" and is_hyundai_volleyball_article(title, summary):
        return None

    description = clean_description(summary, title, publisher)

    return Article(
        title=title,
        link=link,
        published=published,
        language="en" if re.search(r"[A-Za-z]", title) and not re.search(r"[가-힣]", title) else "ko",
        group=group,
        publisher=publisher,
        image=extract_image(entry),
        source_url=feed_url,
        description=description,
    )


def _fetch_one_direct_rss_feed(
    publisher: str,
    feed_url: str,
    start: datetime,
    end: datetime,
) -> list[Article]:
    """언론사 자체 RSS 한 개를 timeout 내에서 읽고 필요한 기사만 반환합니다."""
    try:
        request = Request(
            feed_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; NuclearDailyBrief/1.0; "
                    "+https://github.com/newenergy870314-hub/nuclear-daily-brief)"
                )
            },
        )
        with urlopen(request, timeout=DIRECT_RSS_TIMEOUT_SECONDS) as response:
            payload = response.read()

        feed = feedparser.parse(payload)
    except Exception:
        # 개별 언론사 RSS 하나가 실패해도 나머지 직접 수집은 계속 진행합니다.
        return []

    articles: list[Article] = []
    for entry in getattr(feed, "entries", []):
        article = parse_direct_rss_entry(entry, publisher, feed_url)
        if not article:
            continue
        if not (start <= article.published < end):
            continue
        articles.append(article)

    if articles:
        print(f"[RSS] {publisher}: {len(articles)} article(s)")
    print(f"[SOURCE RSS] {publisher} | accepted={len(articles)} | url={feed_url}")
    return articles


def fetch_direct_rss(start: datetime, end: datetime) -> list[Article]:
    """
    국내외 언론사 자체 RSS에서 직접 기사를 수집합니다.

    성능 원칙:
    - 검색 포털 RSS를 사용하지 않습니다.
    - 각 언론사 자체 RSS를 병렬 조회합니다.
    - 피드별 timeout을 두어 느린 언론사 때문에 전체 작업이 지연되지 않게 합니다.
    """
    if not DIRECT_RSS_FEEDS:
        return []

    fetched: list[Article] = []
    workers = min(DIRECT_RSS_WORKERS, len(DIRECT_RSS_FEEDS))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _fetch_one_direct_rss_feed,
                publisher,
                feed_url,
                start,
                end,
            )
            for publisher, feed_url in DIRECT_RSS_FEEDS
        ]

        for future in as_completed(futures):
            try:
                fetched.extend(future.result())
            except Exception:
                # 개별 언론사 피드 오류는 전체 수집 실패로 이어지지 않게 합니다.
                continue

    return fetched



class _DirectLinkParser(HTMLParser):
    """언론사 뉴스 목록 페이지에서 기사 후보 링크와 화면 제목을 수집합니다."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        attrs_map = {str(k).lower(): str(v) for k, v in attrs if k and v is not None}
        href = attrs_map.get("href", "").strip()
        if href:
            self._href = urljoin(self.base_url, href)
            self._chunks = []

    def handle_data(self, data: str):
        if self._href and data.strip():
            self._chunks.append(data.strip())

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._href:
            title = re.sub(r"\s+", " ", " ".join(self._chunks)).strip()
            if title:
                self.links.append((self._href, title))
            self._href = ""
            self._chunks = []


def _looks_like_article_url(url: str, publisher: str) -> bool:
    lower = url.lower()
    blocked = (
        "/login", "/search", "/tag/", "/author/", "/category/", "/privacy", "/terms",
        "/subscribe", "/membership", "/event/", "/photo/", "/video/", "/sports/", "/entertainment/",
        "javascript:", "mailto:", "#",
    )
    if any(token in lower for token in blocked):
        return False

    if publisher == "연합뉴스":
        return "yna.co.kr/view/" in lower
        return "ans.org/news/article-" in lower or "/news/article-" in lower
    if publisher == "Nuclear Engineering International":
        return "neimagazine.com/news/" in lower and lower.rstrip("/") != "https://www.neimagazine.com/news"
    if publisher == "NucNet":
        return "nucnet.org/news/" in lower
    return True


def _jsonld_date_published(decoded_html: str) -> str:
    for match in re.finditer(
        r'<script[^>]+type=["\\\']application/ld\\+json["\\\'][^>]*>(.*?)</script>',
        decoded_html,
        re.I | re.S,
    ):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        queue = payload if isinstance(payload, list) else [payload]
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            value = (
                item.get("datePublished")
                or item.get("dateCreated")
                or item.get("dateModified")
                or item.get("uploadDate")
            )
            if value:
                return str(value)
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(x for x in graph if isinstance(x, dict))
    return ""


def _extract_direct_article_date(
    parser,
    decoded_html: str,
    final_url: str,
    language: str,
) -> datetime | None:
    """
    해외 기사에서 자주 쓰는 meta / JSON-LD / <time datetime> / URL 날짜를 순차 확인합니다.
    발행일 형식 차이 때문에 정상 영문 기사가 통째로 빠지는 문제를 줄입니다.
    """
    raw_candidates = [
        parser.values.get("article:published_time"),
        parser.values.get("article:published"),
        parser.values.get("og:published_time"),
        parser.values.get("date"),
        parser.values.get("datepublished"),
        parser.values.get("datePublished"),
        parser.values.get("pubdate"),
        parser.values.get("publishdate"),
        parser.values.get("publish-date"),
        parser.values.get("dc.date"),
        parser.values.get("dc.date.issued"),
        _jsonld_date_published(decoded_html),
    ]

    time_matches = re.findall(
        r'<time[^>]+datetime=["\\\']([^"\\\']+)["\\\']',
        decoded_html,
        re.I,
    )
    raw_candidates.extend(time_matches[:4])

    # common inline JSON / data attributes
    inline_patterns = [
        r'["\\\']datePublished["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
        r'["\\\']published[_-]?time["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
        r'data-(?:publish|published)-date=["\\\']([^"\\\']+)["\\\']',
    ]
    for pattern in inline_patterns:
        m = re.search(pattern, decoded_html, re.I)
        if m:
            raw_candidates.append(m.group(1))

    # URL에 YYYY/MM/DD 또는 YYYY-MM-DD가 들어가는 언론사 보완
    url_match = re.search(r'/((?:20)\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|$)', final_url)
    if url_match:
        raw_candidates.append("-".join(url_match.groups()))

    # 최후 보완: HTML에서 ISO 날짜 1건
    m = re.search(
        r'20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?',
        decoded_html,
    )
    if m:
        raw_candidates.append(m.group(0))

    for raw_date in raw_candidates:
        if not raw_date:
            continue
        try:
            published = date_parser.parse(str(raw_date))
            if published.tzinfo is None:
                # 영문 기사에 timezone이 없으면 UTC로 간주하되,
                # 날짜만 있는 경우 정오를 사용해 경계시간 오분류를 줄입니다.
                if (
                    published.hour == 0
                    and published.minute == 0
                    and not re.search(r'\d{1,2}:\d{2}', str(raw_date))
                ):
                    published = published.replace(hour=12)
                published = published.replace(
                    tzinfo=KST if language == "ko" else timezone.utc
                )
            return published.astimezone(KST)
        except Exception:
            continue

    return None


def _fetch_direct_page_article(
    publisher: str,
    link: str,
    title_hint: str,
    language: str,
    source_url: str,
) -> Article | None:
    """
    언론사 원문을 직접 열어 제목+description을 확보한 뒤 분류합니다.
    과거에는 제목만으로 먼저 탈락시켜 영문 원전 기사가 누락될 수 있었습니다.
    """
    try:
        request = Request(
            link,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,ko-KR;q=0.7,ko;q=0.6" if language == "en"
                else "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        with urlopen(request, timeout=DIRECT_PAGE_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            payload = response.read(ARTICLE_META_MAX_BYTES)
    except Exception:
        return None

    decoded = payload.decode("utf-8", errors="ignore")
    parser = _MetaTagParser(final_url)
    try:
        parser.feed(decoded)
    except Exception:
        pass

    title = (
        parser.values.get("og:title")
        or parser.values.get("twitter:title")
        or title_hint
        or ""
    ).strip()
    title = re.sub(r"\s+", " ", html.unescape(title)).strip()
    if not title:
        return None

    description_raw = (
        parser.values.get("og:description")
        or parser.values.get("twitter:description")
        or parser.values.get("description")
        or ""
    )
    description = clean_description(description_raw, title, publisher)
    if not description:
        description = _best_paragraph_description(parser, title, publisher)

    # 제목만이 아니라 description까지 읽고 최종 분류
    group = classify_direct_article(title, description)
    if group is None:
        return None
    if group == "현대건설" and is_hyundai_volleyball_article(title, description):
        return None

    published = _extract_direct_article_date(
        parser,
        decoded,
        final_url,
        language,
    )
    if published is None:
        return None

    jsonld_images = _extract_jsonld_image_candidates(decoded, final_url)
    image = (
        parser.values.get("og:image")
        or parser.values.get("twitter:image")
        or parser.values.get("twitter:image:src")
        or parser.values.get("image")
        or _best_jsonld_image_candidate(jsonld_images)
        or _best_html_image_candidate(parser)
        or ""
    ).strip()
    if image:
        image = urljoin(final_url, image)

    return Article(
        title=title,
        link=final_url,
        published=published,
        language=language,
        group=group,
        publisher=publisher,
        image=image,
        source_url=source_url,
        description=description,
    )


def _fetch_one_direct_news_page(
    publisher: str,
    page_url: str,
    language: str,
    start: datetime,
    end: datetime,
) -> list[Article]:
    try:
        request = Request(
            page_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NuclearDailyBrief/3.0)",
                "Accept-Language": "en-US,en;q=0.9" if language == "en" else "ko-KR,ko;q=0.9,en;q=0.7",
            },
        )
        with urlopen(request, timeout=DIRECT_PAGE_TIMEOUT_SECONDS) as response:
            payload = response.read(2_500_000)
            final_url = response.geturl()
    except Exception as exc:
        print(f"[SOURCE PAGE] {publisher} | fetch_error={type(exc).__name__} | url={page_url}")
        return []

    decoded = payload.decode("utf-8", errors="ignore")
    parser = _DirectLinkParser(final_url)
    try:
        parser.feed(decoded)
    except Exception as exc:
        print(f"[SOURCE PAGE] {publisher} | parse_error={type(exc).__name__} | url={page_url}")
        return []

    candidates: list[tuple[str, str]] = []
    seen = set()
    is_nuclear_specialist = publisher in NUCLEAR_SPECIALIST_PUBLISHERS
    is_english_energy_page = language == "en" and _english_energy_page(page_url)
    blind_energy_candidates = 0

    for link, title in parser.links:
        if link in seen or not _looks_like_article_url(link, publisher):
            continue
        seen.add(link)

        # 메뉴/버튼 수준의 너무 짧은 텍스트만 제거.
        # 원자력 전문매체는 짧은 제목도 실제 기사일 수 있어 기준을 더 느슨하게 둡니다.
        min_title_len = 4 if is_nuclear_specialist else 8
        if len(title.strip()) < min_title_len:
            continue

        title_lower = html.unescape(title).lower()

        if is_nuclear_specialist:
            # 전문매체는 기사 URL이면 원문까지 확인하여 제목 선필터 누락을 방지
            candidate_ok = True
        elif language == "en":
            candidate_ok = (
                classify_direct_article(title, "") is not None
                or any(term in title_lower for term in ENGLISH_NUCLEAR_CANDIDATE_TERMS)
                or is_priority_nuclear_market_candidate(title, "")
            )
            if (
                not candidate_ok
                and is_english_energy_page
                and blind_energy_candidates < 160
                and len(title.strip()) >= 14
                and _looks_like_article_candidate_url(link)
            ):
                candidate_ok = True
                blind_energy_candidates += 1
        else:
            candidate_ok = classify_direct_article(title, "") is not None

        if not candidate_ok:
            continue

        candidates.append((link, title))

        # 전문 원자력 매체는 더 많은 최신 링크를 확인
        page_limit = 400 if is_nuclear_specialist else DIRECT_PAGE_MAX_LINKS
        if len(candidates) >= page_limit:
            break

    if not candidates:
        print(f"[SOURCE PAGE] {publisher} | links={len(parser.links)} | candidates=0 | accepted=0 | url={page_url}")
        return []

    articles: list[Article] = []
    opened = 0
    article_errors = 0
    workers = min(DIRECT_PAGE_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _fetch_direct_page_article,
                publisher,
                link,
                title,
                language,
                page_url,
            )
            for link, title in candidates
        ]
        for future in as_completed(futures):
            try:
                article = future.result()
                opened += 1
            except Exception:
                article_errors += 1
                continue
            if article and start <= article.published < end:
                articles.append(article)

    print(
        f"[SOURCE PAGE] {publisher} | links={len(parser.links)} | candidates={len(candidates)} "
        f"| opened={opened} | errors={article_errors} | accepted={len(articles)} | url={page_url}"
    )
    return articles


def fetch_direct_news_pages(start: datetime, end: datetime) -> list[Article]:
    """RSS가 없는 국내외 언론사의 공식 뉴스 목록 페이지를 병렬로 직접 수집합니다."""
    if not DIRECT_NEWS_PAGES:
        return []
    fetched: list[Article] = []
    workers = min(DIRECT_PAGE_WORKERS, len(DIRECT_NEWS_PAGES))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_fetch_one_direct_news_page, publisher, page_url, language, start, end)
            for publisher, page_url, language in DIRECT_NEWS_PAGES
        ]
        for future in as_completed(futures):
            try:
                fetched.extend(future.result())
            except Exception:
                continue
    return fetched

def fetch_articles(start: datetime, end: datetime) -> list[Article]:
    """
    국내외 언론사에서 직접 기사를 수집합니다.

    수집 구조:
    1) 언론사 자체 RSS 직접 수집
    2) RSS가 없거나 부족한 언론사는 공식 뉴스 페이지 직접 수집
    3) 기존 분류·중복 제거·미리보기·썸네일 보완 로직 적용

    검색 포털 RSS는 사용하지 않습니다.
    """
    rss_articles = fetch_direct_rss(start, end)
    page_articles = fetch_direct_news_pages(start, end)
    fetched = rss_articles + page_articles

    ko_count = sum(1 for article in fetched if article.language == "ko")
    en_count = sum(1 for article in fetched if article.language == "en")
    overseas_publishers = sorted({article.publisher for article in fetched if article.language == "en"})
    print(
        f"[COLLECT] raw={len(fetched)} / ko={ko_count} / en={en_count} "
        f"/ rss={len(rss_articles)} / pages={len(page_articles)} / RAW_REVIEW_MODE=ON"
    )
    print(
        f"[OVERSEAS COVERAGE] active_publishers={len(overseas_publishers)} / "
        f"publishers={', '.join(overseas_publishers) if overseas_publishers else '-'}"
    )
    return fetched

def select_articles_for_period(
    fetched: list[Article],
    start: datetime,
    end: datetime,
) -> list[Article]:
    """
    검토용 원본 모드:
    - 기간에 해당하는 기사를 모두 유지
    - 그룹별/언어별 기사 수 제한 없음
    - 중복 기사 제거 없음
    먼저 실제 수집량을 확인한 뒤 필터 기준을 다시 설계합니다.
    """
    period_articles = [
        article
        for article in fetched
        if start <= article.published < end
        and article.publisher not in EXCLUDED_PUBLISHERS
    ]

    all_selected: list[Article] = []

    for group, _queries in GROUPS:
        for language in ("ko", "en"):
            found = [
                article
                for article in period_articles
                if article.group == group and article.language == language
            ]
            found.sort(key=lambda article: -article.published.timestamp())
            all_selected.extend(found)

    selected_ko = sum(1 for article in all_selected if article.language == "ko")
    selected_en = sum(1 for article in all_selected if article.language == "en")
    country_other = sum(
        1 for article in all_selected
        if detect_article_country(article) == "OTHER"
    )
    priority_codes = {"US", "GB", "FI", "BG", "RO", "IN", "VN", "AE", "SA"}
    priority_market_count = sum(
        1 for article in all_selected
        if detect_article_country(article) in priority_codes
    )

    print(
        f"[SELECT RAW] final={len(all_selected)} / ko={selected_ko} / en={selected_en} "
        f"/ country_other={country_other} / priority_markets={priority_market_count} "
        f"/ dedup=ON / limit=OFF"
    )
    return final_deduplicate_articles(all_selected)


def collect(start: datetime, end: datetime) -> list[Article]:
    """
    단일 기간 수집용 호환 함수.
    과거 날짜 수동 Backfill에서 사용합니다.
    """
    fetched = fetch_articles(start, end)
    return select_articles_for_period(fetched, start, end)


def escape(text: str) -> str:
    return html.escape(text, quote=True)



COUNTRY_META = {
    "US": ("🇺🇸", "미국"),
    "KR": ("🇰🇷", "한국"),
    "GB": ("🇬🇧", "영국"),
    "BG": ("🇧🇬", "불가리아"),
    "UA": ("🇺🇦", "우크라이나"),
    "AE": ("🇦🇪", "UAE"),
    "VN": ("🇻🇳", "베트남"),
    "MY": ("🇲🇾", "말레이시아"),
    "TH": ("🇹🇭", "태국"),
    "SG": ("🇸🇬", "싱가포르"),
    "RO": ("🇷🇴", "루마니아"),
    "CZ": ("🇨🇿", "체코"),
    "PL": ("🇵🇱", "폴란드"),
    "SI": ("🇸🇮", "슬로베니아"),
    "SK": ("🇸🇰", "슬로바키아"),
    "FI": ("🇫🇮", "핀란드"),
    "JP": ("🇯🇵", "일본"),
    "CA": ("🇨🇦", "캐나다"),
    "FR": ("🇫🇷", "프랑스"),
    "SE": ("🇸🇪", "스웨덴"),
    "DK": ("🇩🇰", "덴마크"),
    "CN": ("🇨🇳", "중국"),
    "IN": ("🇮🇳", "인도"),
    "AU": ("🇦🇺", "호주"),
    "RU": ("🇷🇺", "러시아"),
    "TR": ("🇹🇷", "튀르키예"),
    "SA": ("🇸🇦", "사우디"),
    "ZA": ("🇿🇦", "남아공"),
    "NL": ("🇳🇱", "네덜란드"),
    "BE": ("🇧🇪", "벨기에"),
    "CH": ("🇨🇭", "스위스"),
    "OTHER": ("🌐", "기타"),
}

# 1순위: 실제 프로젝트·사업·부지 위치
COUNTRY_PROJECT_TERMS = {
    "US": (
        "palisades", "팰리세이즈", "fermi america", "project matador",
        "matador", "amarillo", "아마릴로", "texas", "텍사스",
        "michigan", "미시간",
    ),
    "KR": (
        "신한울", "새울", "고리", "월성", "한빛", "한울",
        "신고리", "울진", "울주",
    ),
    "GB": ("gbn", "great british nuclear", "sizewell", "hinkley point"),
    "BG": ("kozloduy", "코즐로두이", "belene", "벨레네"),
    "UA": ("khmelnytskyi", "흐멜니츠키", "rivnе", "리우네"),
    "AE": (
        "barakah", "바라카", "barakah nuclear power plant",
        "barakah nuclear energy plant", "braka", "براكة",
        "enec", "emirates nuclear energy corporation", "nawah energy",
    ),
    "VN": (
        "ninh thuan", "ninh thuận", "ninh-thuan",
        "닌투언", "닌투안", "닌 투언", "닌 투안",
        "ninh thuan 1", "ninh thuan 2",
        "ninh thuận 1", "ninh thuận 2",
        "ninh thuan nuclear power project",
        "ninh thuận nuclear power project",
        "vietnam nuclear power", "vietnamese nuclear power",
        "베트남 원전", "베트남 원자력발전",
    ),
    "RO": ("cernavoda", "체르나보다", "nuclearelectrica"),
    "CZ": ("dukovany", "두코바니", "temelin", "테멜린"),
    "PL": ("lubiatowo", "루비아토보", "pomerania", "포메라니아"),
    "SI": ("krško", "krsko", "크르슈코"),
    "FI": ("olkiluoto", "올킬루오토", "hanhikivi", "한히키비", "loviisa", "fennovoima", "tvo"),
    "JP": ("fukushima", "후쿠시마", "kashiwazaki", "가시와자키"),
    "CA": ("darlington", "달링턴", "ontario", "온타리오"),
    "FR": ("flamanville", "플라망빌"),
    "SE": ("ringhals", "링할스", "forsmark", "포스마르크"),
    "CN": ("taishan", "타이산", "sanmen", "산먼", "haiyang", "하이양", "xudapu", "쉬다푸"),
    "IN": ("kudankulam", "쿠단쿨람", "jaitapur", "자이타푸르", "kaiga", "카이가", "npcil"),
    "AU": ("australian nuclear", "호주 원전"),
    "RU": ("kursk ii", "쿠르스크", "leningrad ii", "레닌그라드", "rosatom project"),
    "TR": ("akkuyu", "아쿠유", "sinop nuclear", "시노프 원전"),
    "SA": (
        "saudi nuclear", "saudi nuclear power", "사우디 원전", "사우디 원자력",
        "king abdullah city for atomic and renewable energy",
        "k.a.care", "kacare", "ka-care",
        "saudi national atomic energy project",
        "saudi nuclear energy project",
    ),
    "ZA": ("koeberg", "쿠버그"),
    "NL": ("borssele", "보르셀"),
    "BE": ("doel", "두엘", "tihange", "티앙주"),
    "CH": ("beznau", "베츠나우", "gösgen", "goesgen", "괴스겐", "leibstadt", "라이프슈타트"),
    "SK": ("slovakia nuclear", "slovak nuclear", "슬로바키아 원전", "bohunice", "mochovce"),
    "DK": ("denmark nuclear", "danish nuclear", "덴마크 원전", "seaborg", "copenhagen atomics"),
    "MY": ("malaysia nuclear", "malaysian nuclear", "말레이시아 원전", "malaysian nuclear agency"),
    "TH": ("thailand nuclear", "thai nuclear", "태국 원전", "office of atoms for peace"),
    "SG": ("singapore nuclear", "singapore nuclear energy", "싱가포르 원전", "singapore nuclear"),
}

# 2순위: 정부·규제기관·정책이 발생한 국가
COUNTRY_GOVERNMENT_TERMS = {
    "US": (
        "nrc", "u.s. nuclear regulatory commission", "us nuclear regulatory commission",
        "department of energy", "u.s. doe", "us doe", "미 에너지부", "미 원자력규제위원회",
    ),
    "KR": (
        "산업통상", "산업부", "기후에너지환경부", "과학기술정보통신부",
        "과기정통부", "원자력안전위원회", "원안위", "대한민국 정부", "한국 정부",
    ),
    "GB": ("office for nuclear regulation", "onr", "영국 정부"),
    "AE": (
        "fanr", "federal authority for nuclear regulation",
        "uae nuclear regulator", "uae government",
        "아랍에미리트 정부", "uae 원자력규제",
    ),
    "VN": (
        "vietnam atomic energy agency", "vaea",
        "vietnam agency for radiation and nuclear safety", "varans",
        "vietnam ministry of industry and trade",
        "vietnam ministry of science and technology",
        "베트남 정부", "베트남 원자력청",
    ),
    "SA": (
        "k.a.care", "kacare",
        "king abdullah city for atomic and renewable energy",
        "saudi ministry of energy", "saudi government",
        "사우디 정부", "사우디 에너지부",
    ),
    "FR": ("asn", "프랑스 정부"),
    "CA": ("canadian nuclear safety commission", "cnsc", "캐나다 정부"),
    "JP": ("nuclear regulation authority", "일본 원자력규제위원회", "일본 정부"),
    "SK": ("slovak government", "slovak nuclear regulator", "újd sr", "ujd sr"),
    "DK": ("danish government", "danish energy agency"),
    "MY": ("malaysian government", "malaysian nuclear agency", "atom malaysia"),
    "TH": ("thai government", "office of atoms for peace", "oap thailand"),
    "SG": ("singapore government", "energy market authority singapore", "ema singapore"),
}

# 3순위: 국가 자체가 기사 사건·장소로 명시된 경우
COUNTRY_EXPLICIT_TERMS = {
    "US": ("미국", "미국원전", "미국원자력", "미국정부", "united states", "u.s.", "u.s.a.", "usa"),
    "KR": ("한국", "한국원전", "한국원자력", "한국정부", "대한민국", "south korea", "republic of korea", "korea"),
    "GB": ("영국", "영국원전", "영국원자력", "영국정부", "united kingdom", "great britain", "britain"),
    "BG": ("불가리아", "bulgaria"),
    "UA": ("우크라이나", "ukraine", "kyiv", "키이우"),
    "AE": (
        "아랍에미리트", "아랍 에미리트", "uae",
        "united arab emirates", "emirates", "emirati",
    ),
    "VN": (
        "베트남", "vietnam", "vietnamese",
        "ninh thuan", "ninh thuận", "닌투언", "닌투안",
    ),
    "RO": ("루마니아", "romania"),
    "CZ": ("체코", "czech republic", "czechia", "czech"),
    "PL": ("폴란드", "poland"),
    "SI": ("슬로베니아", "slovenia"),
    "FI": ("핀란드", "finland"),
    "JP": ("일본", "japan", "tokyo", "도쿄"),
    "CA": ("캐나다", "canada"),
    "FR": ("프랑스", "france"),
    "SE": ("스웨덴", "sweden"),
    "CN": ("중국", "china", "chinese"),
    "IN": ("인도", "india", "indian"),
    "AU": ("호주", "australia", "australian"),
    "RU": ("러시아", "russia", "russian"),
    "TR": ("튀르키예", "터키", "turkey", "türkiye", "turkiye", "turkish"),
    "SA": ("사우디", "사우디아라비아", "saudi arabia", "saudi"),
    "ZA": ("남아공", "남아프리카공화국", "south africa", "south african"),
    "NL": ("네덜란드", "netherlands", "dutch"),
    "BE": ("벨기에", "belgium", "belgian"),
    "CH": ("스위스", "switzerland", "swiss"),
    "SK": ("슬로바키아", "slovakia", "slovak"),
    "DK": ("덴마크", "denmark", "danish"),
    "MY": ("말레이시아", "malaysia", "malaysian"),
    "TH": ("태국", "thailand", "thai"),
    "SG": ("싱가포르", "singapore", "singaporean"),
}

# 4순위: 프로젝트/정책/장소가 불명확할 때만 기업·기관의 본국 사용
COUNTRY_HOME_ENTITY_TERMS = {
    "US": (
        "westinghouse", "웨스팅하우스", "holtec", "홀텍",
        "fermi america",
    ),
    "KR": (
        "현대건설", "hdec", "한국수력원자력", "한수원", "khnp",
        "한국전력", "한전", "kepco", "두산에너빌리티",
    ),
    "FR": ("edf", "framatome",),
    "RU": ("rosatom",),
    "CN": ("cnnc", "cgn", "china national nuclear corporation",),
    "IN": ("npcil", "nuclear power corporation of india",),
    "CA": ("candu energy", "atkinsréalis", "atkinsrealis",),
    "AE": (
        "emirates nuclear energy corporation", "enec",
        "nawah energy", "nawah energy company",
    ),
    "VN": (
        "vietnam electricity", "evn",
        "vietnam atomic energy institute", "vinatom",
    ),
    "SA": (
        "king abdullah city for atomic and renewable energy",
        "k.a.care", "kacare",
    ),
}


def _article_country_text(article: Article) -> tuple[str, str]:
    title = article.title.lower()
    full = " ".join(
        [article.title, article.description, article.publisher, article.group]
    ).lower()
    return title, full


def _contains_country_term(text: str, term: str) -> bool:
    """
    국가 키워드 오탐 방지.
    - 영문/숫자 용어는 영숫자 단어 내부 매치를 막습니다.
    - 짧은 한글 국가명(예: 영국, 한국, 미국, 인도)은 사람 이름/일반 단어 내부 매치를 막습니다.
      예: '박영국' -> 영국 아님.
    - 조사/띄어쓰기/문장부호 뒤는 허용합니다.
      예: '영국의 원전', '영국 정부' -> 영국.
    """
    if not text or not term:
        return False

    haystack = text.lower()
    needle = term.lower()

    # 한글이 들어간 키워드
    if re.search(r"[가-힣]", needle):
        # 단어 내부의 짧은 국가명 오탐을 특히 엄격하게 차단
        if len(needle) <= 3 and re.fullmatch(r"[가-힣]+", needle):
            # 앞쪽이 한글이면 사람 이름/복합명사 내부일 가능성이 높음
            pattern = rf"(?<![가-힣]){re.escape(needle)}"
            return re.search(pattern, haystack) is not None

        return needle in haystack

    # 영문/숫자 용어는 영숫자 단어 내부 매치 방지
    pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
    return re.search(pattern, haystack, flags=re.I) is not None


def _country_term_position(text: str, term: str) -> int:
    """_contains_country_term과 같은 규칙으로 첫 등장 위치를 반환합니다."""
    if not text or not term:
        return -1

    haystack = text.lower()
    needle = term.lower()

    if re.search(r"[가-힣]", needle):
        if len(needle) <= 3 and re.fullmatch(r"[가-힣]+", needle):
            m = re.search(rf"(?<![가-힣]){re.escape(needle)}", haystack)
            return m.start() if m else -1
        return haystack.find(needle)

    m = re.search(
        rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
        haystack,
        flags=re.I,
    )
    return m.start() if m else -1


def _country_term_count(text: str, term: str) -> int:
    """국가 키워드 등장 횟수를 오탐 방지 규칙으로 계산합니다."""
    if not text or not term:
        return 0

    haystack = text.lower()
    needle = term.lower()

    if re.search(r"[가-힣]", needle):
        if len(needle) <= 3 and re.fullmatch(r"[가-힣]+", needle):
            return len(re.findall(rf"(?<![가-힣]){re.escape(needle)}", haystack))
        return haystack.count(needle)

    return len(
        re.findall(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
            haystack,
            flags=re.I,
        )
    )


def _matching_country_codes(text: str, rules: dict[str, tuple[str, ...]]) -> list[str]:
    hits: list[str] = []
    for code, terms in rules.items():
        if any(_contains_country_term(text, term) for term in terms):
            hits.append(code)
    return hits


def _best_country_match(
    title: str,
    full: str,
    rules: dict[str, tuple[str, ...]],
) -> str | None:
    """
    같은 우선순위 안에서 여러 국가가 잡히면
    1) 제목에 직접 등장한 국가
    2) 제목에서 더 앞에 등장한 국가
    3) 전체 기사에서 등장 횟수가 많은 국가
    순으로 대표국가를 선택합니다.
    """
    candidates: list[tuple[int, int, int, str]] = []
    for order, (code, terms) in enumerate(rules.items()):
        title_positions = [
            pos
            for term in terms
            if (pos := _country_term_position(title, term)) >= 0
        ]
        title_pos = min(title_positions) if title_positions else 10**9
        full_count = sum(_country_term_count(full, term) for term in terms)
        if title_pos < 10**9 or full_count > 0:
            in_title_rank = 0 if title_pos < 10**9 else 1
            candidates.append((in_title_rank, title_pos, -full_count, code))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


def detect_related_countries(article: Article) -> list[str]:
    """
    기사에 실제로 관련된 국가를 모두 찾습니다.
    지도 집계에는 사용하지 않고 기사 카드의 보조 정보로만 사용합니다.
    """
    title, full = _article_country_text(article)
    found: list[str] = []

    for rules in (
        COUNTRY_PROJECT_TERMS,
        COUNTRY_GOVERNMENT_TERMS,
        COUNTRY_EXPLICIT_TERMS,
        COUNTRY_HOME_ENTITY_TERMS,
    ):
        for code in _matching_country_codes(full, rules):
            if code not in found:
                found.append(code)

    primary = detect_article_country(article)
    if primary != "OTHER":
        found = [primary] + [code for code in found if code != primary]
    return found


def detect_article_country(article: Article) -> str:
    """
    대표국가 1개만 반환합니다.
    지도 기사 수는 이 대표국가만 집계하므로 전체 기사 수와 국가별 합계가 일치합니다.

    우선순위:
    1) 프로젝트/사업 위치
    2) 정부·규제기관/정책 국가
    3) 사건·장소로 명시된 국가
    4) 기업/기관 본국
    5) 기타
    """
    title, full = _article_country_text(article)

    for rules in (
        COUNTRY_PROJECT_TERMS,
        COUNTRY_GOVERNMENT_TERMS,
        COUNTRY_EXPLICIT_TERMS,
        COUNTRY_HOME_ENTITY_TERMS,
    ):
        code = _best_country_match(title, full, rules)
        if code:
            return code

    # 영문 기사도 언어 때문에 '기타'로 보내지 않습니다.
    # 다만 국가/프로젝트를 실제로 특정할 근거가 없는 글로벌·국제기구 기사만 OTHER로 남깁니다.
    return "OTHER"



def render_card(article: Article, number: int, is_new: bool = False) -> str:
    ensure_article_display_metadata(article)

    if article.image:
        image_html = (
            f'<img src="{escape(article.image)}" alt="" '
            'loading="lazy" referrerpolicy="no-referrer">'
        )
    else:
        image_html = '<div class="no-image">NUCLEAR<br>NEWS</div>'

    new_badge = '<span class="new-badge">NEW</span>' if is_new else ''
    snippet_text = article.description.strip()
    snippet_html = (
        f'<div class="article-snippet">{escape(snippet_text)}</div>'
        if snippet_text
        else '<div class="article-snippet article-snippet-empty">미리보기 정보 없음</div>'
    )
    search_text = ' '.join(
        [article.title, article.publisher, article.group, article.description]
    ).lower()
    primary_country = detect_article_country(article)

    return f"""
<article class="preview-card{' new-article' if is_new else ''}"
  data-url="{escape(article.link)}"
  data-title="{escape(article.title)}"
  data-publisher="{escape(article.publisher)}"
  data-group="{escape(article.group)}"
  data-language="{escape(article.language)}"
  data-published="{article.published.timestamp():.0f}"
  data-country="{primary_country}"
  data-search="{escape(search_text)}"
  tabindex="0" role="link">
  <div class="preview-copy">
    <div class="article-order-column">
      <span class="article-order-inline">{number}.</span>
    </div>
    <div class="article-content-column">
      <div class="meta-row">
        <div class="publisher">{escape(article.publisher)}</div>
        <span class="meta-divider">·</span>
        <div class="status-inline">
          <span class="unread-label">안읽음</span>
          <span class="read-label">읽음</span>
          <span class="important-label">중요</span>
        </div>
      </div>
      <div class="headline">{new_badge}{escape(article.title)}</div>
      {snippet_html}
    </div>
    <button class="important-button" type="button" aria-label="중요 기사">★</button>
  </div>
  <div class="card-side">
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
    if not articles and group not in ALWAYS_SHOW_GROUPS:
        return ''
    new_urls = new_urls or set()
    korean_articles = order_similar_articles([a for a in articles if a.language == 'ko'])
    english_articles = order_similar_articles([a for a in articles if a.language == 'en'])
    ordered_articles = korean_articles + english_articles
    cards = ''.join(
        render_card(article, index, article.link in new_urls)
        for index, article in enumerate(ordered_articles, start=1)
    )
    if not cards:
        cards = '<div class="empty">해당 시간대에 수집된 기사가 없습니다.</div>'
    return f"""
<section class="news-group group-tab-section" data-group="{escape(group)}">
  <button class="group-title" type="button" aria-expanded="true">
    <span class="group-arrow">▲</span>
    <span class="group-name">{escape(group)}</span>
    <span class="group-count">{len(ordered_articles)}건</span>
  </button>
  <div class="article-stack">{cards}</div>
</section>
"""



# ============================================================
# FINAL DUPLICATE CONTROL
# 1) exact duplicate: same canonical URL OR same publisher + normalized title
# 2) same-event duplicate: different publishers but same core event
# ============================================================

DEDUP_ENABLED = True

_DEDUP_STOPWORDS = {
    # Korean news boilerplate / weak words
    "단독", "속보", "종합", "영상", "포토", "사진", "인터뷰", "기고", "사설",
    "관련", "대한", "위한", "통해", "이번", "올해", "지난", "오늘", "내일",
    "밝혀", "밝혔다", "전망", "예정", "추진", "진행", "관련해",
    # English news boilerplate / weak words
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at",
    "with", "from", "by", "as", "is", "are", "be", "will", "says", "said",
    "report", "reports", "news", "update",
}

_STRONG_EVENT_TERMS = (
    # Nuclear/project identifiers
    "ap1000", "ap300", "smr-300", "smr300", "natrium", "xe-100", "bwrx-300",
    "kozloduy", "cernavoda", "sizewell", "hinkley", "ninh thuan", "ninh thuận",
    "barakah", "palisades", "fermi", "matador", "vogtle", "dukovany",
    "olkiluoto", "loviisa", "khmelnytskyi", "rivne",
    # Korean site/project identifiers often shared across headlines
    "목동10단지", "목동 10단지", "울진", "한울원전", "한울 원전",
    "새울원전", "새울 원전", "신한울", "고리원전", "고리 원전",
)

_ACTION_EQUIVALENTS = {
    "수주": {"수주", "수주했다", "따냈다", "품으로", "선정", "시공사", "낙찰"},
    "입찰": {"입찰", "응찰", "단독응찰", "제안서", "bid", "tender"},
    "계약": {"계약", "체결", "서명", "contract", "agreement", "signed"},
    "승인": {"승인", "허가", "인가", "승인했다", "approved", "approval", "permit", "license", "licensing"},
    "착공": {"착공", "공사착수", "첫삽", "construction start", "groundbreaking"},
    "준공": {"준공", "완공", "상업운전", "commercial operation", "completed"},
    "지원": {"지원", "기부", "후원", "개선", "교체", "무상", "지원사업"},
    "협력": {"협력", "협약", "mou", "partnership", "cooperation", "협력체계"},
    "투자": {"투자", "출자", "funding", "investment", "financing"},
    "재가동": {"재가동", "restart", "reopen", "reopening"},
    "해체": {"해체", "decommission", "decommissioning"},
}

def _canonical_news_url(url: str) -> str:
    """Remove fragments and common tracking parameters for exact URL duplicate checks."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [
            (k, v) for k, v in query_pairs
            if k.lower() not in {
                "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "gclid", "fbclid", "ref", "source", "output", "oc",
            }
        ]
        path = re.sub(r"/+$", "", parsed.path or "")
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return urlunparse((
            (parsed.scheme or "https").lower(),
            host,
            path,
            "",
            urlencode(filtered, doseq=True),
            "",
        )).lower()
    except Exception:
        return url.strip().lower()

def _normalize_dedup_title(title: str) -> str:
    value = (title or "").lower()
    value = re.sub(r"\[[^\]]+\]|\([^)]+\)", " ", value)
    value = re.sub(r"[^0-9a-z가-힣]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def _dedup_tokens(title: str) -> set[str]:
    norm = _normalize_dedup_title(title)
    tokens = {
        tok for tok in norm.split()
        if len(tok) >= 2 and tok not in _DEDUP_STOPWORDS
    }
    return tokens

def _action_classes(text: str) -> set[str]:
    lower = (text or "").lower()
    result = set()
    for action, variants in _ACTION_EQUIVALENTS.items():
        if any(v.lower() in lower for v in variants):
            result.add(action)
    return result

def _strong_event_terms(text: str) -> set[str]:
    lower = (text or "").lower()
    return {term for term in _STRONG_EVENT_TERMS if term.lower() in lower}

def _publisher_key(article: Article) -> str:
    return re.sub(r"\s+", "", (article.publisher or "").lower())

def _same_exact_article(a: Article, b: Article) -> bool:
    # Same canonical article URL always wins.
    au = _canonical_news_url(a.link or a.source_url)
    bu = _canonical_news_url(b.link or b.source_url)
    if au and bu and au == bu:
        return True

    # Same publisher + exactly same normalized title.
    at = _normalize_dedup_title(a.title)
    bt = _normalize_dedup_title(b.title)
    return bool(
        at and bt
        and at == bt
        and _publisher_key(a) == _publisher_key(b)
    )

def _same_event_article(a: Article, b: Article) -> bool:
    """
    Conservative cross-publisher event clustering.
    We only merge when the evidence is strong enough that the stories describe
    the same underlying event, not merely the same broad topic.
    """
    if _same_exact_article(a, b):
        return True

    # Avoid collapsing separate follow-ups far apart in time.
    try:
        hours = abs((a.published - b.published).total_seconds()) / 3600
        if hours > 48:
            return False
    except Exception:
        pass

    atitle = _normalize_dedup_title(a.title)
    btitle = _normalize_dedup_title(b.title)
    if not atitle or not btitle:
        return False

    # Identical title across different media => same event.
    if atitle == btitle:
        return True

    atok = _dedup_tokens(a.title)
    btok = _dedup_tokens(b.title)
    if not atok or not btok:
        return False

    intersection = len(atok & btok)
    union = len(atok | btok)
    jaccard = intersection / union if union else 0.0
    seq = SequenceMatcher(None, atitle, btitle).ratio()

    astrong = _strong_event_terms(a.title + " " + (a.description or ""))
    bstrong = _strong_event_terms(b.title + " " + (b.description or ""))
    shared_strong = astrong & bstrong

    aactions = _action_classes(a.title + " " + (a.description or ""))
    bactions = _action_classes(b.title + " " + (b.description or ""))
    shared_action = bool(aactions & bactions)

    # Rule A: Very similar titles.
    if seq >= 0.86 and intersection >= 3:
        return True

    # Rule B: Strong project/site identifier + same action + reasonable title overlap.
    if shared_strong and shared_action and (jaccard >= 0.34 or intersection >= 3):
        return True

    # Rule C: Same action and high token overlap for rewrites of press releases.
    if shared_action and intersection >= 4 and jaccard >= 0.50:
        return True

    # Rule D: Extremely high token overlap even when action dictionary misses wording.
    if intersection >= 5 and jaccard >= 0.62:
        return True

    return False

def _article_rep_score(article: Article) -> tuple:
    """Choose the richest representative among duplicate/event-equivalent stories."""
    description_len = len((article.description or "").strip())
    image_bonus = 1 if (article.image_url or "").strip() else 0
    publisher_bonus = 1 if (article.publisher or "").strip() and article.publisher != "출처 미확인" else 0
    link_bonus = 1 if (article.link or "").strip() else 0
    # Richness first; for ties prefer earlier publication as likely original report.
    return (
        description_len,
        image_bonus,
        publisher_bonus,
        link_bonus,
        -int(article.published.timestamp()) if article.published else 0,
    )

def deduplicate_articles_final(articles: list[Article]) -> list[Article]:
    """
    Stage 1: exact duplicates
    Stage 2: same-event duplicates across outlets
    Returns only one representative card per duplicate/event cluster.
    """
    if not DEDUP_ENABLED:
        return sorted(articles, key=lambda x: x.published, reverse=True)

    sorted_articles = sorted(articles, key=lambda x: x.published, reverse=True)

    # Stage 1: exact duplicates
    exact_unique: list[Article] = []
    exact_removed = 0
    for article in sorted_articles:
        matched_idx = None
        for idx, kept in enumerate(exact_unique):
            if _same_exact_article(article, kept):
                matched_idx = idx
                break
        if matched_idx is None:
            exact_unique.append(article)
        else:
            exact_removed += 1
            if _article_rep_score(article) > _article_rep_score(exact_unique[matched_idx]):
                exact_unique[matched_idx] = article

    # Stage 2: same-event duplicates across publishers
    event_unique: list[Article] = []
    event_removed = 0
    for article in exact_unique:
        matched_idx = None
        for idx, kept in enumerate(event_unique):
            if _same_event_article(article, kept):
                matched_idx = idx
                break
        if matched_idx is None:
            event_unique.append(article)
        else:
            event_removed += 1
            if _article_rep_score(article) > _article_rep_score(event_unique[matched_idx]):
                event_unique[matched_idx] = article

    result = sorted(event_unique, key=lambda x: x.published, reverse=True)
    print(
        f"[DEDUP FINAL] input={len(articles)} / exact_removed={exact_removed} "
        f"/ same_event_removed={event_removed} / final={len(result)}"
    )
    return result


def final_deduplicate_articles(articles: list[Article]) -> list[Article]:
    return deduplicate_articles_final(articles)


def render_news_sections(articles: list[Article], new_urls: set[str] | None = None) -> str:
    articles = final_deduplicate_articles(articles)
    grouped: dict[str, list[Article]] = {name: [] for name, _ in GROUPS}
    for article in articles:
        grouped[article.group].append(article)

    sections = []
    for group, _ in GROUPS:
        if not grouped[group] and group not in ALWAYS_SHOW_GROUPS:
            continue
        section = render_group_unified(group, grouped[group], new_urls)
        section = section.replace(
            'class="news-group group-tab-section"',
            'class="news-group collapsed"',
            1,
        )
        section = section.replace('aria-expanded="true"', 'aria-expanded="false"', 1)
        section = section.replace('<span class="group-arrow">▲</span>', '<span class="group-arrow">▼</span>', 1)
        sections.append(section)

    return ''.join(sections)


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
        "description": article.description,
    }



def infer_publisher_from_url(*urls: str) -> str:
    """기사/원본 URL의 호스트를 설정된 매체 목록과 대조해 언론사명을 복원합니다."""
    configured_sources: list[tuple[str, str]] = []

    try:
        configured_sources.extend(
            (publisher, url)
            for publisher, url, _language in DIRECT_NEWS_PAGES
        )
    except Exception:
        pass

    try:
        configured_sources.extend(
            (publisher, url)
            for publisher, url in DIRECT_RSS_FEEDS
        )
    except Exception:
        pass

    host_to_publisher: dict[str, str] = {}
    for publisher, source in configured_sources:
        host = urlparse(source).netloc.lower().split(":", 1)[0]
        if host.startswith("www."):
            host = host[4:]
        if host and publisher:
            host_to_publisher.setdefault(host, publisher)

    for raw_url in urls:
        if not raw_url:
            continue
        try:
            host = urlparse(raw_url).netloc.lower().split(":", 1)[0]
        except Exception:
            continue
        if host.startswith("www."):
            host = host[4:]
        if not host:
            continue

        if host in host_to_publisher:
            return host_to_publisher[host]

        # rss.example.com / news.example.com 같은 서브도메인도 동일 매체로 인식
        for known_host, publisher in host_to_publisher.items():
            if host.endswith("." + known_host) or known_host.endswith("." + host):
                return publisher

    return ""


def ensure_article_display_metadata(article: Article) -> None:
    """과거 archive 등에서 비어 있는 표시용 언론사명을 안전하게 복원합니다."""
    if not article.publisher.strip():
        article.publisher = infer_publisher_from_url(
            article.link,
            article.source_url,
        )

    if not article.publisher.strip():
        article.publisher = "출처 미확인"


def article_from_dict(data: dict) -> Article | None:
    try:
        title = str(data.get("title", ""))
        publisher = str(data.get("publisher", "")).strip()
        source_url = str(data.get("source_url", ""))
        link = str(data.get("link", ""))

        if publisher in EXCLUDED_PUBLISHERS:
            return None

        if not publisher:
            publisher = infer_publisher_from_url(link, source_url)
        if not publisher:
            publisher = "출처 미확인"

        # 과거 archive에 이미 저장된 무관·광고·유해 기사도 표시하지 않음
        if not is_news_source(publisher, source_url, title):
            return None

        group_name = str(data.get("group", ""))
        group_name = {
            "한수원·한국수력원자력": "한국수력원자력",
            "한전·한국전력": "한국전력",
            "원전·원자력": "원자력",
            "정부 관계부처": "원전 관계부처",
        }.get(group_name, group_name)

        if group_name == "현대건설" and is_hyundai_volleyball_article(title):
            return None

        return Article(
            title=title,
            link=link,
            published=date_parser.parse(str(data.get("published", ""))).astimezone(KST),
            language=str(data.get("language", "")),
            group=group_name,
            publisher=publisher,
            image=str(data.get("image", "")),
            source_url=source_url,
            description=str(data.get("description", "")),
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
    """
    RAW 검토모드용 날짜별 누적 저장.
    의미상 중복 제거는 하지 않고, 동일 URL 반복만 하나로 합칩니다.
    """
    for label in ("전일", "금일", "익일"):
        start, end = periods[label]
        key = end.strftime("%Y-%m-%d")

        existing_items: list[Article] = []
        existing_entry = archive.get(key, {})
        for raw in existing_entry.get("articles", []):
            article = article_from_dict(raw)
            if article is not None and start <= article.published < end:
                existing_items.append(article)

        current_items = list(articles_by_period.get(label, []))
        merged_by_url: dict[str, Article] = {}

        for article in existing_items + current_items:
            normalized_link = article.link.strip() if article.link else ""
            identity = normalized_link or f"{article.publisher}|{article.title}|{article.published.isoformat()}"

            previous = merged_by_url.get(identity)
            if previous is None:
                merged_by_url[identity] = article
                continue

            previous_score = int(bool(previous.image)) + int(bool(previous.description))
            current_score = int(bool(article.image)) + int(bool(article.description))
            if current_score >= previous_score:
                merged_by_url[identity] = article

        merged_items = sorted(
            merged_by_url.values(),
            key=lambda article: -article.published.timestamp(),
        )

        archive[key] = {
            "label": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "updated_at": generated_at.isoformat(),
            "articles": [article_to_dict(article) for article in merged_items],
        }

        print(
            f"[ARCHIVE MERGE {label}] existing={len(existing_items)} "
            f"/ current={len(current_items)} / merged={len(merged_items)}"
        )

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
    <div class="group-master-control">
      <button class="group-master-button" type="button" data-collapsed="true">전체 펼치기 ▼</button>
    </div>
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
        f'<button class="tab-button{" active" if label == "금일" else ""}" type="button" data-tab="{escape(label)}">{escape(label)}</button>'
        for label in ("전일", "금일", "익일")
    )

    panels: list[str] = []
    for label in ("전일", "금일", "익일"):
        start, end = periods[label]
        sections = render_news_sections(articles_by_period[label], new_urls)
        panel_class = "tab-panel active" if label == "금일" else "tab-panel"

        note = ""

        panels.append(f'''
<section class="{panel_class}" id="tab-{escape(label)}">
  <div class="period-card">
    <strong>{escape(label)}</strong>
    <span>{start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)</span>
  </div>
  {note}
  <div class="language-section">
    <div class="group-master-control">
      <button class="group-master-button" type="button" data-collapsed="true">전체 펼치기 ▼</button>
    </div>
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
body {{ margin: 0; background: #c4d6e8; color: #111827; font-family: Arial, "Malgun Gothic", sans-serif; }}
.phone {{ width: min(100%, 520px); min-height: 100vh; margin: 0 auto; background: #c4d6e8; }}
.topbar {{ position: sticky; top: 0; z-index: 20; margin: 10px 8px 14px; padding: 16px 16px 14px; background: #23395d; border: 1px solid rgba(255,255,255,.18); border-radius: 22px; box-shadow: 0 8px 20px rgba(17,24,39,.12); backdrop-filter: blur(8px); }}
.topbar-title-row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
.topbar h1 {{ margin: 0; color: #ffffff; font-size: 20px; line-height: 1.25; font-weight: 900; letter-spacing: -.4px; text-shadow: none; }}
.header-toggle {{ flex: 0 0 96px; width: 100px; min-width: 100px; height: 42px; padding: 0 8px; border: 1px solid rgba(17,24,39,.14); border-radius: 16px; background: #f7e889; color: #111827; font-size: 10px; font-weight: 800; letter-spacing: -.2px; cursor: pointer; box-shadow: none; }}
.header-toggle:hover {{ background: #f5d900; }}
.header-toggle:active {{ transform: translateY(1px); }}
.topbar.collapsed .header-toggle {{ background: #fee500; color: #111827; border-color: rgba(17,24,39,.14); box-shadow: none; }}
.header-controls {{ overflow: hidden; max-height: 210px; opacity: 1; transition: max-height .2s ease, opacity .15s ease, margin .2s ease; }}
.topbar.collapsed {{ padding-bottom: 9px; background: #23395d; }}
.topbar.collapsed .header-controls {{ max-height: 0; opacity: 0; margin: 0; pointer-events: none; }}
.updated {{ margin-top: 7px; color: rgba(255,255,255,.72); font-size: 10.5px; font-weight: 600; }}
.tabs {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin-top: 13px; }}
.utility-row {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:8px; align-items:stretch; }}
.utility-box {{ min-width:0; height:40px; padding:0 7px; border:1px solid rgba(17,24,39,.14); border-radius:11px; background:rgba(255,255,255,.96); display:flex; align-items:center; gap:6px; box-shadow:0 1px 4px rgba(17,24,39,.06); }}
.utility-label {{ flex:0 0 auto; color:#344054; font-size:10.5px; font-weight:800; white-space:nowrap; line-height:1; }}
.language-order-toggle {{ flex:1 1 auto; min-width:0; height:28px; padding:0 8px; border:0; border-radius:8px; background:#344054; color:#ffffff; font-size:10.5px; font-weight:800; line-height:28px; text-align:center; white-space:nowrap; cursor:pointer; box-shadow:inset 0 0 0 1px rgba(255,255,255,.05); }}
.language-order-toggle:active {{ transform: translateY(1px); }}
.date-picker-box {{ cursor: pointer; }}
.date-control {{ position:relative; flex:1 1 auto; min-width:0; height:28px; border-radius:8px; background:#344054; color:#ffffff; display:flex; align-items:center; justify-content:center; overflow:hidden; box-shadow:inset 0 0 0 1px rgba(255,255,255,.05); }}
.date-display {{ position:relative; z-index:1; width:100%; padding:0 18px 0 6px; box-sizing:border-box; color:#ffffff; font-size:10.5px; font-weight:800; line-height:28px; text-align:center; white-space:nowrap; font-variant-numeric:tabular-nums; letter-spacing:-0.1px; pointer-events:none; }}
.date-calendar {{ position:absolute; z-index:1; right:6px; top:50%; transform:translateY(-52%); color:#ffffff; font-size:10px; line-height:1; pointer-events:none; opacity:.9; }}
.date-input {{ position:absolute; z-index:5; inset:0; width:100%; height:100%; margin:0; padding:0; border:0; opacity:0; cursor:pointer; }}
.date-input::-webkit-calendar-picker-indicator {{ width: 100%; height: 100%; margin: 0; padding: 0; cursor: pointer; }}
.search-wrap {{ position: relative; margin-top: 6px; }}
.search-input {{ width: 100%; height: 34px; padding: 0 12px; border: 1px solid rgba(17,24,39,.13); border-radius: 12px; background: rgba(255,255,255,.94); font-size: 11px; }}

.world-map-panel {{ margin: 0 12px 14px; padding: 11px 12px 12px; border: 1px solid rgba(35,57,93,.12); border-radius: 20px; background: rgba(255,255,255,.80); box-shadow: 0 3px 10px rgba(17,24,39,.06); }}
.world-map-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:8px; margin-bottom:7px; }}
.world-map-title-wrap {{ min-width:0; }}
.world-map-title {{ color:#23395d; font-size:11px; font-weight:900; }}
.world-map-summary {{ margin-top:2px; color:#667085; font-size:8.5px; font-weight:700; }}
.world-map-canvas {{ position:relative; width:100%; height:210px; overflow:hidden; border-radius:9px; background:#f7fafc; border:1px solid rgba(35,57,93,.08); box-sizing:border-box; }}
.world-map-image {{ position:absolute; inset:0; width:100%; height:100%; object-fit:fill; opacity:.92; }}
.map-connectors {{
  position:absolute;
  inset:0;
  width:100%;
  height:100%;
  pointer-events:none;
  z-index:2;
  overflow:visible;
}}
.map-connector-line {{
  stroke:rgba(35,57,93,.58);
  stroke-width:1.15;
  vector-effect:non-scaling-stroke;
}}
.map-anchor-dot {{
  fill:#1f4f8a;
  stroke:#ffffff;
  stroke-width:1.4;
  vector-effect:non-scaling-stroke;
}}
.map-anchor-dot.kr-anchor {{
  fill:#d92d20;
  r:3.2;
}}

.country-pin {{
  position:absolute;
  display:flex;
  align-items:center;
  gap:2px;
  min-height:21px;
  padding:2px 4px;
  border:1px solid rgba(35,57,93,.18);
  border-radius:999px;
  background:rgba(255,253,248,.98);
  color:#1f4f8a;
  box-shadow:0 2px 5px rgba(17,24,39,.14);
  font-size:8px;
  font-weight:900;
  white-space:nowrap;
  cursor:pointer;
  z-index:4;
  box-sizing:border-box;
  max-width:96px;
  justify-content:center;
}}
.country-pin .flag {{ font-size:11px; line-height:1; }}
.country-pin .country-count {{ color:#d92d20; font-weight:900; }}
.country-pin.active {{ background:#fee500; color:#202124; border-color:rgba(35,57,93,.28); }}
.country-pin[hidden] {{ display:none; }}

/* 국가 라벨은 JS가 현재 표시되는 국가 수에 맞춰 3열로 자동 배치합니다.
   고정 top/left 값을 사용하지 않아 기사 수가 달라져도 서로 겹치지 않습니다. */
.country-kr {{
  z-index:8;
  border-color:rgba(217,45,32,.30);
  box-shadow:0 2px 6px rgba(217,45,32,.14);
}}
.country-other {{
  opacity:.94;
}}
@media (min-width:700px) {{
  .country-pin {{
    min-height:26px;
    padding:3px 7px;
    font-size:10px;
    max-width:none;
  }}
  .country-pin .flag {{ font-size:14px; }}
}}
.country-filter-note {{ margin-top:5px; color:#667085; font-size:9px; text-align:center; }}
.world-map-credit {{ margin-top:3px; color:#98a2b3; font-size:7px; text-align:right; }}
@media (min-width: 700px) {{
  .world-map-panel {{ margin:0 20px 16px; padding:12px 14px 13px; }}
  .world-map-title {{ font-size:13px; }}
  .world-map-summary {{ font-size:10px; }}
  .world-map-canvas {{ height:260px; }}
  .country-pin {{ min-height:26px; padding:3px 8px; font-size:10px; }}
  .country-pin .flag {{ font-size:15px; }}
  .country-pin.tight {{ font-size:9px; padding:3px 6px; }}
  .country-pin.callout-right::after, .country-pin.callout-left::after {{ width:20px; }}
  .country-pin.callout-up::after, .country-pin.callout-down::after {{ height:18px; }}
}}
  .world-map-title {{ font-size:13px; }}
  .world-map-summary {{ font-size:10px; }}
  .country-pin {{ min-height:27px; padding:3px 8px; font-size:10px; }}
  .country-pin .flag {{ font-size:15px; }}
  }}
  .world-map-title {{ font-size:13px; }}
  .world-map-canvas {{ height:205px; }}
  .country-pin {{ min-height:29px; padding:4px 9px; font-size:10px; }}
  .country-pin .flag {{ font-size:15px; }}
}}

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
.period-card {{
  box-shadow:0 3px 10px rgba(15,23,42,.05); display: flex; flex-direction: column; gap: 3px; margin-bottom: 10px; padding: 10px 12px; color: #344054; background: #eef3f8; border-radius: 20px; font-size: 11px; }}
.period-card strong {{ color: #111827; font-size: 14px; }}
.partial-note {{ margin-bottom: 10px; padding: 9px 11px; color: #475467; background: #fff7cc; border-radius: 8px; font-size: 10px; line-height: 1.45; }}
.language-section {{ margin-bottom: 30px; }}
.group-nav-compact .group-nav-dense 
.news-group {{ margin-bottom: 16px; }}
.group-title {{ display: flex; align-items: center; gap: 5px; width: 100%; max-width: 100%; height: 27px; box-sizing: border-box; margin: 0; padding: 0 14px; border: 1px solid rgba(91,79,38,.10); background: #efe2a1; color: #1f4f8a; border-radius: 14px; font: inherit; font-size: 11px; font-weight: 800; line-height: 1; text-align: left; box-shadow: 0 1px 2px rgba(62,52,42,.08); cursor: pointer; }}
.group-title:active {{ transform: translateY(1px); }}
.group-master-control {{ display: flex; justify-content: flex-end; margin: 0 0 8px; }}
.group-master-button {{ width: 96px; min-width: 96px; height: 30px; padding: 0 8px; border: 1px solid rgba(17,24,39,.12); border-radius: 7px; background: rgba(255,255,255,.88); color: #344054; font-size: 10px; font-weight: 800; cursor: pointer; box-shadow: 0 1px 2px rgba(17,24,39,.08); }}
.group-master-button:active {{ transform: translateY(1px); }}
.group-name {{ display: inline-flex; align-items: center; height: 27px; font-size: 12px; font-weight: 800; line-height: 1; white-space: nowrap; }}
.group-count {{ display: inline-flex; align-items: center; height: 27px; margin-left: 2px; color: #4f6f96; font-size: 12px; font-weight: 800; line-height: 1; white-space: nowrap; }}
.group-arrow {{ display: inline-flex; align-items: center; justify-content: center; width: 10px; min-width: 10px; height: 27px; color: #1f4f8a; font-size: 10px; line-height: 1; }}
.article-stack {{ display: grid; gap: 10px; margin-top: 7px; margin-bottom: 7px; }}
.news-group.collapsed .article-stack {{ display: none; }}
.preview-card {{ position:relative; display:grid; grid-template-columns:minmax(0,1fr) 86px; gap:6px; align-items:stretch; min-height:98px; padding:3px 2px 3px 6px; border-radius:0; background:#fbfaf7; border:1px solid rgba(35,57,93,.09); box-shadow:0 2px 7px rgba(15,23,42,.05); overflow:visible; transition:opacity .15s ease, background .15s ease; }}
.preview-card.read {{ background: #ebeff3; opacity: .92; }}
.preview-card.read .headline {{ grid-column:2 / 4; grid-row:2; display:-webkit-box; overflow:hidden; margin:2px 0 0; padding:0; color:#0b57d0; font-size:12.8px; font-weight:750; line-height:1.27; letter-spacing:-0.08px; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.preview-card.read .publisher {{ flex:0 1 auto; min-width:0; color:#475467; font-size:9.5px; font-weight:800; line-height:1.05; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.preview-card.read .article-snippet {{ grid-column:2 / 4; grid-row:3; display:-webkit-box; overflow:hidden; margin-top:2px; padding:0; color:#5f6672; font-size:9.6px; line-height:1.34; letter-spacing:-0.03px; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}

.preview-card.important {{ border: 2px solid #f2c94c; background: #fffdf3; opacity: 1; }}
.article-number {{ display:none; }}
.preview-copy {{ display:grid; grid-template-columns:auto minmax(0,1fr) 20px; grid-template-rows:auto auto auto; column-gap:4px; row-gap:0; min-width:0; min-height:98px; padding:2px 0; overflow:visible; align-content:start; }}
.publisher {{ overflow: hidden; color: #667085; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }}
.headline {{ display: -webkit-box; overflow: hidden; margin-top: 3px; color: #0b57d0; font-size: 13px; font-weight: 700; line-height: 1.32; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.article-snippet {{ display: -webkit-box; overflow: hidden; margin-top: 5px; color: #5f6368; font-size: 10.5px; font-weight: 400; line-height: 1.42; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.status-line {{ display: none; }}
.status-text {{ min-width: 0; }}

.unread-label {{ color: #d92d20; font-weight: 800; white-space: nowrap; }}
.read-label {{ display: none; color: #4f5968; font-weight: 800; white-space: nowrap; }}
.important-label {{ display: none; color: #b77900; font-weight: 800; white-space: nowrap; }}
.preview-card.read .unread-label {{ display: none; }}
.preview-card.read .read-label {{ display: inline; }}
.preview-card.important .unread-label, .preview-card.important .read-label {{ display: none; }}
.preview-card.important .important-label {{ display: inline; }}
.card-side {{ position:relative; align-self:stretch; width:86px; min-width:86px; display:flex; align-items:stretch; justify-content:flex-end; overflow:visible; background:transparent; padding:0; }}
.important-button {{ grid-column:3; grid-row:1; align-self:center; justify-self:end; width:20px; height:18px; padding:0; border:0; border-radius:0; background:transparent; color:#98a2b3; font-size:14px; font-weight:800; line-height:18px; text-align:center; cursor:pointer; box-shadow:none; overflow:visible; margin:0; }}
.important-button:hover {{ background: rgba(17,24,39,.04); }}
.preview-card.important .important-button {{ color: #b77900; border: 0; background: transparent; }}
.preview-image {{ width:86px; height:98px; min-height:98px; align-self:stretch; display:flex; align-items:center; justify-content:center; overflow:hidden; border:0; border-radius:0; background:#f4f6f8; margin-left:auto; }}
.preview-image img {{ display: block; width: 100%; height: 100%; min-height: 0; object-fit: cover; object-position: 50% 50%; }}
.new-badge {{ display: inline-block; margin-right: 4px; padding: 1px 4px; border-radius: 0; color: white; background: #e5484d; font-size: 8px; font-weight: 900; }}
.no-image {{ display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; min-height: 0; color: #6b7280; font-size: 9px; font-weight: 800; line-height: 1.25; text-align: center; }}
.empty {{ padding: 22px 15px; background: #fffdf8; border-radius: 10px; text-align: center; color: #667085; }}
footer {{ padding: 0 12px 28px; color: #475467; font-size: 10px; text-align: center; }}
@media (min-width: 768px) {{
  body {{ background: #d7e0e8; }}
  .phone {{ width: 100%; max-width: none; background: #d7e0e8; }}
  .topbar {{ margin: 16px 20px 0; padding: 18px 22px 15px; border-radius: 14px; }}
  .topbar h1 {{ font-size: 23px; }}
  .header-controls {{ max-height: 260px; }}
  .search-wrap {{ margin-top: 12px; }}
  .search-input {{ height: 40px; font-size: 13px; }}
  .tabs {{ grid-template-columns: repeat(3, 140px); justify-content: start; }}
  .tab-button {{ height: 38px; font-size: 13px; }}
  .utility-row {{ grid-template-columns: repeat(2,minmax(0,1fr)); max-width: 600px; }}
  .utility-box {{ height: 36px; padding: 0 10px; }}
  .utility-label, .language-order-toggle {{ font-size: 12px; }}
  .language-order-toggle {{ height: 28px; min-width: 0; padding: 0 8px; font-size: 12px; }}
  .date-control {{ min-width: 112px; height: 28px; }}
  .date-display {{ padding: 0 20px 0 7px; font-size:12px; line-height: 28px; font-weight:800; text-align: center; }}
  .date-calendar {{ right: 7px; font-size: 11px; }}
  main {{ padding: 18px 20px 44px; }}
  .favorites-panel {{ margin-bottom: 16px; padding: 16px; border-radius: 12px; }}
  .favorites-title {{ margin-bottom: 12px; font-size: 16px; }}
  .favorites-list {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
  .favorite-item {{ min-height: 92px; padding: 14px 16px; gap: 10px; border-radius: 10px; }}
  .favorite-publisher {{ font-size: 11px; }}
  .favorite-headline {{ margin-top: 5px; font-size: 14px; line-height: 1.45; }}
  .favorite-remove {{ align-self: start; font-size: 22px; }}
  .period-card {{ flex-direction: row; align-items: center; justify-content: space-between; padding: 12px 16px; font-size: 12px; }}
  .period-card strong {{ font-size: 16px; }}
  .group-master-button, .header-toggle {{ width: 106px; min-width: 106px; height: 34px; padding: 0 9px; font-size: 11px; }}
      .group-nav-compact   .group-nav-dense 
  .news-group {{ margin-bottom: 18px; }}
  .group-title {{ height: 30px; padding: 0 11px; font-size: 12px; border-radius: 6px; }}
  .group-name {{ height: 30px; font-size: 12px; }}
  .group-count {{ height: 30px; font-size: 12px; }}
  .group-arrow {{ height: 30px; }}
  .article-stack {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }}
  .preview-card {{ grid-template-columns: 32px minmax(0,1fr) 124px; height: 142px; min-height: 142px; border-radius: 11px; }}
  .article-number {{ padding-top: 12px; font-size: 13px; }}
  .preview-copy {{ padding: 10px 11px 8px 0; }}
  .publisher {{ font-size: 11px; }}
  .headline {{ margin-top: 5px; font-size: 14px; line-height: 1.38; -webkit-line-clamp: 2; }}
  .article-snippet {{ margin-top: 6px; font-size: 11.5px; line-height: 1.45; -webkit-line-clamp: 2; }}
  .status-line {{ font-size: 10px; }}
  .card-side, .preview-image {{ width: 124px; height: 142px; min-height: 142px; }}
  .card-side {{ align-self: center; }}
  .preview-image img {{ height: 142px; min-height: 142px; }}
  footer {{ padding-bottom: 28px; font-size: 11px; }}
}}

@media (min-width: 1200px) {{
  .phone {{ width: 100%; max-width: none; }}
  .topbar {{ margin-left: 24px; margin-right: 24px; }}
  main {{ padding-left: 24px; padding-right: 24px; }}
  .favorites-list {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
  .favorite-item {{ min-height: 104px; padding: 16px 18px; }}
  .favorite-publisher {{ font-size: 12px; }}
  .favorite-headline {{ font-size: 15px; line-height: 1.48; }}
  .article-stack {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  .preview-card {{ grid-template-columns: 30px minmax(0,1fr) 112px; }}
  .card-side, .preview-image {{ width: 112px; }}
}}

@media (max-width: 380px) {{ .preview-card {{ grid-template-columns: 24px minmax(0,1fr) 76px; }} .card-side, .preview-image {{ width: 76px; }} .headline {{ font-size: 12px; }} .article-snippet {{ font-size: 10px; }} }}

/* Newspaper-inspired visual refinement */
body {{
  background-color: #f5f3ee;
  background-image:
    linear-gradient(rgba(95, 82, 63, 0.018) 1px, transparent 1px),
    linear-gradient(90deg, rgba(95, 82, 63, 0.012) 1px, transparent 1px);
  background-size: 4px 4px;
}}

.page,
.container,
.main-wrap {{
  background: transparent;
}}

header,
.hero,
.top-header {{
  background: #faf8f2;
  border-bottom: 1px solid rgba(62, 52, 42, .16);
  box-shadow: none;
}}

.brand-title,
.site-title {{
  color: #202124;
  letter-spacing: -0.02em;
  font-family: Georgia, "Times New Roman", "Noto Serif KR", serif;
  font-weight: 700;
}}

.topbar h1 {{
  font-family: Arial, "Malgun Gothic", sans-serif;
  font-weight: 900;
}}

.preview-card {{
  background: #fffdf8;
  border-color: rgba(62, 52, 42, .11);
  box-shadow: 0 1px 2px rgba(62, 52, 42, .07);
}}

.preview-card:hover {{
  background: #fffaf0;
  box-shadow: 0 2px 5px rgba(62, 52, 42, .10);
}}

.publisher {{
  color: #6f655a;
}}

.headline {{
  color: #0b57d0;
}}

.article-snippet {{
  color: #6a665f;
}}

.section-title,
.group-title,
.tab-button,
.period-tab {{
  letter-spacing: -0.01em;
}}

@media (prefers-color-scheme: dark) {{
  body {{
    background-image: none;
  }}
}}


@media (min-width:768px) {{
  .utility-row {{ gap:10px; max-width:620px; }}
  .utility-box {{ height:42px; padding:0 9px; border-radius:12px; gap:7px; }}
  .utility-label {{ font-size:12px; }}
  .language-order-toggle, .date-control {{ height:30px; border-radius:9px; }}
  .language-order-toggle {{ font-size:12px; line-height:30px; padding:0 10px; }}
  .date-display {{ font-size:12px; line-height:30px; padding:0 20px 0 8px; }}
  .date-calendar {{ right:7px; font-size:11px; }}
}}

@media (min-width: 768px) {{
  .preview-card {{ grid-template-columns: 30px minmax(0,1fr) 104px; gap: 14px; padding: 14px 14px 16px 14px; }}
  .preview-copy {{ min-height: 148px; padding: 8px 10px 8px 0; }}
  .status-line {{ min-height: 38px; }}
  .card-side {{ width: 104px; min-width: 104px; }}
  .preview-image {{ width: 104px; min-height: 148px; }}
}}

.meta-row {{ display:contents; }}

.meta-left {{ display:contents; }}

.status-inline {{ flex:0 0 auto; display:flex; align-items:center; gap:2px; font-size:9px; line-height:1; white-space:nowrap; }}

@media (min-width: 768px) {{
  .preview-copy {{ min-height: 148px; gap: 8px; }}
  .meta-row {{ min-height: 38px; }}
  .publisher, .status-inline {{ font-size: 10px; }}
  .important-button {{ width: 32px; height: 32px; font-size: 19px; line-height: 30px; }}
}}

@media (min-width: 768px) {{
  .preview-card {{ grid-template-columns: 30px minmax(0,1fr) 96px; gap: 12px; min-height: 118px; padding: 10px 12px; }}
  .preview-copy {{ min-height: 118px; padding: 3px 6px 3px 0; gap: 6px; }}
  .meta-row {{ min-height: 30px; }}
  .card-side {{ width: 96px; min-width: 96px; }}
  .preview-image {{ width: 96px; height: 118px; min-height: 118px; }}
  .important-button {{ width: 30px; height: 30px; font-size: 18px; line-height: 28px; }}
}}

@media (min-width: 768px) {{
  .meta-row {{ min-height: 24px; }}
  .important-button {{ width: 22px; height: 22px; font-size: 13px; line-height: 22px; }}
}}

@media (min-width:768px) {{
  .preview-copy {{ gap:0; }}
  .meta-row {{ min-height:24px; margin-bottom:2px; }}
  .publisher, .status-inline {{ font-size:10px; }}
  .important-button {{ width:24px; height:24px; font-size:15px; line-height:24px; }}
  .headline {{ margin-top:1px; line-height:1.30; }}
  .article-snippet {{ margin-top:4px; line-height:1.40; }}
}}

@media (min-width:768px) {{
  .preview-card {{ min-height:112px; padding:3px 12px; }}
  .preview-copy {{ min-height:112px; padding:0 6px 0 0; }}
  .preview-image {{ height:112px; min-height:112px; align-self:stretch; }}
}}

@media (min-width:768px) {{
  .preview-card {{ border-radius:0; }}
}}

@media (min-width:768px) {{
  .preview-card {{ grid-template-columns:24px minmax(0,1fr) 96px; gap:10px; min-height:112px; padding:3px 4px 3px 10px; border-radius:0; }}
  .preview-copy {{ min-height:112px; padding:0 1px 0 0; }}
  .meta-row {{ min-height:22px; margin-bottom:1px; gap:5px; }}
  .meta-left {{ gap:5px; }}
  .publisher, .status-inline {{ font-size:10px; line-height:1.12; }}
  .article-number {{ font-size:13px; }}
  .headline {{ line-height:1.25; }}
  .article-snippet {{ margin-top:3px; line-height:1.34; }}
  .important-button {{ width:26px; height:22px; font-size:15px; line-height:22px; }}
  .card-side {{ width:96px; min-width:96px; justify-content:flex-end; }}
  .preview-image {{ width:96px; height:112px; min-height:112px; margin-left:auto; }}
}}

.publisher-row {{ display:contents; }}

.article-order-inline {{ display:block; color:#475467; font-size:9.5px; font-weight:800; line-height:18px; white-space:nowrap; }}

@media (min-width:768px) {{
  .preview-card {{ grid-template-columns:minmax(0,1fr) 96px; gap:10px; min-height:112px; padding:3px 4px 3px 10px; border-radius:0; }}
  .preview-copy {{ min-height:112px; padding:0 1px 0 0; }}
  .meta-row {{ min-height:22px; margin-bottom:1px; gap:5px; }}
  .publisher-row {{ gap:5px; }}
  .article-order-inline {{ font-size:11px; }}
  .publisher, .status-inline {{ font-size:10px; line-height:1.12; }}
}}

.meta-divider {{ flex:0 0 auto; color:#b0b7c3; font-size:9px; line-height:1; }}

@media (min-width:768px) {{
  .preview-card {{ grid-template-columns:minmax(0,1fr) 96px; gap:8px; min-height:106px; padding:4px 3px 4px 8px; }}
  .preview-copy {{ min-height:106px; padding:2px 0; }}
  .meta-row {{ min-height:20px; margin-bottom:2px; }}
  .meta-left {{ gap:4px; }}
  .article-order-inline, .meta-divider, .status-inline {{ font-size:10px; }}
  .publisher {{ max-width:55%; font-size:10px; }}
  .important-button {{ width:22px; height:20px; font-size:15px; line-height:20px; }}
  .headline {{ font-size:13.5px; line-height:1.28; }}
  .article-snippet {{ margin-top:3px; font-size:10px; line-height:1.36; }}
  .card-side {{ width:96px; min-width:96px; }}
  .preview-image {{ width:96px; height:106px; min-height:106px; }}
}}

@media (min-width:768px) {{
  .headline, .article-snippet {{ padding-left:24px; }}
}}

.article-order-column {{ grid-column:1; grid-row:1; display:flex; align-items:center; justify-content:flex-start; min-width:0; white-space:nowrap; }}

.article-text-column {{ display:contents; }}

@media (min-width:768px) {{
  .preview-copy {{ grid-template-columns:20px minmax(0,1fr); column-gap:5px; }}
  .article-order-column {{ padding-top:2px; }}
  .article-order-inline {{ font-size:10px; }}
  .headline, .article-snippet {{ padding-left:0 !important; }}
}}

.meta-content {{ grid-column:2; grid-row:1; min-width:0; display:flex; align-items:center; gap:3px; height:18px; overflow:hidden; }}

@media (min-width:768px) {{
  .preview-copy {{ grid-template-columns:auto minmax(0,1fr) 22px; column-gap:5px; }}
  .article-order-inline {{ font-size:10px; line-height:20px; }}
  .meta-content {{ height:20px; gap:4px; }}
  .publisher, .status-inline {{ font-size:10px; }}
  .important-button {{ width:22px; height:20px; font-size:15px; line-height:20px; }}
  .headline {{ margin-top:2px; padding:0 !important; }}
  .article-snippet {{ margin-top:3px; padding:0 !important; }}
}}


/* === FINAL ARTICLE CARD ALIGNMENT ===
   1열 번호 / 2열 본문 / 3열 즐겨찾기.
   언론사·제목·미리보기는 모두 2열의 동일한 x축에서 시작합니다. */
.preview-card {{
  grid-template-columns:minmax(0,1fr) 86px;
  gap:6px;
  min-height:98px;
  padding:3px 2px 3px 6px;
  border-radius:0;
}}
.preview-copy {{
  display:grid;
  grid-template-columns:18px minmax(0,1fr) 20px;
  column-gap:4px;
  min-width:0;
  min-height:98px;
  padding:2px 0;
  align-content:start;
  overflow:visible;
}}
.article-order-column {{
  grid-column:1;
  grid-row:1;
  display:flex;
  align-items:flex-start;
  justify-content:flex-start;
  padding-top:1px;
  white-space:nowrap;
}}
.article-order-inline {{
  color:#475467;
  font-size:9.5px;
  font-weight:800;
  line-height:18px;
}}
.article-content-column {{
  grid-column:2;
  grid-row:1;
  min-width:0;
  display:flex;
  flex-direction:column;
  align-items:stretch;
}}
.meta-row {{
  display:flex;
  align-items:center;
  min-width:0;
  height:18px;
  gap:3px;
  margin:0 0 2px 0;
  overflow:hidden;
}}
.publisher {{
  flex:0 1 auto;
  min-width:0;
  color:#475467;
  font-size:9.5px;
  font-weight:800;
  line-height:1.05;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}}
.meta-divider {{
  flex:0 0 auto;
  color:#b0b7c3;
  font-size:9px;
}}
.status-inline {{
  flex:0 0 auto;
  display:flex;
  align-items:center;
  font-size:9px;
  line-height:1;
  white-space:nowrap;
}}
.important-button {{
  grid-column:3;
  grid-row:1;
  align-self:start;
  justify-self:end;
  width:20px;
  height:18px;
  margin:0;
  padding:0;
  border:0;
  background:transparent;
  font-size:14px;
  line-height:18px;
}}
.headline {{
  display:-webkit-box;
  margin:2px 0 0;
  padding:0 !important;
  color:#0b57d0;
  font-size:12.8px;
  font-weight:750;
  line-height:1.27;
  overflow:hidden;
  -webkit-line-clamp:2;
  -webkit-box-orient:vertical;
}}
.article-snippet {{
  display:-webkit-box;
  margin:2px 0 0;
  padding:0 !important;
  color:#5f6672;
  font-size:9.6px;
  line-height:1.34;
  overflow:hidden;
  -webkit-line-clamp:2;
  -webkit-box-orient:vertical;
}}
.article-snippet-empty {{
  color:#98a2b3;
  font-style:normal;
}}
.card-side {{
  width:86px;
  min-width:86px;
}}
.preview-image {{
  width:86px;
  height:98px;
  min-height:98px;
  border-radius:0;
}}

@media (min-width:768px) {{
  .preview-card {{
    grid-template-columns:minmax(0,1fr) 96px;
    gap:8px;
    min-height:106px;
    padding:4px 3px 4px 8px;
  }}
  .preview-copy {{
    grid-template-columns:20px minmax(0,1fr) 22px;
    column-gap:5px;
    min-height:106px;
  }}
  .article-order-inline, .publisher, .status-inline {{
    font-size:10px;
  }}
  .meta-row {{
    height:20px;
  }}
  .important-button {{
    width:22px;
    height:20px;
    font-size:15px;
    line-height:20px;
  }}
  .headline {{
    font-size:13.5px;
  }}
  .article-snippet {{
    font-size:10px;
  }}
  .card-side {{
    width:96px;
    min-width:96px;
  }}
  .preview-image {{
    width:96px;
    height:106px;
    min-height:106px;
  }}
}}

/* === FINAL PREVIEW TEXT SIZE === */
.article-snippet {{
  font-size:11px !important;
  line-height:1.38 !important;
  margin-top:3px !important;
}}
.article-snippet-empty {{
  font-size:10.5px !important;
}}

@media (min-width:768px) {{
  .article-snippet {{
    font-size:11.5px !important;
    line-height:1.40 !important;
    margin-top:3px !important;
  }}
}}

</style>
</head>
<body>
<div class="phone">
  <header class="topbar" id="topbar">
    <div class="topbar-title-row">
      <h1>원자력 주요기사</h1>
      <button id="header-toggle" class="header-toggle" type="button" aria-expanded="true">설정 접기 ▲</button>
    </div>
    <div class="header-controls" id="header-controls">
      <div class="updated">최종 업데이트: {generated_at:%Y. %-m. %-d. %H:%M} (KST)</div>
      <div class="search-wrap"><input id="article-search" class="search-input" type="search" placeholder="기사·언론사·기업·프로젝트·국가 검색"></div>
      <div class="tabs">{buttons}</div>
      <div class="utility-row">
        <div class="utility-box language-order-box">
          <span class="utility-label">기사 순서</span>
          <button id="language-order" class="language-order-toggle" type="button" aria-label="기사 언어 우선순위 변경">한글 → 영어</button>
        </div>
        <label class="utility-box date-picker-box" for="archive-date">
          <span class="utility-label">날짜 보기</span>
          <span class="date-control">
            <span id="archive-date-display" class="date-display">0000.00.00</span>
            <span class="date-calendar" aria-hidden="true">▾</span>
            <input id="archive-date" class="date-input" type="date" aria-label="기사 날짜 선택">
          </span>
        </label>
      </div>
    </div>
  </header>
  <section id="world-map-panel" class="world-map-panel">
    <div class="world-map-head">
      <div class="world-map-title-wrap">
        <div class="world-map-title">🌐 국가별 기사</div>
        <div id="world-map-summary" class="world-map-summary">현재 탭 전체 0건 · 국가 합계 0건</div>
      </div>
    </div>
    <div class="world-map-canvas" aria-label="국가별 기사 필터 지도">
      <img class="world-map-image" src="https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Blank_world_map.svg/960px-Blank_world_map.svg.png" alt="세계지도" loading="lazy">
      <svg id="map-connectors" class="map-connectors" aria-hidden="true"></svg>
      <button class="country-pin country-kr" data-country-filter="KR" data-anchor-x="83.2" data-anchor-y="38.8" type="button"><span class="flag">🇰🇷</span><span>한국</span><span class="country-count">0건</span></button>
      <button class="country-pin country-ca" data-country-filter="CA" data-anchor-x="20.0" data-anchor-y="26.0" type="button"><span class="flag">🇨🇦</span><span>캐나다</span><span class="country-count">0건</span></button>
      <button class="country-pin country-us" data-country-filter="US" data-anchor-x="22.0" data-anchor-y="39.5" type="button"><span class="flag">🇺🇸</span><span>미국</span><span class="country-count">0건</span></button>
      <button class="country-pin country-gb" data-country-filter="GB" data-anchor-x="47.8" data-anchor-y="29.0" type="button"><span class="flag">🇬🇧</span><span>영국</span><span class="country-count">0건</span></button>
      <button class="country-pin country-fr" data-country-filter="FR" data-anchor-x="48.6" data-anchor-y="35.0" type="button"><span class="flag">🇫🇷</span><span>프랑스</span><span class="country-count">0건</span></button>
      <button class="country-pin country-nl" data-country-filter="NL" data-anchor-x="49.2" data-anchor-y="31.3" type="button"><span class="flag">🇳🇱</span><span>네덜란드</span><span class="country-count">0건</span></button>
      <button class="country-pin country-be" data-country-filter="BE" data-anchor-x="49.0" data-anchor-y="33.1" type="button"><span class="flag">🇧🇪</span><span>벨기에</span><span class="country-count">0건</span></button>
      <button class="country-pin country-ch" data-country-filter="CH" data-anchor-x="49.9" data-anchor-y="35.8" type="button"><span class="flag">🇨🇭</span><span>스위스</span><span class="country-count">0건</span></button>
      <button class="country-pin country-se" data-country-filter="SE" data-anchor-x="52.8" data-anchor-y="24.5" type="button"><span class="flag">🇸🇪</span><span>스웨덴</span><span class="country-count">0건</span></button>
      <button class="country-pin country-fi" data-country-filter="FI" data-anchor-x="56.4" data-anchor-y="24.0" type="button"><span class="flag">🇫🇮</span><span>핀란드</span><span class="country-count">0건</span></button>
      <button class="country-pin country-pl" data-country-filter="PL" data-anchor-x="54.0" data-anchor-y="32.0" type="button"><span class="flag">🇵🇱</span><span>폴란드</span><span class="country-count">0건</span></button>
      <button class="country-pin country-cz" data-country-filter="CZ" data-anchor-x="51.7" data-anchor-y="33.8" type="button"><span class="flag">🇨🇿</span><span>체코</span><span class="country-count">0건</span></button>
      <button class="country-pin country-si" data-country-filter="SI" data-anchor-x="51.3" data-anchor-y="36.8" type="button"><span class="flag">🇸🇮</span><span>슬로베니아</span><span class="country-count">0건</span></button>
      <button class="country-pin country-ro" data-country-filter="RO" data-anchor-x="55.3" data-anchor-y="36.0" type="button"><span class="flag">🇷🇴</span><span>루마니아</span><span class="country-count">0건</span></button>
      <button class="country-pin country-bg" data-country-filter="BG" data-anchor-x="55.6" data-anchor-y="39.2" type="button"><span class="flag">🇧🇬</span><span>불가리아</span><span class="country-count">0건</span></button>
      <button class="country-pin country-ua" data-country-filter="UA" data-anchor-x="59.6" data-anchor-y="33.0" type="button"><span class="flag">🇺🇦</span><span>우크라이나</span><span class="country-count">0건</span></button>
      <button class="country-pin country-ru" data-country-filter="RU" data-anchor-x="68.0" data-anchor-y="25.5" type="button"><span class="flag">🇷🇺</span><span>러시아</span><span class="country-count">0건</span></button>
      <button class="country-pin country-tr" data-country-filter="TR" data-anchor-x="57.3" data-anchor-y="41.4" type="button"><span class="flag">🇹🇷</span><span>튀르키예</span><span class="country-count">0건</span></button>
      <button class="country-pin country-ae" data-country-filter="AE" data-anchor-x="61.8" data-anchor-y="49.5" type="button"><span class="flag">🇦🇪</span><span>UAE</span><span class="country-count">0건</span></button>
      <button class="country-pin country-vn" data-country-filter="VN" data-anchor-x="78.6" data-anchor-y="52.0" type="button"><span class="flag">🇻🇳</span><span>베트남</span><span class="country-count">0건</span></button>
      <button class="country-pin country-sa" data-country-filter="SA" data-anchor-x="59.0" data-anchor-y="49.0" type="button"><span class="flag">🇸🇦</span><span>사우디</span><span class="country-count">0건</span></button>
      <button class="country-pin country-in" data-country-filter="IN" data-anchor-x="69.0" data-anchor-y="50.0" type="button"><span class="flag">🇮🇳</span><span>인도</span><span class="country-count">0건</span></button>
      <button class="country-pin country-cn" data-country-filter="CN" data-anchor-x="77.0" data-anchor-y="41.0" type="button"><span class="flag">🇨🇳</span><span>중국</span><span class="country-count">0건</span></button>
      <button class="country-pin country-jp" data-country-filter="JP" data-anchor-x="88.0" data-anchor-y="40.5" type="button"><span class="flag">🇯🇵</span><span>일본</span><span class="country-count">0건</span></button>
      <button class="country-pin country-au" data-country-filter="AU" data-anchor-x="84.0" data-anchor-y="69.0" type="button"><span class="flag">🇦🇺</span><span>호주</span><span class="country-count">0건</span></button>
      <button class="country-pin country-za" data-country-filter="ZA" data-anchor-x="54.5" data-anchor-y="71.0" type="button"><span class="flag">🇿🇦</span><span>남아공</span><span class="country-count">0건</span></button>
      <button class="country-pin country-sk" data-country-filter="SK" data-anchor-x="52.8" data-anchor-y="35.0" type="button"><span class="flag">🇸🇰</span><span>슬로바키아</span><span class="country-count">0건</span></button>
      <button class="country-pin country-dk" data-country-filter="DK" data-anchor-x="48.8" data-anchor-y="25.0" type="button"><span class="flag">🇩🇰</span><span>덴마크</span><span class="country-count">0건</span></button>
      <button class="country-pin country-my" data-country-filter="MY" data-anchor-x="76.5" data-anchor-y="63.0" type="button"><span class="flag">🇲🇾</span><span>말레이시아</span><span class="country-count">0건</span></button>
      <button class="country-pin country-th" data-country-filter="TH" data-anchor-x="77.5" data-anchor-y="57.0" type="button"><span class="flag">🇹🇭</span><span>태국</span><span class="country-count">0건</span></button>
      <button class="country-pin country-sg" data-country-filter="SG" data-anchor-x="78.0" data-anchor-y="65.0" type="button"><span class="flag">🇸🇬</span><span>싱가포르</span><span class="country-count">0건</span></button>
      <button class="country-pin country-other" data-country-filter="OTHER" type="button"><span class="flag">🌐</span><span>기타</span><span class="country-count">0건</span></button>
    </div>
    <div id="country-filter-note" class="country-filter-note">국가를 누르면 해당 국가 기사로 이동합니다. 다시 누르면 해제됩니다.</div>
    <div class="world-map-credit">Map: Wikimedia Commons · CC0</div>
  </section>
  <main><section id="favorites-panel" class="favorites-panel" hidden><div class="favorites-title">★ 중요 기사 <span id="favorite-count"></span></div><div id="favorites-list" class="favorites-list"></div></section><div id="no-results" class="no-results">검색 결과가 없습니다.</div>{panels_html}</main>
  <footer>기사 카드를 누르면 원문으로 이동하며, 읽음한 기사는 회색으로 표시됩니다.</footer>
</div>
<script>

const headerStateKey = "nuclearDailyBriefHeaderCollapsed";
const topbar = document.getElementById("topbar");
const headerToggle = document.getElementById("header-toggle");
function setHeaderCollapsed(collapsed){{
  topbar.classList.toggle("collapsed", collapsed);
  headerToggle.textContent = collapsed ? "설정 펼치기 ▼" : "설정 접기 ▲";
  headerToggle.setAttribute("aria-expanded", String(!collapsed));
  localStorage.setItem(headerStateKey, collapsed ? "1" : "0");
}}
const savedHeaderState = localStorage.getItem(headerStateKey);
setHeaderCollapsed(savedHeaderState === null ? true : savedHeaderState === "1");
headerToggle.addEventListener("click", () => setHeaderCollapsed(!topbar.classList.contains("collapsed")));

function setCategoryGroups(container, collapsed) {{
  container.querySelectorAll(".news-group").forEach(group => {{
    group.classList.toggle("collapsed", collapsed);

    const title = group.querySelector(".group-title");
    if(title) title.setAttribute("aria-expanded", String(!collapsed));

    const arrow = group.querySelector(".group-arrow");
    if(arrow) arrow.textContent = collapsed ? "▼" : "▲";
  }});
}}

document.addEventListener("click", event => {{
  const masterButton = event.target.closest(".group-master-button");
  if(masterButton) {{
    const section = masterButton.closest(".language-section");
    const currentlyCollapsed = masterButton.dataset.collapsed === "true";
    const nextCollapsed = !currentlyCollapsed;

    setCategoryGroups(section, nextCollapsed);
    masterButton.dataset.collapsed = String(nextCollapsed);
    masterButton.textContent = nextCollapsed ? "전체 펼치기 ▼" : "전체 접기 ▲";
    return;
  }}

  const groupTitle = event.target.closest(".group-title");
  if(!groupTitle) return;

  const group = groupTitle.closest(".news-group");
  if(!group) return;

  const collapsed = group.classList.toggle("collapsed");
  const expanded = !collapsed;
  groupTitle.setAttribute("aria-expanded", String(expanded));

  const arrow = groupTitle.querySelector(".group-arrow");
  if(arrow) arrow.textContent = expanded ? "▲" : "▼";
}});
const languageOrderKey = "nuclearDailyBriefLanguageOrder";
const languageOrderButton = document.getElementById("language-order");

function languageOrderLabel(order){{
  return order === "en-ko" ? "영어 → 한글" : "한글 → 영어";
}}

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

  languageOrderButton.dataset.order = order;
  languageOrderButton.textContent = languageOrderLabel(order);
  localStorage.setItem(languageOrderKey, order);
}}

const savedLanguageOrder = localStorage.getItem(languageOrderKey) || "ko-en";
reorderLanguageArticles(savedLanguageOrder);

languageOrderButton.addEventListener("click", () => {{
  const currentOrder = languageOrderButton.dataset.order || "ko-en";
  const nextOrder = currentOrder === "ko-en" ? "en-ko" : "ko-en";
  reorderLanguageArticles(nextOrder);
  renderFavorites();
}});
const readKey = "nuclearDailyBriefReadArticles";
const importantKey = "nuclearDailyBriefImportantArticles";

function loadStoredSet(key){{
  try {{
    const raw = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(raw) ? raw : []);
  }} catch (_error) {{
    return new Set();
  }}
}}

const readArticles = loadStoredSet(readKey);
const importantArticles = loadStoredSet(importantKey);

function saveState(){{
  try {{
    localStorage.setItem(readKey, JSON.stringify([...readArticles]));
    localStorage.setItem(importantKey, JSON.stringify([...importantArticles]));
  }} catch (_error) {{}}
}}
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

  const isDesktop = window.matchMedia("(min-width: 768px)").matches;

  if(isDesktop){{
    const width = Math.min(1280, Math.max(900, Math.floor(screen.availWidth * 0.82)));
    const height = Math.min(920, Math.max(700, Math.floor(screen.availHeight * 0.88)));
    const left = Math.max(0, Math.floor((screen.availWidth - width) / 2));
    const top = Math.max(0, Math.floor((screen.availHeight - height) / 2));

    window.open(
      u,
      "nuclearArticleWindow",
      `popup=yes,width=${{width}},height=${{height}},left=${{left}},top=${{top}},scrollbars=yes,resizable=yes,noopener`
    );
  }} else {{
    window.open(u, "_blank", "noopener");
  }}
}}
document.querySelectorAll(".preview-card").forEach(card=>{{
  applyState(card);
  card.addEventListener("click",e=>{{ if(!e.target.closest(".important-button")) openArticle(card); }});
  card.addEventListener("keydown",e=>{{ if(e.key==="Enter"||e.key===" "){{ e.preventDefault(); openArticle(card); }}}});
  const importantButton = card.querySelector(".important-button");
  if(importantButton){{
    importantButton.addEventListener("click", e => {{
      e.preventDefault();
      e.stopPropagation();

      const u = card.dataset.url;
      if(!u) return;

      if(importantArticles.has(u)){{
        importantArticles.delete(u);
      }} else {{
        importantArticles.add(u);
      }}

      saveState();

      document.querySelectorAll(".preview-card").forEach(cardItem => {{
        if(cardItem.dataset.url === u) applyState(cardItem);
      }});

      renderFavorites();
    }});
  }}

  const translateButton = card.querySelector(".translate-button");
  if(translateButton){{
    translateButton.addEventListener("click", e => {{
      e.stopPropagation();
      const translatedUrl =
        "https://translate.google.com/?sl=en&tl=ko&text=" +
        encodeURIComponent(card.dataset.title) +
        "&op=translate";
      window.open(translatedUrl, "_blank", "noopener");
    }});
  }}
}});
function activePanel(){{ return document.querySelector(".tab-panel.active"); }}
function renderFavorites(){{
  const panel=activePanel(), box=document.getElementById("favorites-panel"), list=document.getElementById("favorites-list"), count=document.getElementById("favorite-count"); list.innerHTML="";
  if(!panel){{box.hidden=true;return;}}
  const cards=[...panel.querySelectorAll(".preview-card")].filter(c=>importantArticles.has(c.dataset.url));
  cards.forEach(card=>{{ const item=document.createElement("div"); item.className="favorite-item"; item.innerHTML=`<div><div class="favorite-publisher">${{card.dataset.publisher}}</div><div class="favorite-headline">${{card.dataset.title}}</div></div><button class="favorite-remove" type="button">★</button>`; item.addEventListener("click",e=>{{if(!e.target.closest(".favorite-remove"))openArticle(card)}}); item.querySelector(".favorite-remove").addEventListener("click",e=>{{e.preventDefault();e.stopPropagation();importantArticles.delete(card.dataset.url);saveState();document.querySelectorAll(".preview-card").forEach(cardItem=>{{if(cardItem.dataset.url===card.dataset.url)applyState(cardItem);}});renderFavorites();}}); list.appendChild(item); }});
  count.textContent=`${{cards.length}}건`; box.hidden=cards.length===0;
}}

let activeCountryFilter = "";

const COUNTRY_NAMES = {{
  US:"미국", KR:"한국", GB:"영국", BG:"불가리아", UA:"우크라이나", AE:"UAE",
  RO:"루마니아", CZ:"체코", PL:"폴란드", SI:"슬로베니아", FI:"핀란드",
  JP:"일본", CA:"캐나다", FR:"프랑스", SE:"스웨덴",
  CN:"중국", IN:"인도", AU:"호주", RU:"러시아", TR:"튀르키예", SA:"사우디",
  ZA:"남아공", NL:"네덜란드", BE:"벨기에", CH:"스위스", OTHER:"기타"
}};

function layoutCountryPins(){{
  const canvas=document.querySelector(".world-map-canvas");
  if(!canvas)return;

  const visible=[...canvas.querySelectorAll(".country-pin")]
    .filter(button=>!button.hidden);

  const kr=visible.find(button=>button.dataset.countryFilter==="KR");
  const other=visible.find(button=>button.dataset.countryFilter==="OTHER");
  const normal=visible
    .filter(button=>button!==kr && button!==other)
    .sort((a,b)=>Number(a.dataset.anchorY||50)-Number(b.dataset.anchorY||50));

  // 3개 안전 레일: 좌 / 중앙 / 우
  const rails=[[],[],[]];

  // 한국은 항상 우측 최상단
  if(kr)rails[2].push(kr);

  const railTargetX=[12,50,88];

  normal.forEach(button=>{{
    const anchorX=Number(button.dataset.anchorX||50);
    let preferred=anchorX<36?0:(anchorX>66?2:1);

    // 선호 레일을 우선하되, 한 열에 몰리면 가장 짧은 레일로 분산
    const scores=rails.map((rail,index)=>
      rail.length*42 + Math.abs(anchorX-railTargetX[index])
      + (index===preferred?-10:0)
    );
    const chosen=scores.indexOf(Math.min(...scores));
    rails[chosen].push(button);
  }});

  // 기타도 겹치지 않도록 현재 가장 짧은 레일의 마지막 슬롯에 배치
  if(other){{
    const lengths=rails.map(rail=>rail.length);
    const chosen=lengths.indexOf(Math.min(...lengths));
    rails[chosen].push(other);
  }}

  const isDesktop=canvas.clientWidth>=700;
  const pinWidth=isDesktop?112:96;
  const rowStep=isDesktop?31:25;
  const topPad=isDesktop?10:8;
  const pinHeight=isDesktop?26:21;
  const maxRows=Math.max(1,...rails.map(rail=>rail.length));

  // 표시 국가가 많아도 세로 슬롯 자체가 부족하지 않도록 지도 높이를 자동 확장
  const minHeight=isDesktop?260:210;
  const neededHeight=topPad+(maxRows-1)*rowStep+pinHeight+10;
  canvas.style.height=`${{Math.max(minHeight,neededHeight)}}px`;

  rails.forEach((rail,railIndex)=>{{
    rail.forEach((button,rowIndex)=>{{
      button.style.top=`${{topPad+rowIndex*rowStep}}px`;
      button.style.bottom="auto";
      button.style.width=`${{pinWidth}}px`;
      button.style.maxWidth=`${{pinWidth}}px`;
      button.style.transform="none";

      if(railIndex===0){{
        button.style.left=isDesktop?"10px":"5px";
        button.style.right="auto";
      }} else if(railIndex===1){{
        button.style.left=`calc(50% - ${{pinWidth/2}}px)`;
        button.style.right="auto";
      }} else {{
        button.style.left="auto";
        button.style.right=isDesktop?"10px":"5px";
      }}
    }});
  }});
}}

function layoutAndRenderCountryMap(){{
  layoutCountryPins();
  requestAnimationFrame(layoutAndRenderCountryMap);
}}

function renderCountryMapConnectors(){{
  const canvas=document.querySelector(".world-map-canvas");
  const svg=document.getElementById("map-connectors");
  if(!canvas||!svg)return;

  const rect=canvas.getBoundingClientRect();
  if(rect.width<=0||rect.height<=0)return;

  svg.setAttribute("viewBox",`0 0 ${{rect.width}} ${{rect.height}}`);
  svg.innerHTML="";

  canvas.querySelectorAll(".country-pin[data-anchor-x][data-anchor-y]").forEach(button=>{{
    if(button.hidden)return;

    const anchorX=rect.width*(Number(button.dataset.anchorX)/100);
    const anchorY=rect.height*(Number(button.dataset.anchorY)/100);

    const buttonRect=button.getBoundingClientRect();
    const bx=buttonRect.left-rect.left+buttonRect.width/2;
    const by=buttonRect.top-rect.top+buttonRect.height/2;

    // 라벨 중심에서 국가 기준점까지 직접 연결합니다.
    const line=document.createElementNS("http://www.w3.org/2000/svg","line");
    line.setAttribute("x1",String(bx));
    line.setAttribute("y1",String(by));
    line.setAttribute("x2",String(anchorX));
    line.setAttribute("y2",String(anchorY));
    line.setAttribute("class","map-connector-line");
    svg.appendChild(line);

    const dot=document.createElementNS("http://www.w3.org/2000/svg","circle");
    dot.setAttribute("cx",String(anchorX));
    dot.setAttribute("cy",String(anchorY));
    dot.setAttribute("r",button.dataset.countryFilter==="KR"?"3.2":"2.5");
    dot.setAttribute("class",button.dataset.countryFilter==="KR"?"map-anchor-dot kr-anchor":"map-anchor-dot");
    svg.appendChild(dot);
  }});
}}

function updateCountryMapCounts(){{
  const panel=activePanel();
  if(!panel)return;

  // 지도에 등록된 국가코드에서 집계표를 자동 생성합니다.
  // 향후 국가를 추가해도 JS 국가목록을 따로 수정하지 않아도 됩니다.
  const counts={{OTHER:0}};
  document.querySelectorAll("[data-country-filter]").forEach(button=>{{
    const code=button.dataset.countryFilter;
    if(code && code!=="ALL" && !Object.prototype.hasOwnProperty.call(counts,code)){{
      counts[code]=0;
    }}
  }});
  const cards=[...panel.querySelectorAll(".preview-card")];

  cards.forEach(card=>{{
    const code=card.dataset.country||"OTHER";
    if(!Object.prototype.hasOwnProperty.call(counts,code))counts.OTHER++;
    else counts[code]++;
  }});

  document.querySelectorAll("[data-country-filter]").forEach(button=>{{
    const code=button.dataset.countryFilter;
    const count=counts[code]||0;
    const countNode=button.querySelector(".country-count")||button.querySelector(".chip-count");
    if(countNode)countNode.textContent=`${{count}}건`;
    // 한국은 국가별 기사 영역에서 항상 첫 번째로 표시합니다.
    button.hidden=(!["KR","OTHER"].includes(code) && count===0);
    button.classList.toggle("active",activeCountryFilter===code);
  }});

  const total=cards.length;
  const countryTotal=Object.values(counts).reduce((sum,value)=>sum+value,0);
  const summary=document.getElementById("world-map-summary");
  if(summary)summary.textContent=`현재 탭 전체 ${{total}}건 · 국가 합계 ${{countryTotal}}건`;
  if(countryTotal!==total){{
    console.warn("[COUNTRY COUNT MISMATCH]", {{total, countryTotal, counts}});
  }}

  const allButton=document.getElementById("country-all");
  if(allButton)allButton.classList.toggle("active",!activeCountryFilter);

  requestAnimationFrame(layoutAndRenderCountryMap);
}}

function expandVisibleCountryGroups(){{
  const panel=activePanel();
  if(!panel)return null;

  let firstVisibleCard=null;
  panel.querySelectorAll(".news-group").forEach(group=>{{
    const visibleCards=[...group.querySelectorAll(".preview-card")].filter(card=>card.style.display!=="none");
    if(visibleCards.length){{
      group.classList.remove("collapsed");
      const title=group.querySelector(".group-title");
      if(title)title.setAttribute("aria-expanded","true");
      const arrow=group.querySelector(".group-arrow");
      if(arrow)arrow.textContent="▲";
      if(!firstVisibleCard)firstVisibleCard=visibleCards[0];
    }}
  }});
  return firstVisibleCard;
}}

function setCountryFilter(code){{
  activeCountryFilter=activeCountryFilter===code?"":code;
  filterArticles();
  updateCountryMapCounts();

  const note=document.getElementById("country-filter-note");
  if(activeCountryFilter){{
    const name=COUNTRY_NAMES[activeCountryFilter]||activeCountryFilter;
    if(note)note.textContent=`${{name}} 관련 기사만 표시 중 · 기사 위치로 이동합니다.`;
    const firstVisibleCard=expandVisibleCountryGroups();
    if(firstVisibleCard){{
      setTimeout(()=>firstVisibleCard.scrollIntoView({{behavior:"smooth",block:"center"}}),80);
    }}
  }} else {{
    if(note)note.textContent="국가를 누르면 해당 국가 기사로 이동합니다.";
  }}
}}

document.querySelectorAll("[data-country-filter]").forEach(button=>{{
  button.addEventListener("click",()=>setCountryFilter(button.dataset.countryFilter));
}});

const countryAllButton=document.getElementById("country-all");
if(countryAllButton){{
  countryAllButton.addEventListener("click",()=>{{
    activeCountryFilter="";
    filterArticles();
    updateCountryMapCounts();
    const note=document.getElementById("country-filter-note");
    if(note)note.textContent="국가를 누르면 해당 국가 기사로 이동합니다.";
  }});
}}

function filterArticles(){{
  const q=document.getElementById("article-search").value.trim().toLowerCase();
  const panel=activePanel();
  if(!panel)return;

  let total=0;

  panel.querySelectorAll(".news-group").forEach(group=>{{
    const stack=group.querySelector(".article-stack");
    if(!stack)return;

    const cards=[...stack.querySelectorAll(".preview-card")];
    let visible=[];

    cards.forEach(card=>{{
      const matchesSearch=!q||card.dataset.search.includes(q); const matchesCountry=!activeCountryFilter||card.dataset.country===activeCountryFilter; const show=matchesSearch&&matchesCountry;
      card.style.display=show?"":"none";
      if(show){{
        visible.push(card);
        total++;
      }}
    }});

    if(q||activeCountryFilter){{
      visible.sort((a,b)=>Number(b.dataset.published||0)-Number(a.dataset.published||0));
      visible.forEach((card,index)=>{{
        stack.appendChild(card);
        const number=card.querySelector(".article-number");
        if(number)number.textContent=String(index+1);
      }});
      group.style.display=visible.length?"":"none";
    }} else {{
      group.style.display="";
    }}
  }});

  if(!q&&!activeCountryFilter){{
    const currentOrder=languageOrderButton?.dataset.order||localStorage.getItem(languageOrderKey)||"ko-en";
    reorderLanguageArticles(currentOrder);
  }}

  document.getElementById("no-results").style.display=(q||activeCountryFilter)&&total===0?"block":"none";
}}
function refreshActivePeriodUI(){{
  filterArticles();
  updateCountryMapCounts();
  renderFavorites();
  requestAnimationFrame(layoutAndRenderCountryMap);
}}

function activatePanel(panel, button=null){{
  document.querySelectorAll(".tab-button").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(x=>x.classList.remove("active"));
  if(button)button.classList.add("active");
  panel.classList.add("active");
  refreshActivePeriodUI();
}}
document.querySelectorAll(".tab-button").forEach(button => {{
  button.addEventListener("click", event => {{
    event.preventDefault();
    const label = button.getAttribute("data-tab");
    const panel = document.getElementById("tab-" + label);
    if(!panel) return;

    document.querySelectorAll(".tab-button").forEach(item => item.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(item => item.classList.remove("active"));

    button.classList.add("active");
    panel.classList.add("active");

    const periodDate = panel.getAttribute("data-report-date");
    if(periodDate && archiveInput){{
      archiveInput.value = periodDate;
      updateArchiveDateDisplay(periodDate);
    }}

    if(typeof refreshActivePeriodUI === "function") refreshActivePeriodUI();

    window.scrollTo({{ top: 0, behavior: "smooth" }});
  }});
}});
const archiveCutoff="{generated_at:%Y-%m-%d}";
const archiveDates=[...document.querySelectorAll(".archive-panel")]
  .map(x=>x.dataset.archiveDate)
  .filter(value=>value && value<=archiveCutoff)
  .sort();

const archiveInput=document.getElementById("archive-date");
const archiveDateDisplay=document.getElementById("archive-date-display");

function formatArchiveDateDisplay(value){{
  return value ? value.replaceAll("-", ".") : "";
}}

function updateArchiveDateDisplay(value){{
  if(archiveDateDisplay) archiveDateDisplay.textContent=formatArchiveDateDisplay(value);
}}


if(archiveInput && archiveDates.length){{
  archiveInput.min=archiveDates[0];
  archiveInput.max=archiveDates[archiveDates.length-1];
  archiveInput.value=archiveDates[archiveDates.length-1];
  updateArchiveDateDisplay(archiveInput.value);
}}

const archiveDateControl=document.querySelector(".date-control");
if(archiveDateControl && archiveInput){{
  archiveDateControl.addEventListener("click", (event)=>{{
    // input 자체의 기본 동작은 그대로 두고, 나머지 영역 클릭 시 picker를 엽니다.
    if(event.target===archiveInput) return;
    if(typeof archiveInput.showPicker==="function"){{
      try{{ archiveInput.showPicker(); }}catch(_error){{ archiveInput.focus(); }}
    }} else {{
      archiveInput.focus();
      archiveInput.click();
    }}
  }});
}}

function openArchiveDate(value){{
  if(!value) return false;

  const panel=document.getElementById("archive-" + value);
  if(!panel){{
    alert("선택한 날짜의 저장된 기사가 없습니다.");
    return false;
  }}

  document.querySelectorAll(".tab-button").forEach(item=>item.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(item=>item.classList.remove("active"));

  panel.classList.add("active");

  if(archiveInput){{
    archiveInput.value=value;
    updateArchiveDateDisplay(value);
  }}
  if(typeof refreshActivePeriodUI==="function") refreshActivePeriodUI();

  panel.scrollIntoView({{behavior:"smooth", block:"start"}});
  return true;
}}

if(archiveInput){{
  archiveInput.addEventListener("change", ()=>{{
    const value=archiveInput.value;
    if(!value) return;

    const opened=openArchiveDate(value);
    if(!opened){{
      // 선택한 날짜가 archive에 없으면 표시값은 선택 전 상태로 되돌립니다.
      const activeArchive=document.querySelector(".archive-panel.active");
      if(activeArchive && activeArchive.dataset.archiveDate){{
        archiveInput.value=activeArchive.dataset.archiveDate;
        updateArchiveDateDisplay(archiveInput.value);
      }}
    }}
  }});
}}
window.addEventListener("resize",()=>requestAnimationFrame(layoutAndRenderCountryMap));
const worldMapImage=document.querySelector(".world-map-image");
if(worldMapImage)worldMapImage.addEventListener("load",()=>requestAnimationFrame(layoutAndRenderCountryMap));
document.getElementById("article-search").addEventListener("input",filterArticles);
updateCountryMapCounts();

filterArticles();renderFavorites();
</script>
</body>
</html>
'''



def main() -> int:
    now = datetime.now(KST)

    # 실제 GitHub Actions가 예약시각보다 몇 분 늦게 시작돼도
    # 화면에는 05/15/25/35/45/55분 기준시각으로 표시합니다.
    scheduled_minutes = (5, 15, 25, 35, 45, 55)
    display_minute = max(
        (minute for minute in scheduled_minutes if minute <= now.minute),
        default=55,
    )
    display_hour = now.hour
    display_date = now

    if now.minute < 5:
        display_date = now - timedelta(hours=1)
        display_hour = display_date.hour

    display_updated_at = display_date.replace(
        hour=display_hour,
        minute=display_minute,
        second=0,
        microsecond=0,
    )

    periods = brief_periods(now)
    today_start, today_end = periods["금일"]

    for label in ("전일", "금일", "익일"):
        start, end = periods[label]
        print(
            f"[REPORT WINDOW {label}] "
            f"{start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}"
        )

    print(
        "Current KST window:",
        f"{today_start:%Y-%m-%d %H:%M} ~ {today_end:%Y-%m-%d %H:%M}"
    )

    previous_urls = load_previous_urls()

    # 전일/금일/익일 전체 범위를 한 번에 수집
    overall_start = min(start for start, _end in periods.values())
    overall_end = max(end for _start, end in periods.values())

    print(
        "Fetch once for all 3 periods:",
        f"{overall_start:%Y-%m-%d %H:%M} ~ {overall_end:%Y-%m-%d %H:%M}"
    )

    next_start, next_end = periods["익일"]
    elapsed_hours = max(
        0.0,
        (min(now, next_end) - next_start).total_seconds() / 3600,
    )
    print(
        f"[익일 WINDOW] {next_start:%Y-%m-%d %H:%M} ~ "
        f"{next_end:%Y-%m-%d %H:%M} / elapsed={elapsed_hours:.1f}h"
    )

    fetched = fetch_articles(overall_start, overall_end)
    print(f"Fetched raw articles: {len(fetched)}")

    # 네트워크 재호출 없이 메모리에서 전일/금일/익일로 분리
    articles_by_period = {
        label: select_articles_for_period(fetched, start, end)
        for label, (start, end) in periods.items()
    }

    for label in ("전일", "금일", "익일"):
        items = articles_by_period.get(label, [])
        ko_count = sum(1 for article in items if article.language == "ko")
        en_count = sum(1 for article in items if article.language == "en")
        other_count = sum(
            1 for article in items
            if detect_article_country(article) == "OTHER"
        )
        print(
            f"[PERIOD {label}] total={len(items)} / ko={ko_count} / en={en_count} "
            f"/ country_other={other_count} / RAW=ON / dedup=ON / limit=OFF"
        )

    # 과거 실행분 + 현재 수집분을 날짜별 archive에 먼저 누적
    archive = update_archive(
        load_archive(),
        periods,
        articles_by_period,
        now,
    )

    # 누적된 archive를 전일/금일/익일 탭에 다시 반영
    for label in ("전일", "금일", "익일"):
        start, end = periods[label]
        archive_key = end.strftime("%Y-%m-%d")
        merged_items: list[Article] = []

        for raw in archive.get(archive_key, {}).get("articles", []):
            article = article_from_dict(raw)
            if article is not None and start <= article.published < end:
                merged_items.append(article)

        articles_by_period[label] = sorted(
            merged_items,
            key=lambda article: -article.published.timestamp(),
        )

        print(
            f"[PERIOD ACCUMULATED {label}] total={len(articles_by_period[label])}"
        )

    # 실제 화면에 표시할 기사만 대상으로 원문 대표 이미지/설명을 병렬 보완
    enrich_article_metadata(articles_by_period)

    # 과거 archive에서 비어 있던 언론사명도 URL 기준으로 복원
    for items in articles_by_period.values():
        for article in items:
            ensure_article_display_metadata(article)

    # 대표 이미지를 로컬 파일로 저장해 외부 이미지 차단/로딩 실패를 줄입니다.
    cache_article_thumbnails(articles_by_period)
    cleanup_old_thumbnails(now)

    current_urls = {
        article.link
        for items in articles_by_period.values()
        for article in items
    }
    new_urls = current_urls - previous_urls if previous_urls else set()

    # 화면용으로 보완된 이미지/설명 정보도 누적 archive에 반영
    archive = update_archive(
        archive,
        periods,
        articles_by_period,
        now,
    )

    # 10분 예약 실행에서는 Backfill을 끄고,
    # 필요할 때 수동 실행(workflow_dispatch)에서만 채웁니다.
    if SKIP_BACKFILL:
        print("SKIP_BACKFILL=1: historical archive backfill skipped")
    else:
        archive = backfill_missing_archive_dates(archive, now)

    save_archive(archive)

    available_archive_dates = sorted(
        key for key in archive.keys()
        if key <= now.strftime("%Y-%m-%d")
    )
    print(
        f"[ARCHIVE AVAILABLE] {len(available_archive_dates)} date(s) / "
        f"oldest={available_archive_dates[0] if available_archive_dates else '-'} / "
        f"latest={available_archive_dates[-1] if available_archive_dates else '-'}"
    )

    OUTPUT.write_text(
        build_html(
            periods,
            articles_by_period,
            display_updated_at,
            new_urls,
            archive,
        ),
        encoding="utf-8",
    )

    save_current_urls(current_urls, now)

    total = sum(len(items) for items in articles_by_period.values())
    print(
        f"Generated {OUTPUT}: {total} news articles across 3 periods; "
        f"{len(archive)} archive dates"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
