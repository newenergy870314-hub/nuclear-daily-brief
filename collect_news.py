from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

import feedparser
from dateutil import parser as date_parser
from zoneinfo import ZoneInfo

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


def period(now: datetime) -> tuple[datetime, datetime]:
    """Return the Daily Brief interval in KST."""
    now = now.astimezone(KST)
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)

    # When manually run before 06:00, use the previous 06:00 as the endpoint.
    if now < end:
        end -= timedelta(days=1)

    weekday = end.weekday()  # Monday=0
    if weekday == 0:
        start = end - timedelta(days=3)
    elif weekday in (1, 2, 3, 4):
        start = end - timedelta(days=1)
    else:
        # A manual weekend run uses the most recent Friday 06:00 endpoint.
        days_back = 1 if weekday == 5 else 2
        end -= timedelta(days=days_back)
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


def clean_title(title: str) -> str:
    # Google News often appends " - Publisher".
    return re.sub(r"\s+-\s+[^-]{2,80}$", "", title).strip()


def normalized(title: str) -> str:
    text = clean_title(title).lower()
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return " ".join(text.split())


def is_duplicate(article: Article, selected: list[Article]) -> bool:
    key = normalized(article.title)
    for existing in selected:
        other = normalized(existing.title)
        if key == other:
            return True
        # Similar headlines from the same announcement/republication.
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

    title = clean_title(html.unescape(getattr(entry, "title", "")).strip())
    link = getattr(entry, "link", "").strip()
    if not title or not link:
        return None

    return Article(title=title, link=link, published=published,
                   language=language, group=group)


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

    # Priority by group, then newest first.
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


def render_section(group: str, articles: Iterable[Article]) -> str:
    items = list(articles)
    if not items:
        return ""
    rows = []
    for article in items:
        title = escape(article.title)
        link = escape(article.link)
        # English headlines are left in the original language to avoid
        # unreliable machine translation without a paid AI API.
        rows.append(f'<li><a href="{link}" target="_blank" rel="noopener">{title}</a></li>')
    return f"<section><h2>{escape(group)}</h2><ul>{''.join(rows)}</ul></section>"


def build_html(start: datetime, end: datetime, articles: list[Article]) -> str:
    grouped: dict[str, list[Article]] = {name: [] for name, _ in GROUPS}
    for article in articles:
        grouped[article.group].append(article)

    sections = "\n".join(render_section(group, grouped[group]) for group, _ in GROUPS)
    count_ko = sum(a.language == "ko" for a in articles)
    count_en = sum(a.language == "en" for a in articles)

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>원자력 주요기사 Daily Brief</title>
  <style>
    body {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 70px;
            font-family: Arial, "Noto Sans KR", sans-serif; line-height: 1.6;
            color: #1f2937; background: #f5f7fa; }}
    main {{ background: white; padding: 30px; border: 1px solid #d1d5db;
            border-radius: 12px; }}
    h1 {{ margin-top: 0; font-size: 28px; }}
    h2 {{ margin-top: 30px; padding-bottom: 7px; border-bottom: 2px solid #111827;
          font-size: 20px; }}
    .meta {{ padding: 14px 16px; background: #f3f4f6; border-radius: 8px; }}
    ul {{ padding-left: 22px; }}
    li {{ margin: 10px 0; }}
    a {{ color: #075985; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    footer {{ margin-top: 28px; color: #6b7280; font-size: 13px; }}
  </style>
</head>
<body>
<main>
  <h1>원자력 주요기사 Daily Brief</h1>
  <div class="meta">
    <div><strong>조회기간:</strong> {start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)</div>
    <div><strong>한글기사:</strong> {count_ko}건 · <strong>영문기사:</strong> {count_en}건</div>
  </div>
  {sections or '<p>조회기간 내 확인된 관련 기사가 없습니다.</p>'}
  <footer>기사 제목을 클릭하면 원문으로 이동합니다.</footer>
</main>
</body>
</html>
"""


def main() -> int:
    now = datetime.now(KST)
    start, end = period(now)
    articles = collect(start, end)
    OUTPUT.write_text(build_html(start, end, articles), encoding="utf-8")
    print(f"Generated {OUTPUT}: {len(articles)} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
