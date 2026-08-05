from __future__ import annotations

import html
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
MAX_PER_GROUP_PER_LANGUAGE = 12

GROUPS = [
    ("현대건설", [
        "현대건설 원전", "현대건설 원자력",
        '"Hyundai Engineering & Construction" nuclear',
        '"Hyundai E&C" nuclear', "HDEC nuclear",
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


def is_duplicate(article: Article, selected: list[Article]) -> bool:
    key = normalized(article.title)
    for existing in selected:
        other = normalized(existing.title)
        if key == other or SequenceMatcher(None, key, other).ratio() >= 0.85:
            return True
    return False


def is_news_source(publisher: str, source_url: str) -> bool:
    publisher_lower = publisher.lower()
    host = urlparse(source_url).netloc.lower()

    if any(keyword in publisher_lower for keyword in BLOCKED_SOURCE_KEYWORDS):
        return False
    if any(keyword in host for keyword in BLOCKED_HOST_KEYWORDS):
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

    if not title or not link or not is_news_source(publisher, source_url):
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
            for article in found:
                if len(selected_group) >= MAX_PER_GROUP_PER_LANGUAGE:
                    break
                if is_duplicate(article, all_selected + selected_group):
                    continue
                selected_group.append(article)

            all_selected.extend(selected_group)

    return all_selected


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def render_card(article: Article, number: int) -> str:
    if article.image:
        image_html = (
            f'<img src="{escape(article.image)}" alt="" '
            'loading="lazy" referrerpolicy="no-referrer">'
        )
    else:
        image_html = '<div class="no-image">NUCLEAR<br>NEWS</div>'

    return f'''
<a class="preview-card" data-url="{escape(article.link)}"
   href="{escape(article.link)}" target="_blank" rel="noopener">
  <div class="article-number">{number}</div>
  <div class="preview-copy">
    <div class="publisher">{escape(article.publisher)}</div>
    <div class="headline">{escape(article.title)}</div>
    <div class="status-line">
      <span class="unread-label">미확인</span>
      <span class="read-label">확인함</span>
    </div>
  </div>
  <div class="preview-image">{image_html}</div>
</a>
'''


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


def render_group_unified(group: str, articles: list[Article]) -> str:
    if not articles:
        return ""

    korean_articles = [
        article for article in articles if article.language == "ko"
    ]
    english_articles = [
        article for article in articles if article.language == "en"
    ]

    ordered_articles = korean_articles + english_articles

    cards = "".join(
        render_card(article, index)
        for index, article in enumerate(ordered_articles, start=1)
    )

    return f"""
<section class="news-group">
  <div class="group-title">{escape(group)}</div>
  <div class="article-stack">{cards}</div>
</section>
"""



def build_html(start: datetime, end: datetime, articles: list[Article]) -> str:
    grouped: dict[str, list[Article]] = {name: [] for name, _ in GROUPS}
    for article in articles:
        grouped[article.group].append(article)

    news_sections = "".join(
        render_group_unified(group, grouped[group])
        for group, _ in GROUPS
    )

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#b2c7d9">
<title>원자력 주요기사 Daily Brief</title>
<style>
* {{ box-sizing: border-box; }}

body {{
  margin: 0;
  background: #b2c7d9;
  color: #111827;
  font-family: Arial, "Malgun Gothic", sans-serif;
}}

.phone {{
  width: min(100%, 520px);
  min-height: 100vh;
  margin: 0 auto;
  background: #b2c7d9;
}}

.topbar {{
  position: sticky;
  top: 0;
  z-index: 20;
  padding: 15px 16px 13px;
  background: rgba(178, 199, 217, 0.97);
  border-bottom: 1px solid rgba(17, 24, 39, 0.08);
  backdrop-filter: blur(8px);
}}

.topbar h1 {{
  margin: 0;
  font-size: 19px;
  line-height: 1.25;
  font-weight: 800;
}}

.period {{
  margin-top: 5px;
  color: #344054;
  font-size: 11px;
  line-height: 1.45;
}}

main {{
  padding: 12px 12px 34px;
}}

.language-section {{
  margin-bottom: 30px;
}}

.language-title {{
  margin: 4px 0 15px;
  padding: 10px 12px;
  color: white;
  background: #23395d;
  border-radius: 9px;
  font-size: 16px;
  font-weight: 800;
  text-align: left;
}}

.news-group {{
  margin-bottom: 20px;
}}

.group-title {{
  display: block;
  width: fit-content;
  max-width: 100%;
  margin: 0 0 8px 0;
  padding: 8px 11px;
  background: #fee500;
  border-radius: 4px 11px 11px 11px;
  font-size: 14px;
  font-weight: 800;
  text-align: left;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.12);
}}

.article-stack {{
  display: grid;
  gap: 7px;
}}

.preview-card {{
  position: relative;
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) 82px;
  min-height: 88px;
  overflow: hidden;
  color: inherit;
  background: white;
  border: 1px solid rgba(17, 24, 39, 0.08);
  border-radius: 10px;
  text-decoration: none;
  box-shadow: 0 1px 3px rgba(17, 24, 39, 0.15);
  transition: opacity 0.15s ease, background 0.15s ease;
}}

.preview-card.read {{
  background: #eef1f4;
  opacity: 0.72;
}}

.article-number {{
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 12px;
  color: #344054;
  font-size: 12px;
  font-weight: 800;
}}

.preview-copy {{
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 10px 9px 8px 0;
}}

.publisher {{
  overflow: hidden;
  color: #667085;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}}

.lang-badge {{
  display: inline-block;
  margin-right: 5px;
  padding: 1px 4px;
  border-radius: 4px;
  color: white;
  background: #23395d;
  font-size: 9px;
  font-weight: 800;
}}

.headline {{
  display: -webkit-box;
  margin-top: 5px;
  overflow: hidden;
  color: #101828;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.38;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}}

.status-line {{
  margin-top: auto;
  padding-top: 5px;
  font-size: 10px;
}}

.unread-label {{
  color: #17639f;
  font-weight: 700;
}}

.read-label {{
  display: none;
  color: #667085;
  font-weight: 700;
}}

.preview-card.read .unread-label {{
  display: none;
}}

.preview-card.read .read-label {{
  display: inline;
}}

.preview-image {{
  width: 82px;
  min-height: 88px;
  background: #d0d5dd;
}}

.preview-image img {{
  display: block;
  width: 100%;
  height: 100%;
  min-height: 88px;
  object-fit: cover;
}}

.no-image {{
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  min-height: 88px;
  color: white;
  background: linear-gradient(135deg, #173b67, #0b213d);
  font-size: 9px;
  font-weight: 800;
  line-height: 1.45;
  text-align: center;
}}

.empty {{
  padding: 22px 15px;
  background: white;
  border-radius: 10px;
  text-align: center;
  color: #667085;
}}

footer {{
  padding: 0 12px 28px;
  color: #475467;
  font-size: 10px;
  text-align: center;
}}

@media (max-width: 380px) {{
  .preview-card {{
    grid-template-columns: 24px minmax(0, 1fr) 72px;
  }}

  .preview-image {{
    width: 72px;
  }}

  .lang-badge {{
  display: inline-block;
  margin-right: 5px;
  padding: 1px 4px;
  border-radius: 4px;
  color: white;
  background: #23395d;
  font-size: 9px;
  font-weight: 800;
}}

.headline {{
    font-size: 12px;
  }}
}}
</style>
</head>
<body>
<div class="phone">
  <header class="topbar">
    <h1>원자력 주요기사 Daily Brief</h1>
    <div class="period">
      {start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)
    </div>
  </header>

  <main>
    <div class="language-section">
      <div class="language-title">뉴스기사</div>
      {news_sections}
    </div>
    {"" if news_sections else '<div class="empty">조회기간 내 뉴스 기사가 없습니다.</div>'}
  </main>

  <footer>기사 카드를 누르면 원문으로 이동하며, 확인한 기사는 회색으로 표시됩니다.</footer>
</div>

<script>
const storageKey = "nuclearDailyBriefReadArticles";
const readArticles = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]"));

document.querySelectorAll(".preview-card").forEach((card) => {{
  const url = card.dataset.url;

  if (readArticles.has(url)) {{
    card.classList.add("read");
  }}

  card.addEventListener("click", () => {{
    readArticles.add(url);
    localStorage.setItem(storageKey, JSON.stringify([...readArticles]));
    card.classList.add("read");
  }});
}});
</script>
</body>
</html>
'''


def main() -> int:
    start, end = period(datetime.now(KST))
    articles = collect(start, end)
    OUTPUT.write_text(build_html(start, end, articles), encoding="utf-8")
    print(f"Generated {OUTPUT}: {len(articles)} news articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
