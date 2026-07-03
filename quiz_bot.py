#!/usr/bin/env python3
"""
매일 아침 텔레그램으로 퀴즈 발송 (GitHub Actions cron)
주제: 영어단어 / 조선시대 임금 / 세계 수도 / 사자성어
"""

import logging
import os
import sys

import requests
from dotenv import load_dotenv
from google import genai

from quiz_generator import generate_daily_quiz

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_CIRCLED = ["①", "②", "③", "④"]


def send(text: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


def format_quiz(idx: int, quiz: dict) -> str:
    lines = [f"<b>Q{idx}. [{quiz['topic']}]</b> {quiz['question']}"]
    for i, choice in enumerate(quiz["choices"]):
        lines.append(f"{_CIRCLED[i]} {choice}")
    answer = quiz["choices"][quiz["answer_index"]]
    lines.append(
        f'<span class="tg-spoiler">정답: {_CIRCLED[quiz["answer_index"]]} {answer}\n'
        f'{quiz["explanation"]}</span>'
    )
    return "\n".join(lines)


def main():
    missing = [v for v in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(v)]
    if missing:
        log.error(f"환경변수 누락: {', '.join(missing)}")
        sys.exit(1)

    client = genai.Client(api_key=GEMINI_API_KEY)
    quizzes = generate_daily_quiz(client)

    send("🧠 <b>오늘의 아침 퀴즈</b>\n정답은 가려져 있어요. 탭해서 확인하세요!")
    for i, quiz in enumerate(quizzes, start=1):
        send(format_quiz(i, quiz))
    log.info(f"퀴즈 {len(quizzes)}개 발송 완료")


if __name__ == "__main__":
    main()
