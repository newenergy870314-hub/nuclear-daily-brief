from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
from dateutil import parser as date_parser

KST = ZoneInfo("Asia/Seoul")
OUTPUT = Path("index.html")
MAX_PER_LANGUAGE = 20

GROUPS = [
    ("현대건설", [
        "현대건설 원전", "현대건설 원자력",
        '"Hyundai Engineering & Construction" nuclear',
        '"Hyundai E&C" nuclear', "HDEC nuclear",
    ]),
    ("Fermi America", [
        '"Fermi America"', '"Project Matador"', "HyperGrid nuclear",
        '"Fermi America" AP1000', "Amarillo nuclear", '"Carson County" nuclear',
    ]),
    ("Westinghouse", [
        "Westinghouse nuclear", "AP1000", "AP300",
        '"Westinghouse Electric Company"',
    ]),
    ("Holtec", [
        "Holtec nuclear", '"Holtec International"', "SMR-300",
        "Palisades nuclear", '"Oyster Creek" SMR',
    ]),
    ("국내 원전", [
        "원전", "원자력", "원자력발전", "원자력발전소",
        "대형원전", "신규 원전", "원전 건설", "원전 수출",
        "한수원", "KHNP", "한전 원전", "KEPCO nuclear",
        "새울원전", "신한울원전", "원전 공급망",
    ]),
    ("SMR 및 차세대원자로", [
        "SMR nuclear", "소형모듈원자로", '"Small Modular Reactor"',
        '"Advanced Reactor"', '"Advanced Nuclear"', "Microreactor",
    ]),
    ("글로벌 원전", [
        '"Nuclear Power"', '"Nuclear Energy"', '"Nuclear Power Plant"',
        '"Nuclear Construction"', '"Nuclear Project"',
        '"Nuclear New Build"', '"New Nuclear Build"',
    ]),
]


@dataclass
class Article:
    title: str
    link: str
    published: datetime
    language: str
    group: str
    publisher: str
    image: str


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
    match = re.match(r"^(.*)\s+-\s+([^-]{2,80})$", raw_title)
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
    text = title.lower()
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return " ".join(text.split())


def is_duplicate(article: Article, selected: list[Article]) -> bool:
    key = normalized(article.title)
    for existing in selected:
        other = normalized(existing.title)
        if key == other:
            return True
        if SequenceMatcher(None, key, other).ratio() >= 0.86:
            return True
    return False


def parse_entry(entry, language: str, group: str) -> Article | None:
    published_raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if not published_raw:
        return None

    try:
        published = date_parser.parse(published_raw).astimezone(KST)
    except Exception:
        return None

    title, publisher_from_title = split_title_and_publisher(
        getattr(entry, "title", "")
    )
    link = getattr(entry, "link", "").strip()
    if not title or not link:
        return None

    source = getattr(entry, "source", {})
    publisher = source.get("title", "") if isinstance(source, dict) else ""
    publisher = publisher.strip() or publisher_from_title or "출처 확인"

    return Article(
        title=title,
        link=link,
        published=published,
        language=language,
        group=group,
        publisher=publisher,
        image=extract_image(entry),
    )


def collect(start: datetime, end: datetime) -> list[Article]:
    found: list[Article] = []

    for group, queries in GROUPS:
        for language in ("ko", "en"):
            for query in queries:
                feed = feedparser.parse(google_news_url(query, language))
                for entry in feed.entries:
                    article = parse_entry(entry, language, group)
                    if article and start <= article.published < end:
                        found.append(article)

    priority = {name: i for i, (name, _) in enumerate(GROUPS)}
    found.sort(key=lambda a: (priority[a.group], -a.published.timestamp()))

    selected: list[Article] = []
    language_counts = {"ko": 0, "en": 0}

    for article in found:
        if language_counts[article.language] >= MAX_PER_LANGUAGE:
            continue
        if is_duplicate(article, selected):
            continue
        selected.append(article)
        language_counts[article.language] += 1

    return selected


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def render_card(article: Article) -> str:
    if article.image:
        thumbnail = (
            f'<img src="{escape(article.image)}" alt="" loading="lazy" '
            'referrerpolicy="no-referrer">'
        )
    else:
        thumbnail = '<div class="fallback">NUCLEAR<br>DAILY BRIEF</div>'

    badge = "KR" if article.language == "ko" else "EN"

    return f'''
<a class="news-card" href="{escape(article.link)}" target="_blank" rel="noopener">
  <div class="thumbnail">{thumbnail}</div>
  <div class="card-body">
    <div class="source-line">
      <span class="badge">{badge}</span>
      <span class="publisher">{escape(article.publisher)}</span>
    </div>
    <h3>{escape(article.title)}</h3>
    <div class="open-text">기사 원문 보기 →</div>
  </div>
</a>
'''


def render_section(group: str, articles: list[Article]) -> str:
    if not articles:
        return ""

    cards = "".join(render_card(article) for article in articles)
    return f'''
<section>
  <div class="section-title">{escape(group)}</div>
  <div class="card-list">{cards}</div>
</section>
'''


def build_html(start: datetime, end: datetime, articles: list[Article]) -> str:
    grouped: dict[str, list[Article]] = {name: [] for name, _ in GROUPS}
    for article in articles:
        grouped[article.group].append(article)

    sections = "".join(
        render_section(group, grouped[group]) for group, _ in GROUPS
    )

    count_ko = sum(article.language == "ko" for article in articles)
    count_en = sum(article.language == "en" for article in articles)

    return f'''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0d2340">
  <title>원자력 주요기사 Daily Brief</title>
  <style>
    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: #e9eef4;
      color: #172033;
      font-family: Arial, "Malgun Gothic", sans-serif;
    }}

    .page {{
      width: min(100%, 540px);
      min-height: 100vh;
      margin: 0 auto;
      background: #f7f9fc;
      box-shadow: 0 0 28px rgba(15, 23, 42, 0.08);
    }}

    .hero {{
      padding: 28px 20px 23px;
      color: white;
      background:
        radial-gradient(circle at top right, rgba(77, 159, 255, 0.28), transparent 36%),
        linear-gradient(135deg, #091f3b, #14518a);
    }}

    .eyebrow {{
      font-size: 12px;
      letter-spacing: 0.12em;
      opacity: 0.78;
    }}

    h1 {{
      margin: 7px 0 14px;
      font-size: 25px;
      line-height: 1.25;
    }}

    .brief-meta {{
      font-size: 12px;
      line-height: 1.65;
      opacity: 0.9;
    }}

    main {{
      padding: 18px 13px 38px;
    }}

    section {{
      margin-bottom: 25px;
    }}

    .section-title {{
      display: inline-block;
      margin: 0 0 11px 2px;
      padding: 7px 11px;
      border-radius: 8px;
      background: #ffd43b;
      color: #172033;
      font-size: 15px;
      font-weight: 800;
      box-shadow: 0 4px 10px rgba(251, 191, 36, 0.18);
    }}

    .card-list {{
      display: grid;
      gap: 11px;
    }}

    .news-card {{
      display: grid;
      grid-template-columns: 118px 1fr;
      min-height: 112px;
      overflow: hidden;
      color: inherit;
      background: white;
      border: 1px solid #dbe3ec;
      border-radius: 13px;
      text-decoration: none;
      box-shadow: 0 5px 14px rgba(15, 23, 42, 0.07);
    }}

    .news-card:active {{
      transform: scale(0.992);
    }}

    .thumbnail {{
      width: 118px;
      min-height: 112px;
      background: #dbe3ec;
    }}

    .thumbnail img {{
      display: block;
      width: 100%;
      height: 100%;
      min-height: 112px;
      object-fit: cover;
    }}

    .fallback {{
      display: grid;
      place-items: center;
      width: 100%;
      height: 100%;
      min-height: 112px;
      color: white;
      background: linear-gradient(135deg, #123f70, #0a223d);
      font-size: 11px;
      font-weight: 800;
      line-height: 1.5;
      text-align: center;
    }}

    .card-body {{
      display: flex;
      flex-direction: column;
      min-width: 0;
      padding: 11px 12px 10px;
    }}

    .source-line {{
      display: flex;
      align-items: center;
      min-width: 0;
      font-size: 11px;
      color: #6b7280;
    }}

    .badge {{
      flex: 0 0 auto;
      margin-right: 7px;
      padding: 2px 6px;
      border-radius: 5px;
      color: #164a7b;
      background: #e7f0fa;
      font-weight: 800;
    }}

    .publisher {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    h3 {{
      display: -webkit-box;
      margin: 7px 0 8px;
      overflow: hidden;
      font-size: 14px;
      line-height: 1.42;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
    }}

    .open-text {{
      margin-top: auto;
      color: #17639f;
      font-size: 11px;
      font-weight: 700;
    }}

    .empty {{
      padding: 30px 18px;
      color: #6b7280;
      background: white;
      border: 1px solid #dbe3ec;
      border-radius: 12px;
      text-align: center;
    }}

    footer {{
      padding: 0 15px 28px;
      color: #7b8493;
      font-size: 11px;
      text-align: center;
    }}

    @media (max-width: 380px) {{
      .news-card {{ grid-template-columns: 102px 1fr; }}
      .thumbnail {{ width: 102px; }}
      h1 {{ font-size: 22px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="hero">
      <div class="eyebrow">NEW ENERGY · NUCLEAR NEWS</div>
      <h1>원자력 주요기사<br>Daily Brief</h1>
      <div class="brief-meta">
        조회기간: {start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)<br>
        한글 {count_ko}건 · 영문 {count_en}건
      </div>
    </header>

    <main>
      {sections or '<div class="empty">조회기간 내 확인된 관련 기사가 없습니다.</div>'}
    </main>

    <footer>카드를 누르면 기사 원문으로 이동합니다.</footer>
  </div>
</body>
</html>
'''


def main() -> int:
    start, end = period(datetime.now(KST))
    articles = collect(start, end)
    OUTPUT.write_text(build_html(start, end, articles), encoding="utf-8")
    print(f"Generated {OUTPUT}: {len(articles)} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
