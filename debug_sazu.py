#!/usr/bin/env python3
"""SAZU API 진단 스크립트 — 화면 없이 원본 응답을 그대로 확인.

사용법: saju_app.py와 같은 폴더에서
    python3 debug_sazu.py
"""
import json
import os

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SAZU_API_KEY = os.environ.get("SAZU_API_KEY", "")
SAZU_BASE_URL = "https://api.sazu.app/v1"

if not SAZU_API_KEY:
    print("❌ SAZU_API_KEY가 환경변수/.env에 없습니다. .env 파일을 확인하세요.")
    raise SystemExit(1)

print(f"✅ SAZU_API_KEY 확인됨 (앞 8자: {SAZU_API_KEY[:8]}...)")

payload = {
    "birthYear": 1990,
    "birthMonth": 5,
    "birthDay": 15,
    "isLunar": False,
    "isFemale": True,
    "birthHour": 10,
    "birthMinute": 0,
    "modules": ["fourPillars", "elements", "summary", "sinStrength", "decadeFortune"],
}

print("\n--- 요청 보내는 중 (테스트용 고정값: 1990-05-15 10:00 양력 여성) ---")
try:
    resp = requests.post(
        f"{SAZU_BASE_URL}/sazu/calculate",
        json=payload,
        headers={"Authorization": f"Bearer {SAZU_API_KEY}", "Content-Type": "application/json"},
        timeout=15,
    )
    print(f"HTTP 상태코드: {resp.status_code}")
    body = resp.json()
except Exception as e:
    print(f"❌ 요청 자체가 실패했습니다: {type(e).__name__}: {e}")
    raise SystemExit(1)

print("\n--- 응답 최상위 키 ---")
print(list(body.keys()))

if "meta" in body:
    print("\n--- meta ---")
    print(json.dumps(body["meta"], ensure_ascii=False, indent=2))

modules = body.get("data", {}).get("modules", {})
print("\n--- modules 안에 들어있는 키들 ---")
print(list(modules.keys()) if modules else "❌ modules가 비어있거나 없습니다!")

fp = modules.get("fourPillars")
print("\n--- fourPillars 내용 ---")
if fp:
    print(json.dumps(fp, ensure_ascii=False, indent=2))
else:
    print("❌ fourPillars가 없습니다! 이게 화면이 비는 직접적인 원인입니다.")

print("\n--- 에러 필드가 있는지 확인 ---")
if "error" in body:
    print("⚠️ 응답에 error 필드가 있습니다:")
    print(json.dumps(body["error"], ensure_ascii=False, indent=2))
else:
    print("error 필드 없음 (정상)")
