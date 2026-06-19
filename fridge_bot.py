#!/usr/bin/env python3
"""
냉장고/반찬 관리 텔레그램 봇
자연어로 재료·반찬 추가, 사용, 조회, 메뉴 추천
"""

import json
import logging
import os
import time

import requests
from dotenv import load_dotenv
from google import genai
from notion_client import Client as NotionClient

load_dotenv()

TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN        = os.environ.get("NOTION_TOKEN", "")
NOTION_FRIDGE_DB_ID = os.environ.get("NOTION_FRIDGE_DB_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 <b>냉장고 봇 사용법</b>\n\n"
    "➕ <b>추가</b>\n"
    "  달걀 10개 샀어\n"
    "  된장찌개 4인분 만들었어\n\n"
    "➖ <b>사용</b>\n"
    "  달걀 2개 썼어\n"
    "  된장찌개 1인분 먹었어\n\n"
    "📋 <b>조회</b>\n"
    "  냉장고에 뭐 있어?\n"
    "  반찬 뭐 남았어?\n\n"
    "🍳 <b>메뉴 추천</b>\n"
    "  오늘 뭐 해먹을까?\n\n"
    "🛒 <b>장볼 목록</b>\n"
    "  뭐 사야 해?"
)


# ── Gemini 파싱 ───────────────────────────────────────────────────────────────

def parse_message(text: str) -> dict:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = (
        "한국 주부의 냉장고·반찬 관리 메시지를 JSON으로 변환하세요.\n\n"
        "action 종류:\n"
        '- "add"      : 재료/반찬 추가 (샀어, 있어, 만들었어, 넣었어)\n'
        '- "use"      : 사용/소비 (썼어, 먹었어, 사용했어, 다 썼어)\n'
        '- "list"     : 목록 조회 (뭐 있어, 목록, 현황, 뭐 남았어)\n'
        '- "suggest"  : 메뉴 추천 (뭐 해먹을까, 추천)\n'
        '- "shopping" : 장볼 것 (뭐 사야해, 부족한 거)\n'
        '- "help"     : 도움말\n'
        '- "unknown"  : 기타\n\n'
        "출력 형식 (JSON만, 설명 없이):\n"
        '{"action":"add","items":[{"name":"달걀","quantity":10,"unit":"개","category":"기타"}]}\n\n'
        "카테고리: 채소 / 육류 / 해산물 / 유제품 / 반찬 / 냉동 / 기타\n"
        "수량이 없으면 quantity=1 로 설정\n\n"
        f"메시지: {text}"
    )
    try:
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw = resp.text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        log.error(f"Gemini 파싱 실패: {e}")
        return {"action": "unknown", "items": []}


# ── Notion CRUD ───────────────────────────────────────────────────────────────

def _props(page: dict) -> dict:
    p = page["properties"]
    name     = p["재료명"]["title"][0]["text"]["content"] if p["재료명"]["title"] else ""
    quantity = p["수량"]["number"] or 0
    unit     = p["단위"]["rich_text"][0]["text"]["content"] if p["단위"]["rich_text"] else ""
    category = p["카테고리"]["select"]["name"] if p["카테고리"]["select"] else "기타"
    return {"id": page["id"], "name": name, "quantity": quantity, "unit": unit, "category": category}


def find_item(notion: NotionClient, name: str) -> dict | None:
    res = notion.databases.query(
        database_id=NOTION_FRIDGE_DB_ID,
        filter={"property": "재료명", "title": {"contains": name}},
    )
    return _props(res["results"][0]) if res["results"] else None


def get_all(notion: NotionClient) -> list[dict]:
    res = notion.databases.query(
        database_id=NOTION_FRIDGE_DB_ID,
        sorts=[{"property": "카테고리", "direction": "ascending"}],
    )
    return [_props(p) for p in res["results"] if (p["properties"]["수량"]["number"] or 0) > 0]


def upsert(notion: NotionClient, name: str, qty: float, unit: str, category: str):
    existing = find_item(notion, name)
    if existing:
        notion.pages.update(
            page_id=existing["id"],
            properties={"수량": {"number": existing["quantity"] + qty}},
        )
    else:
        notion.pages.create(
            parent={"database_id": NOTION_FRIDGE_DB_ID},
            properties={
                "재료명":   {"title": [{"text": {"content": name}}]},
                "수량":    {"number": qty},
                "단위":    {"rich_text": [{"text": {"content": unit}}]},
                "카테고리": {"select": {"name": category}},
            },
        )


def consume(notion: NotionClient, name: str, qty: float) -> tuple[bool, float, str]:
    existing = find_item(notion, name)
    if not existing:
        return False, 0, ""
    new_qty = max(0.0, existing["quantity"] - qty)
    notion.pages.update(page_id=existing["id"], properties={"수량": {"number": new_qty}})
    return True, new_qty, existing["unit"]


# ── 텔레그램 ─────────────────────────────────────────────────────────────────

def send(text: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


# ── 메시지 처리 ───────────────────────────────────────────────────────────────

def handle(text: str):
    parsed  = parse_message(text)
    action  = parsed.get("action", "unknown")
    items   = parsed.get("items", [])
    notion  = NotionClient(auth=NOTION_TOKEN)

    if action == "add":
        lines = []
        for it in items:
            upsert(notion, it["name"], it["quantity"], it["unit"], it.get("category", "기타"))
            lines.append(f"✅ {it['name']} {it['quantity']}{it['unit']} 추가됐어요")
        send("\n".join(lines) if lines else "추가할 항목을 찾지 못했어요 😅")

    elif action == "use":
        lines = []
        for it in items:
            ok, remaining, unit = consume(notion, it["name"], it["quantity"])
            if ok:
                msg = f"✅ {it['name']} {it['quantity']}{it['unit']} 사용 → 남은 수량: {remaining}{unit}"
                if remaining == 0:
                    msg += " ⚠️ (다 소진됐어요!)"
            else:
                msg = f"❓ '{it['name']}'을 찾지 못했어요"
            lines.append(msg)
        send("\n".join(lines) if lines else "사용할 항목을 찾지 못했어요 😅")

    elif action == "list":
        inventory = get_all(notion)
        if not inventory:
            send("현재 등록된 재료·반찬이 없어요 🥲\n\n'달걀 10개 샀어' 처럼 입력해보세요!")
            return
        by_cat: dict[str, list] = {}
        for it in inventory:
            by_cat.setdefault(it["category"], []).append(it)
        lines = ["🧊 <b>현재 재고</b>\n"]
        for cat, cat_items in by_cat.items():
            lines.append(f"<b>[{cat}]</b>")
            for it in cat_items:
                lines.append(f"  • {it['name']}  {it['quantity']}{it['unit']}")
        send("\n".join(lines))

    elif action == "suggest":
        inventory = get_all(notion)
        if not inventory:
            send("재고가 없어서 추천이 어려워요 😅\n재료를 먼저 등록해주세요!")
            return
        items_str = ", ".join(f"{i['name']} {i['quantity']}{i['unit']}" for i in inventory)
        client = genai.Client(api_key=GEMINI_API_KEY)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                f"냉장고 재료: {items_str}\n"
                "이 재료로 만들 수 있는 한국 가정식 메뉴 3가지를 추천해주세요. "
                "각 메뉴마다 ① 메뉴명 ② 필요한 재료 ③ 조리법 한 줄로 작성해주세요."
            ),
        )
        send(f"🍳 <b>오늘의 메뉴 추천</b>\n\n{resp.text}")

    elif action == "shopping":
        inventory = get_all(notion)
        low = [i for i in inventory if i["quantity"] <= 2]
        if low:
            lines = ["🛒 <b>보충이 필요한 재료</b>\n"]
            for it in low:
                lines.append(f"  • {it['name']} (현재 {it['quantity']}{it['unit']})")
            send("\n".join(lines))
        else:
            send("✅ 지금은 부족한 재료가 없어요!")

    elif action == "help":
        send(HELP_TEXT)

    else:
        send(
            "이해하지 못했어요 😅\n\n"
            "예시:\n"
            "  달걀 10개 샀어\n"
            "  된장찌개 4인분 만들었어\n"
            "  냉장고에 뭐 있어?\n"
            "  오늘 뭐 해먹을까?\n\n"
            "'도움말' 이라고 입력하면 전체 사용법을 볼 수 있어요."
        )


# ── 메인 루프 ────────────────────────────────────────────────────────────────

def main():
    offset = 0
    log.info("🤖 냉장고 봇 시작!")
    send("🤖 냉장고 봇이 시작됐어요!\n'도움말' 이라고 입력해보세요.")
    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "").strip()
                if chat_id == TELEGRAM_CHAT_ID and text:
                    log.info(f"수신: {text}")
                    handle(text)
        except Exception as e:
            log.error(f"오류: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
