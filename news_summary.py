#!/usr/bin/env python3
"""
매일 오전 7시 국내 뉴스 5개 + 세계 뉴스 5개 → Gemini 한국어 요약 → 텔레그램 전송
Reuters RSS는 2020년 폐지됨 → BBC/Al Jazeera/Guardian/DW 대체 사용
"""

import os
import re
import sys
import logging
from datetime import datetime

import feedparser
import requests
from google import genai
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

# ── RSS 피드 ──────────────────────────────────────────────────────────────────
KOREAN_FEEDS: dict[str, str] = {
    "정치":   "https://news.google.com/rss/search?q=한국+정치&hl=ko&gl=KR&ceid=KR:ko",
    "경제":   "https://news.google.com/rss/search?q=한국+경제&hl=ko&gl=KR&ceid=KR:ko",
    "사회":   "https://news.google.com/rss/search?q=한국+사회&hl=ko&gl=KR&ceid=KR:ko",
    "세계":   "https://news.google.com/rss/search?q=세계+국제&hl=ko&gl=KR&ceid=KR:ko",
    "IT과학": "https://news.google.com/rss/search?q=IT+과학+기술&hl=ko&gl=KR&ceid=KR:ko",
}

# Reuters/AP는 공식 RSS 폐지 → Google News RSS 우회 수집
# 7개 피드 풀에서 최대 5개 성공 기사 채움 (일부 피드 차단 시 자동 보완)
WORLD_FEEDS: dict[str, str] = {
    "Reuters":  "https://news.google.com/rss/search?q=site:reuters.com+world&hl=en&gl=US&ceid=US:en",
    "AP News":  "https://news.google.com/rss/search?q=site:apnews.com&hl=en&gl=US&ceid=US:en",
    "BBC 세계":  "https://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC 경제":  "https://feeds.bbci.co.uk/news/business/rss.xml",
    "DW 세계":   "https://rss.dw.com/rdf/rss-en-world",
    "NPR 세계":  "https://feeds.npr.org/1004/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
}
WORLD_TARGET = 5  # 세계 뉴스 목표 기사 수

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
    """전체 기사를 1회 API 호출로 일괄 요약. 실패 시 summary=None."""
    if not articles:
        return []

    article_lines = []
    for i, art in enumerate(articles, 1):
        desc = (art.get("description") or "")[:300]
        article_lines.append(f"[{i}] [{art['category']}] 제목: {art['title']} / 내용: {desc}")

    prompt = (
        f"아래 {len(articles)}개 뉴스 기사를 각각 한국어 2문장으로 요약하세요.\n"
        "영문 기사도 반드시 한국어로 요약합니다.\n"
        "마크다운 기호(**, ##, -, * 등)는 사용하지 마세요.\n"
        "응답 형식을 반드시 지키세요 — 각 요약을 번호로 시작, 한 줄에 2문장:\n"
        "[1] 첫째 문장. 둘째 문장.\n"
        "[2] 첫째 문장. 둘째 문장.\n\n"
        "기사 목록:\n" + "\n".join(article_lines)
    )

    try:
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw = (resp.text or "").strip()
        summaries: dict[int, str] = {}
        for m in re.finditer(r"\[(\d+)\]\s*(.+?)(?=\n\[\d+\]|\Z)", raw, re.DOTALL):
            summaries[int(m.group(1))] = m.group(2).strip()
        log.info(f"배치 요약 완료: {len(summaries)}/{len(articles)}개")
        return [{**art, "summary": summaries.get(i)} for i, art in enumerate(articles, 1)]
    except Exception as e:
        log.warning(f"배치 요약 실패 — 제목만 전송: {e}")
        return [{**art, "summary": None} for art in articles]


# ── 메시지 포맷 ───────────────────────────────────────────────────────────────

CATEGORY_EMOJI: dict[str, str] = {
    "정치":     "🏛",
    "경제":     "💰",
    "사회":     "🏙",
    "세계":     "🌐",
    "IT과학":   "💻",
    "Reuters":  "📡",
    "AP News":  "🗞",
    "BBC 세계": "🎙",
    "BBC 경제": "💹",
    "DW 세계":  "📻",
    "NPR 세계": "🎧",
    "Al Jazeera": "📺",
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
    title = _html_escape(_strip_markdown(art["title"]))
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
    today = datetime.now().strftime("%Y년 %m월 %d일 (%a)")
    notice = "\n⚠️ <i>Gemini API 한도 초과 — 제목만 전송</i>" if quota_exceeded else ""

    ko_lines = [f"📰 <b>{today} 주요뉴스</b>{notice}", "", f"🇰🇷 <b>국내 뉴스</b>", DIVIDER]
    for art in ko_articles:
        ko_lines.extend(_article_block(art))

    world_lines = [f"🌏 <b>세계 정세</b>", DIVIDER]
    for art in world_articles:
        world_lines.extend(_article_block(art))

    return "\n".join(ko_lines).rstrip(), "\n".join(world_lines).rstrip()


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
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
