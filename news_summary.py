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

import time
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

N_CANDIDATES = 3  # 카테고리당 후보 기사 수 (요약 실패 시 다음 기사로)


class QuotaExceededError(Exception):
    pass


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


# ── Gemini 요약 ───────────────────────────────────────────────────────────────

def _summarize_one(art: dict, client: genai.Client) -> str:
    """단일 기사 요약. 429 시 retryDelay만큼 대기 후 1회 재시도."""
    prompt = (
        "다음 뉴스 기사를 한국어로 2문장으로 핵심만 요약해 주세요.\n"
        "영문 기사도 반드시 한국어로 요약합니다.\n"
        "형식: 번호 없이 줄바꿈으로 구분된 정확히 2문장\n\n"
        f"제목: {art['title']}\n"
        f"내용: {art['description']}"
    )
    for attempt in range(2):  # 최대 1회 재시도
        try:
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            summary = (resp.text or "").strip()
            if not summary:
                raise ValueError("빈 응답")
            return summary
        except Exception as e:
            err_str = str(e)
            m = re.search(r"retryDelay.*?'(\d+)s'", err_str)
            if m and attempt < 1:
                wait = int(m.group(1)) + 3
                log.info(f"[{art['category']}] Rate limit — {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue
            if not m and any(k in err_str for k in ("RESOURCE_EXHAUSTED", "quota", "429")):
                raise QuotaExceededError(str(e))
            raise


def collect_summaries(
    candidates: dict[str, list[dict]],
    client: genai.Client,
    limit: int | None = None,
    skip_api: bool = False,
) -> tuple[list[dict], bool]:
    """카테고리별 후보에서 첫 번째 성공 요약 채택. limit 지정 시 해당 수 달성 후 중단.

    Returns: (articles, quota_exceeded)
    quota_exceeded=True이면 일부/전체 기사에 summary=None (제목만 전송).
    """
    results = []
    quota_exceeded = skip_api

    for category, articles in candidates.items():
        if limit and len(results) >= limit:
            break
        if not articles:
            continue

        if quota_exceeded:
            results.append({**articles[0], "summary": None})
            continue

        for art in articles:
            try:
                summary = _summarize_one(art, client)
                results.append({**art, "summary": summary})
                log.info(f"[{category}] 요약 완료")
                break
            except QuotaExceededError as e:
                log.warning(f"Gemini API 일일 한도 초과 — 나머지 기사는 제목만 전송: {e}")
                quota_exceeded = True
                results.append({**articles[0], "summary": None})
                break
            except Exception as e:
                log.warning(f"[{category}] 기사 건너뜀 → 다음 후보 시도: {e}")
        else:
            log.error(f"[{category}] 후보 {len(articles)}개 모두 실패, 카테고리 제외")

    return results, quota_exceeded


# ── 메시지 포맷 ───────────────────────────────────────────────────────────────

def build_messages(
    ko_articles: list[dict],
    world_articles: list[dict],
    quota_exceeded: bool = False,
) -> tuple[str, str]:
    today = datetime.now().strftime("%Y년 %m월 %d일 (%a)")
    notice = "\n⚠️ Gemini API 한도 초과 — 제목만 전송" if quota_exceeded else ""

    ko_lines = [f"📰 {today} 주요뉴스{notice}", "", "🇰🇷 국내 뉴스"]
    for i, art in enumerate(ko_articles, 1):
        title = art["title"][:34] + "…" if len(art["title"]) > 35 else art["title"]
        ko_lines.append(f"[{i}/{art['category']}] {title}")
        if art.get("summary"):
            ko_lines.append(art["summary"])
        ko_lines.append(f"🔗 {art['link']}")
        ko_lines.append("")

    world_lines = ["🌏 세계 정세"]
    for i, art in enumerate(world_articles, 1):
        title = art["title"][:34] + "…" if len(art["title"]) > 35 else art["title"]
        world_lines.append(f"[{i}/{art['category']}] {title}")
        if art.get("summary"):
            world_lines.append(art["summary"])
        world_lines.append(f"🔗 {art['link']}")
        world_lines.append("")

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

    ko_candidates = fetch_candidates(KOREAN_FEEDS)
    ko_articles, ko_quota = collect_summaries(ko_candidates, client)

    world_candidates = fetch_candidates(WORLD_FEEDS)
    world_articles, world_quota = collect_summaries(
        world_candidates, client, limit=WORLD_TARGET, skip_api=ko_quota
    )

    quota_exceeded = ko_quota or world_quota
    if quota_exceeded:
        log.warning("Gemini API 한도 초과 — 일부 또는 전체 기사를 제목만 전송합니다.")

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
