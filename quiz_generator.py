#!/usr/bin/env python3
"""Gemini 기반 4지선다 퀴즈 생성 (영어단어 / 조선시대 임금 / 세계 수도 / 사자성어)"""

import json

from google import genai

QUIZ_TOPICS = ["영어단어", "조선시대 임금", "세계 수도", "사자성어"]

_TOPIC_GUIDE = {
    "영어단어": "실용적인 중~고급 영어 단어의 뜻을 묻는 문제 (단어를 보여주고 올바른 한국어 뜻 고르기, 또는 그 반대)",
    "조선시대 임금": "조선시대 왕(태조~순종)의 재위 순서, 업적, 일화 등을 묻는 문제",
    "세계 수도": "국가를 보여주고 수도를 고르거나, 수도를 보여주고 국가를 고르는 문제",
    "사자성어": "사자성어의 뜻을 묻거나, 뜻을 보여주고 알맞은 사자성어를 고르는 문제",
}


def _prompt_for(topic: str) -> str:
    return (
        f"'{topic}' 주제로 4지선다 퀴즈 1개를 만들어주세요.\n"
        f"문제 스타일: {_TOPIC_GUIDE[topic]}\n\n"
        "JSON만 출력하세요 (설명 없이):\n"
        '{"question":"...","choices":["...","...","...","..."],'
        '"answer_index":0,"explanation":"정답 이유 한 줄"}\n\n'
        "choices는 정확히 4개, answer_index는 정답 위치(0~3), "
        "매번 다른 새로운 문제를 내주세요."
    )


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1].split("```")[0]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def generate_quiz(topic: str, client: genai.Client) -> dict:
    resp = client.models.generate_content(model="gemini-2.5-flash", contents=_prompt_for(topic))
    quiz = _parse_json(resp.text)
    quiz["topic"] = topic
    return quiz


def generate_daily_quiz(client: genai.Client) -> list[dict]:
    return [generate_quiz(topic, client) for topic in QUIZ_TOPICS]
