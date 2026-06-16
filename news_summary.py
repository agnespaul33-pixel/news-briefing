#!/usr/bin/env python3
"""
평일 오전 9시~오후 3시 30분, 30분마다
경제·주식·부동산·환율·금리 뉴스 → Gemini 한국어 요약 → 텔레그램 전송
"""

import json
import os
import re
import sys
import logging
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from google import genai
from notion_client import Client as NotionClient
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("news_summary.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 환경변수 ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTION_TOKEN      = os.environ.get("NOTION_TOKEN", "")
NOTION_PAGE_ID    = os.environ.get("NOTION_PAGE_ID", "").strip()

# ── RSS 피드 ──────────────────────────────────────────────────────────────────
_GN_KO = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
_GN_EN = "https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en"

KOREAN_FEEDS: dict[str, str] = {
    # 주제별
    "경제":      _GN_KO.format(q="한국+경제+증시"),
    "주식":      _GN_KO.format(q="코스피+코스닥+주식시장"),
    "부동산":    _GN_KO.format(q="부동산+아파트+분양"),
    "환율":      _GN_KO.format(q="원달러+환율+외환시장"),
    "금리":      _GN_KO.format(q="금리+한국은행+기준금리"),
    # 언론사별
    "매일경제":    _GN_KO.format(q="site:mk.co.kr"),
    "한국경제":    _GN_KO.format(q="site:hankyung.com"),
    "머니투데이":  _GN_KO.format(q="site:mt.co.kr"),
    "연합인포맥스": _GN_KO.format(q="site:einfomax.co.kr"),
}

WORLD_FEEDS: dict[str, str] = {
    "Reuters 경제": _GN_EN.format(q="site:reuters.com+economy+finance"),
    "Reuters 시장": _GN_EN.format(q="site:reuters.com+markets+stocks"),
    "Bloomberg":    _GN_EN.format(q="site:bloomberg.com+economy"),
    "FT":           _GN_EN.format(q="site:ft.com+economy"),
    "WSJ":          _GN_EN.format(q="site:wsj.com+economy"),
}
WORLD_TARGET = 5

# settings.json 의 korean_keywords 로 KOREAN_FEEDS URL 오버라이드 (UI 연동)
_settings_path = Path(__file__).parent / "settings.json"
if _settings_path.exists():
    try:
        _s = json.loads(_settings_path.read_text(encoding="utf-8"))
        for _cat, _kw in _s.get("korean_keywords", {}).items():
            if _cat in KOREAN_FEEDS:
                KOREAN_FEEDS[_cat] = _GN_KO.format(q=_kw.replace(" ", "+"))
    except Exception:
        pass

_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

N_CANDIDATES = 3  # 카테고리당 후보 기사 수


# ── 뉴스 수집 ────────────────────────────────────────────────────────────────

def fetch_candidates(feeds: dict[str, str]) -> dict[str, list[dict]]:
    """피드별로 최신 N_CANDIDATES개 후보 기사 수집"""
    candidates: dict[str, list[dict]] = {}
    for category, url in feeds.items():
        try:
            feed = feedparser.parse(url, request_headers=_RSS_HEADERS)
            if not feed.entries:
                log.warning(f"[{category}] RSS 항목 없음: {url}")
                continue
            articles = []
            for entry in feed.entries[:N_CANDIDATES]:
                description = re.sub(r"<[^>]+>", "", entry.get("description", "")).strip()
                articles.append({
                    "category": category,
                    "title":    entry.get("title", "").strip(),
                    "description": description[:500],
                    "link":     entry.get("link", ""),
                })
            candidates[category] = articles
            log.info(f"[{category}] {len(articles)}개 후보 수집")
        except Exception as e:
            log.error(f"[{category}] RSS 수집 오류: {e}")
    return candidates


# ── Gemini 요약 (1회 배치 호출) ───────────────────────────────────────────────

def _select_articles(candidates: dict[str, list[dict]], limit: int | None = None) -> list[dict]:
    """카테고리별 첫 번째 후보 선택 (limit개까지)"""
    selected = []
    for articles in candidates.values():
        if limit and len(selected) >= limit:
            break
        if articles:
            selected.append(articles[0])
    return selected


def summarize_batch(articles: list[dict], client: genai.Client) -> list[dict]:
    """전체 기사를 1회 API 호출로 일괄 요약 + 제목 한국어 번역. 실패 시 summary=None."""
    if not articles:
        return []

    article_lines = []
    for i, art in enumerate(articles, 1):
        desc = (art.get("description") or "")[:300]
        article_lines.append(f"<{i}> [{art['category']}] {art['title']} / {desc}")

    prompt = (
        "모든 출력은 반드시 한국어로 작성합니다. 영어 출력은 절대 허용되지 않습니다.\n\n"
        f"아래 {len(articles)}개 기사를 처리하세요.\n"
        "영문 기사: 제목을 한국어로 번역하고 내용을 한국어 2문장으로 요약합니다.\n"
        "한국어 기사: 제목은 그대로 두고 내용을 한국어 2문장으로 요약합니다.\n"
        "마크다운 기호(**, ##, -, * 등)는 사용하지 않습니다.\n\n"
        "출력 형식 (반드시 준수, 한국어로):\n"
        "[1]\n제목: 한국어 제목\n요약: 한국어 첫 문장. 한국어 둘째 문장.\n"
        "[2]\n제목: 한국어 제목\n요약: 한국어 첫 문장. 한국어 둘째 문장.\n\n"
        "기사 목록:\n" + "\n".join(article_lines)
    )

    try:
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw = (resp.text or "").strip()

        results: dict[int, dict] = {}
        for block in re.split(r"\n(?=\[\d+\])", raw):
            m_idx = re.match(r"\[(\d+)\]", block)
            if not m_idx:
                continue
            idx = int(m_idx.group(1))
            m_title   = re.search(r"제목:\s*(.+?)(?=\n요약:|\Z)", block, re.DOTALL)
            m_summary = re.search(r"요약:\s*(.+?)$", block, re.DOTALL)
            results[idx] = {
                "ko_title": m_title.group(1).strip() if m_title else None,
                "summary":  m_summary.group(1).strip() if m_summary else None,
            }

        log.info(f"배치 요약 완료: {len(results)}/{len(articles)}개")
        return [
            {**art, **results.get(i, {"ko_title": None, "summary": None})}
            for i, art in enumerate(articles, 1)
        ]
    except Exception as e:
        log.warning(f"배치 요약 실패 — 제목만 전송: {e}")
        return [{**art, "ko_title": None, "summary": None} for art in articles]


# ── 메시지 포맷 ───────────────────────────────────────────────────────────────

CATEGORY_EMOJI: dict[str, str] = {
    # 국내 주제
    "경제":      "💰",
    "주식":      "📈",
    "부동산":    "🏠",
    "환율":      "💱",
    "금리":      "🏦",
    # 국내 언론사
    "매일경제":    "📰",
    "한국경제":    "📊",
    "머니투데이":  "💹",
    "연합인포맥스": "🔵",
    # 해외
    "Reuters 경제": "🌐",
    "Reuters 시장": "📉",
    "Bloomberg":   "💼",
    "FT":          "🗞",
    "WSJ":         "🗽",
}

DIVIDER = "─" * 22


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _strip_markdown(text: str) -> str:
    """Gemini 응답에 섞인 마크다운 기호 제거"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _article_block(art: dict) -> list[str]:
    emoji = CATEGORY_EMOJI.get(art["category"], "📌")
    raw_title = art.get("ko_title") or art["title"]
    title = _html_escape(_strip_markdown(raw_title))
    link  = art["link"].replace("&", "&amp;")
    lines = [f'{emoji} <b>[{art["category"]}]</b> {title}']
    if art.get("summary"):
        lines.append(_html_escape(_strip_markdown(art["summary"])))
    lines.append(f'🔗 <a href="{link}">기사 보기</a>')
    lines.append("")
    return lines


def build_messages(
    ko_articles: list[dict],
    world_articles: list[dict],
    quota_exceeded: bool = False,
) -> tuple[str, str]:
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today    = now_kst.strftime("%Y년 %m월 %d일 (%a)")
    time_str = now_kst.strftime("%H:%M")
    notice = "\n⚠️ <i>Gemini API 한도 초과 — 제목만 전송</i>" if quota_exceeded else ""

    ko_lines = [f"📈 <b>{today} {time_str} 경제 브리핑</b>{notice}", "", f"🇰🇷 <b>국내 경제</b>", DIVIDER]
    for art in ko_articles:
        ko_lines.extend(_article_block(art))

    world_lines = [f"🌍 <b>해외 경제</b>", DIVIDER]
    for art in world_articles:
        world_lines.extend(_article_block(art))

    return "\n".join(ko_lines).rstrip(), "\n".join(world_lines).rstrip()


# ── 노션 저장 ────────────────────────────────────────────────────────────────

def _nt(content: str) -> dict:
    return {"type": "text", "text": {"content": content[:2000]}}


def _nt_link(label: str, url: str) -> dict:
    return {"type": "text", "text": {"content": label, "link": {"url": url}}}


def _notion_article_blocks(art: dict) -> list[dict]:
    emoji = CATEGORY_EMOJI.get(art["category"], "📌")
    title = _strip_markdown(art.get("ko_title") or art["title"])
    blocks: list[dict] = [
        {
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [_nt(f"{emoji} [{art['category']}] {title}")]},
        }
    ]
    if art.get("summary"):
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [_nt(_strip_markdown(art["summary"]))]},
        })
    if art.get("link"):
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [_nt_link("🔗 기사 보기", art["link"])]},
        })
    return blocks


def save_to_notion(ko_articles: list[dict], world_articles: list[dict]) -> bool:
    if not NOTION_TOKEN or not NOTION_PAGE_ID:
        log.info("Notion 환경변수 미설정 — 건너뜀")
        return False

    KST = timezone(timedelta(hours=9))
    page_title = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M 경제 브리핑")

    children: list[dict] = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [_nt("🇰🇷 국내 경제")]}},
        {"object": "block", "type": "divider", "divider": {}},
    ]
    for art in ko_articles:
        children.extend(_notion_article_blocks(art))

    children += [
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [_nt("🌍 해외 경제")]}},
        {"object": "block", "type": "divider", "divider": {}},
    ]
    for art in world_articles:
        children.extend(_notion_article_blocks(art))

    try:
        notion = NotionClient(auth=NOTION_TOKEN)
        notion.pages.create(
            parent={"page_id": NOTION_PAGE_ID},
            properties={"title": {"title": [_nt(page_title)]}},
            children=children,
        )
        log.info(f"Notion 저장 완료: {page_title}")
        return True
    except Exception as e:
        log.error(f"Notion 저장 실패: {e}")
        return False


# ── 텔레그램 전송 ─────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        log.error(f"텔레그램 전송 오류: {e}")
        return False

    if resp.status_code == 200 and resp.json().get("ok"):
        return True

    log.error(f"텔레그램 전송 실패: {resp.status_code} {resp.text}")
    return False


# ── 메인 ─────────────────────────────────────────────────────────────────────

def validate_env():
    missing = [
        var for var in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        if not os.environ.get(var)
    ]
    if missing:
        log.error(f"환경변수 누락: {', '.join(missing)}")
        sys.exit(1)


def main():
    validate_env()
    log.info("=== 뉴스 요약 시작 ===")
    client = genai.Client(api_key=GEMINI_API_KEY)

    ko_candidates    = fetch_candidates(KOREAN_FEEDS)
    world_candidates = fetch_candidates(WORLD_FEEDS)

    ko_arts    = _select_articles(ko_candidates)
    world_arts = _select_articles(world_candidates, limit=WORLD_TARGET)

    all_arts = summarize_batch(ko_arts + world_arts, client)
    n_ko = len(ko_arts)
    ko_articles    = all_arts[:n_ko]
    world_articles = all_arts[n_ko:]

    quota_exceeded = not any(art.get("summary") for art in all_arts)
    if quota_exceeded:
        log.warning("전체 요약 실패 — 제목만 전송합니다.")

    if not ko_articles and not world_articles:
        log.error("수집된 뉴스가 없습니다.")
        sys.exit(1)

    ko_message, world_message = build_messages(ko_articles, world_articles, quota_exceeded)
    log.info(f"\n{'='*50}\n{ko_message}\n{world_message}\n{'='*50}")

    ok1 = send_telegram(ko_message)
    ok2 = send_telegram(world_message)
    if ok1 and ok2:
        log.info("텔레그램 전송 성공")

    save_to_notion(ko_articles, world_articles)

    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
