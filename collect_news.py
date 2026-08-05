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
MAX_PER_GROUP = 12

GROUPS = [
    ("현대건설", ["현대건설 원전", "현대건설 원자력", '"Hyundai E&C" nuclear', "HDEC nuclear"]),
    ("한수원·한국수력원자력", ["한수원 원전", "한국수력원자력", "KHNP nuclear", "KHNP reactor"]),
    ("한전·한국전력", ["한전 원전", "한국전력 원자력", "KEPCO nuclear", "KEPCO reactor"]),
    ("원전·원자력", ["원전", "원자력", "원자력발전", "원자력발전소", "대형원전", "신규 원전", "원전 건설", "원전 프로젝트", "원전 수출"]),
    ("SMR", ["SMR", "소형모듈원자로", '"Small Modular Reactor"', "차세대원자로", '"Advanced Reactor"', "Microreactor"]),
    ("Nuclear Power·Nuclear Energy", ['"Nuclear Power"', '"Nuclear Energy"', '"Nuclear Power Plant"', '"Nuclear Construction"', '"Nuclear Project"', '"Nuclear New Build"']),
    ("Holtec", ["Holtec nuclear", '"Holtec International"', "SMR-300", "Palisades nuclear", '"Oyster Creek" SMR']),
    ("TerraPower", ["TerraPower", "Natrium reactor", "Natrium nuclear", "Kemmerer nuclear"]),
    ("Westinghouse", ["Westinghouse nuclear", '"Westinghouse Electric Company"', "AP1000", "AP300", "AP1000 construction"]),
    ("Fermi America", ['"Fermi America"', '"Project Matador"', "HyperGrid nuclear", '"Fermi America" AP1000', "Amarillo nuclear", '"Carson County" nuclear']),
]


@dataclass
class Article:
    title: str
    link: str
    published: datetime
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
        return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


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
        if key == other or SequenceMatcher(None, key, other).ratio() >= 0.86:
            return True
    return False


def parse_entry(entry, group: str) -> Article | None:
    raw_date = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if not raw_date:
        return None
    try:
        published = date_parser.parse(raw_date).astimezone(KST)
    except Exception:
        return None

    title, publisher_from_title = split_title_and_publisher(getattr(entry, "title", ""))
    link = getattr(entry, "link", "").strip()
    if not title or not link:
        return None

    source = getattr(entry, "source", {})
    publisher = source.get("title", "") if isinstance(source, dict) else ""
    return Article(
        title=title,
        link=link,
        published=published,
        group=group,
        publisher=publisher.strip() or publisher_from_title or "출처 확인",
        image=extract_image(entry),
    )


def collect(start: datetime, end: datetime) -> list[Article]:
    all_selected: list[Article] = []

    for group, queries in GROUPS:
        found: list[Article] = []
        for language in ("ko", "en"):
            for query in queries:
                feed = feedparser.parse(google_news_url(query, language))
                for entry in feed.entries:
                    article = parse_entry(entry, group)
                    if article and start <= article.published < end:
                        found.append(article)

        found.sort(key=lambda a: -a.published.timestamp())
        selected_group: list[Article] = []
        for article in found:
            if len(selected_group) >= MAX_PER_GROUP:
                break
            if is_duplicate(article, all_selected + selected_group):
                continue
            selected_group.append(article)

        all_selected.extend(selected_group)

    return all_selected


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def render_card(article: Article) -> str:
    if article.image:
        image_html = (
            f'<img src="{escape(article.image)}" alt="" '
            'loading="lazy" referrerpolicy="no-referrer">'
        )
    else:
        image_html = '<div class="no-image">NUCLEAR<br>NEWS</div>'

    return f'''
<a class="preview-card" href="{escape(article.link)}" target="_blank" rel="noopener">
  <div class="preview-copy">
    <div class="publisher">{escape(article.publisher)}</div>
    <div class="headline">{escape(article.title)}</div>
    <div class="go">기사 보기</div>
  </div>
  <div class="preview-image">{image_html}</div>
</a>
'''


def render_group(group: str, articles: list[Article]) -> str:
    if not articles:
        return ""
    cards = "".join(render_card(article) for article in articles)
    return f'''
<section class="chat-group">
  <div class="bubble-wrap"><div class="bubble">{escape(group)}</div></div>
  <div class="article-stack">{cards}</div>
</section>
'''


def build_html(start: datetime, end: datetime, articles: list[Article]) -> str:
    grouped: dict[str, list[Article]] = {name: [] for name, _ in GROUPS}
    for article in articles:
        grouped[article.group].append(article)

    sections = "".join(render_group(group, grouped[group]) for group, _ in GROUPS)

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
  background: rgba(178, 199, 217, 0.96);
  border-bottom: 1px solid rgba(17, 24, 39, 0.08);
  backdrop-filter: blur(8px);
}}
.topbar h1 {{ margin: 0; font-size: 19px; line-height: 1.25; font-weight: 800; }}
.period {{ margin-top: 5px; color: #344054; font-size: 11px; line-height: 1.45; }}
main {{ padding: 12px 12px 34px; }}
.chat-group {{ margin-bottom: 18px; }}
.bubble-wrap {{ display: flex; justify-content: flex-end; margin-bottom: 7px; }}
.bubble {{
  max-width: 88%;
  padding: 9px 12px;
  background: #fee500;
  border-radius: 12px 4px 12px 12px;
  font-size: 14px;
  font-weight: 800;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.12);
}}
.article-stack {{ display: grid; gap: 7px; }}
.preview-card {{
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px;
  min-height: 105px;
  overflow: hidden;
  color: inherit;
  background: #fff;
  border-radius: 10px;
  text-decoration: none;
  box-shadow: 0 1px 3px rgba(17, 24, 39, 0.16);
}}
.preview-copy {{
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 11px 11px 10px;
}}
.publisher {{
  overflow: hidden;
  color: #667085;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.headline {{
  display: -webkit-box;
  margin-top: 6px;
  overflow: hidden;
  color: #101828;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.38;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}}
.go {{ margin-top: auto; color: #667085; font-size: 10px; }}
.preview-image {{ width: 112px; min-height: 105px; background: #d0d5dd; }}
.preview-image img {{
  display: block;
  width: 100%;
  height: 100%;
  min-height: 105px;
  object-fit: cover;
}}
.no-image {{
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  min-height: 105px;
  color: white;
  background: linear-gradient(135deg, #173b67, #0b213d);
  font-size: 11px;
  font-weight: 800;
  line-height: 1.5;
  text-align: center;
}}
.empty {{
  padding: 22px 15px;
  background: white;
  border-radius: 10px;
  text-align: center;
  color: #667085;
}}
footer {{ padding: 0 12px 28px; color: #475467; font-size: 10px; text-align: center; }}
@media (max-width: 380px) {{
  .preview-card {{ grid-template-columns: minmax(0, 1fr) 96px; }}
  .preview-image {{ width: 96px; }}
  .headline {{ font-size: 13px; }}
}}
</style>
</head>
<body>
<div class="phone">
<header class="topbar">
  <h1>원자력 주요기사 Daily Brief</h1>
  <div class="period">{start:%Y. %-m. %-d. %H:%M} ~ {end:%Y. %-m. %-d. %H:%M} (KST)</div>
</header>
<main>{sections or '<div class="empty">조회기간 내 관련 기사가 없습니다.</div>'}</main>
<footer>기사 카드를 누르면 원문으로 이동합니다.</footer>
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
