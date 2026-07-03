#!/usr/bin/env python3
"""퀴즈 결과를 Notion "퀴즈 기록" DB에 저장/집계"""

import os
from datetime import date

from notion_client import Client

NOTION_QUIZ_DB_ID = os.environ.get("NOTION_QUIZ_DB_ID", "")


def save_quiz_result(notion: Client, quiz: dict, chosen: str, is_correct: bool, source: str = "대시보드"):
    notion.pages.create(
        parent={"database_id": NOTION_QUIZ_DB_ID},
        properties={
            "문제":     {"title": [{"text": {"content": quiz["question"]}}]},
            "주제":     {"select": {"name": quiz["topic"]}},
            "정답여부":  {"checkbox": is_correct},
            "선택한 답": {"rich_text": [{"text": {"content": chosen}}]},
            "정답":     {"rich_text": [{"text": {"content": quiz["choices"][quiz["answer_index"]]}}]},
            "해설":     {"rich_text": [{"text": {"content": quiz.get("explanation", "")}}]},
            "날짜":     {"date": {"start": date.today().isoformat()}},
            "출처":     {"select": {"name": source}},
        },
    )


def get_stats(notion: Client) -> dict:
    results = []
    cursor = None
    while True:
        kwargs = {"database_id": NOTION_QUIZ_DB_ID}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        results.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    by_topic: dict[str, dict] = {}
    correct = 0
    for r in results:
        props = r["properties"]
        topic = (props["주제"]["select"] or {}).get("name", "기타")
        t = by_topic.setdefault(topic, {"total": 0, "correct": 0})
        t["total"] += 1
        if props["정답여부"]["checkbox"]:
            t["correct"] += 1
            correct += 1

    return {"total": len(results), "correct": correct, "by_topic": by_topic}
