#!/usr/bin/env python3
"""만세력 대시보드 — SAZU 만세력 API(사주팔자·대운) + Gemini(만당 스타일 해석)

참고: 사주첩경(이석영) · 자평진전
"""

import json
import os
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="만세력", page_icon="🔮", layout="wide")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _secret(name: str) -> str:
    try:
        val = st.secrets.get(name)
    except Exception:
        val = None
    return val or os.environ.get(name, "")


SAZU_API_KEY = _secret("SAZU_API_KEY")
GEMINI_API_KEY = _secret("SAJU_GEMINI_API_KEY")
# 주의: gemini-1.5-flash, gemini-2.5-flash-lite는 이미 이 계정에서 사용 불가(404).
# gemini-2.5-flash는 2026-10-16 이후 종료 예정(Google 공식, 확정일은 6개월 전 재공지) —
# 그때는 GEMINI_MODEL 환경변수로 gemini-3.6-flash 등으로 전환.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_missing = [n for n, v in (("SAZU_API_KEY", SAZU_API_KEY), ("SAJU_GEMINI_API_KEY", GEMINI_API_KEY)) if not v]
if _missing:
    st.error(f"환경변수 누락: {', '.join(_missing)} — .env 또는 Streamlit secrets에 설정하세요.")
    st.stop()

from google import genai
from google.genai import types as genai_types

# ── 상수 ──────────────────────────────────────────────────────────────────

SAZU_BASE_URL = "https://api.sazu.app/v1"
SAZU_MODULES = [
    "fourPillars", "decadeFortune", "elements", "summary", "sinStrength",
    "gyeokguk", "yongsin", "sinsal", "relationships", "ghostElements",
    "seun", "weolun", "wongukInteraction", "evaluation",
]

ELEMENT_ORDER = ["wood", "fire", "earth", "metal", "water"]
ELEMENT_KR = {"wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수"}
ELEMENT_KR_BY_HANJA = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}
ELEMENT_COLOR = {"목": "#008300", "화": "#e34948", "토": "#eda100", "금": "#2a78d6", "수": "#4a3aa7"}
PILLAR_LABELS = [("hour", "시주(時)"), ("day", "일주(日)"), ("month", "월주(月)"), ("year", "연주(年)")]
# STEM_ELEMENT/BRANCH_ELEMENT(아래 정의)는 오행을 한자(木火土金水)로 반환하는데,
# elem_count 등 집계용 딕셔너리는 한글 키(목화토금수)를 쓰므로 여기서 변환한다.
HANJA_ELEMENT_TO_KR = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}

# ── 신살 조견표 (사주첩경 전통 방식: 역마·도화·화개는 연지 삼합 기준, 천을귀인은 일간 기준) ──
BRANCH_CHARS = "子丑寅卯辰巳午未申酉戌亥"
STEM_CHARS = "甲乙丙丁戊己庚辛壬癸"

TRIAD_OF_BRANCH = {
    "寅": "인오술", "午": "인오술", "戌": "인오술",
    "申": "신자진", "子": "신자진", "辰": "신자진",
    "巳": "사유축", "酉": "사유축", "丑": "사유축",
    "亥": "해묘미", "卯": "해묘미", "未": "해묘미",
}
YEOKMA_TARGET = {"인오술": "申", "신자진": "寅", "사유축": "亥", "해묘미": "巳"}
DOHWA_TARGET = {"인오술": "卯", "신자진": "酉", "사유축": "午", "해묘미": "子"}
HWAGAE_TARGET = {"인오술": "戌", "신자진": "辰", "사유축": "丑", "해묘미": "未"}

STEM_GROUP = {
    "甲": "갑무경", "戊": "갑무경", "庚": "갑무경",
    "乙": "을기", "己": "을기",
    "丙": "병정", "丁": "병정",
    "辛": "신금",
    "壬": "임계", "癸": "임계",
}
CHEONEUL_TARGET = {
    "갑무경": {"丑", "未"}, "을기": {"子", "申"}, "병정": {"亥", "酉"},
    "신금": {"寅", "午"}, "임계": {"巳", "卯"},
}


# SAZU API는 skyFull/earthFull을 한자가 아닌 한글로 반환한다(예: 일간 己=='기토', 일지 卯=='묘목').
# 아래 두 매핑으로 한글→한자 역변환한다. "신"은 천간 辛과 지지 申이 둘 다 '신'으로 읽혀 충돌하므로,
# 반드시 두 매핑을 분리해두고, 호출부가 넘긴 charset(STEM_CHARS 또는 BRANCH_CHARS)으로 어느 쪽인지
# 문맥에 맞게 판별해야 한다(skyFull을 파싱할 땐 STEM_CHARS를 넘기므로 辛로, earthFull을 파싱할 땐
# BRANCH_CHARS를 넘기므로 申으로 정확히 갈린다).
STEM_HANGUL_TO_HANJA = {
    "갑": "甲", "을": "乙", "병": "丙", "정": "丁", "무": "戊",
    "기": "己", "경": "庚", "신": "辛", "임": "壬", "계": "癸",
}
BRANCH_HANGUL_TO_HANJA = {
    "자": "子", "축": "丑", "인": "寅", "묘": "卯", "진": "辰", "사": "巳",
    "오": "午", "미": "未", "신": "申", "유": "酉", "술": "戌", "해": "亥",
}


def _extract_char(text: str | None, charset: str) -> str | None:
    """표시용 문자열(예: SAZU의 '기토', '묘목')에서 원본 한자 1글자를 추출."""
    if not text:
        return None
    for ch in text:
        if ch in charset:
            return ch
        for hangul_map in (STEM_HANGUL_TO_HANJA, BRANCH_HANGUL_TO_HANJA):
            mapped = hangul_map.get(ch)
            if mapped and mapped in charset:
                return mapped
    return None


def compute_sinsal(fp: dict) -> dict:
    """역마·도화·화개(연지+일지 삼합 기준, 둘 다 체크)와 천을귀인(일간 기준)을 결정적으로 계산.

    사주첩경 전통은 연지 기준이 정석이지만, 원광만세력·루시아만세력 등 대중 만세력 앱들은
    일지 기준도 함께 보거나 일지를 기본으로 쓰는 경우가 많아 둘 다 체크합니다.

    fp: modules["fourPillars"] — {"year": {...}, "month": {...}, "day": {...}, "hour": {...}}
    각 pillar dict는 'earthFull'(지지 표시문자열), day pillar는 'skyFull'(일간 표시문자열) 포함.
    반환: {"역마": {"연지기준": [...], "일지기준": [...]}, "도화": {...}, "화개": {...},
           "천을귀인": [pillar_label,...]} — 찾은 게 없으면 빈 리스트.
    """
    PILLAR_KR = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}

    year_p = fp.get("year")
    day_p = fp.get("day")
    result = {
        "역마": {"연지기준": [], "일지기준": []},
        "도화": {"연지기준": [], "일지기준": []},
        "화개": {"연지기준": [], "일지기준": []},
        "천을귀인": [],
    }
    if year_p is None or day_p is None:
        return result

    year_branch = _extract_char(year_p.get("earthFull"), BRANCH_CHARS)
    day_branch = _extract_char(day_p.get("earthFull"), BRANCH_CHARS)
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS)

    branches_present = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if b:
            branches_present.setdefault(b, []).append(PILLAR_KR[key])

    for base_branch, base_label in ((year_branch, "연지기준"), (day_branch, "일지기준")):
        triad = TRIAD_OF_BRANCH.get(base_branch) if base_branch else None
        if not triad:
            continue
        for name, table in (("역마", YEOKMA_TARGET), ("도화", DOHWA_TARGET), ("화개", HWAGAE_TARGET)):
            target = table[triad]
            if target in branches_present:
                result[name][base_label] = branches_present[target]

    stem_group = STEM_GROUP.get(day_stem) if day_stem else None
    if stem_group:
        targets = CHEONEUL_TARGET[stem_group]
        found = []
        for t in targets:
            if t in branches_present:
                found.extend(branches_present[t])
        result["천을귀인"] = found

    return result


# ── 형충파해(合冲刑破害) 결정적 계산 — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ──
# 검증 끝나기 전까지 make_prompt()/format_sazu_context()에서 호출하지 않습니다.
# test_hyeongchunghae.py 로 별도 실행해 원광만세력·루시아만세력과 대조하세요.

STEM_HAP = {
    frozenset({"甲", "己"}): ("土", "갑기합토"),
    frozenset({"乙", "庚"}): ("金", "을경합금"),
    frozenset({"丙", "辛"}): ("水", "병신합수"),
    frozenset({"丁", "壬"}): ("木", "정임합목"),
    frozenset({"戊", "癸"}): ("火", "무계합화"),
}

BRANCH_YUKHAP = {
    frozenset({"子", "丑"}): ("土", "자축합토"),
    frozenset({"寅", "亥"}): ("木", "인해합목"),
    frozenset({"卯", "戌"}): ("火", "묘술합화"),
    frozenset({"辰", "酉"}): ("金", "진유합금"),
    frozenset({"巳", "申"}): ("水", "사신합수"),
    # 오미합은 화/토로 갈리는 등 학파에 따라 이견 있음 — 오행 미확정으로 표기
    frozenset({"午", "未"}): (None, "오미합(오행은 학파에 따라 화/토로 갈림)"),
}

TRIAD_GROUPS = {
    "인오술": {"왕지": "午", "생지": "寅", "고지": "戌", "오행": "火"},
    "신자진": {"왕지": "子", "생지": "申", "고지": "辰", "오행": "水"},
    "사유축": {"왕지": "酉", "생지": "巳", "고지": "丑", "오행": "金"},
    "해묘미": {"왕지": "卯", "생지": "亥", "고지": "未", "오행": "木"},
}

BANGHAP_GROUPS = {
    frozenset({"寅", "卯", "辰"}): ("木", "인묘진 방합(목방)"),
    frozenset({"巳", "午", "未"}): ("火", "사오미 방합(화방)"),
    frozenset({"申", "酉", "戌"}): ("金", "신유술 방합(금방)"),
    frozenset({"亥", "子", "丑"}): ("水", "해자축 방합(수방)"),
}

CHUNG_PAIRS = {
    frozenset({"子", "午"}): "자오충", frozenset({"丑", "未"}): "축미충",
    frozenset({"寅", "申"}): "인신충", frozenset({"卯", "酉"}): "묘유충",
    frozenset({"辰", "戌"}): "진술충", frozenset({"巳", "亥"}): "사해충",
}

PA_PAIRS = {
    frozenset({"子", "酉"}): "자유파", frozenset({"丑", "辰"}): "축진파",
    frozenset({"寅", "亥"}): "인해파", frozenset({"卯", "午"}): "묘오파",
    frozenset({"巳", "申"}): "사신파", frozenset({"戌", "未"}): "술미파",
}

HAE_PAIRS = {
    frozenset({"子", "未"}): "자미해", frozenset({"丑", "午"}): "축오해",
    frozenset({"寅", "巳"}): "인사해", frozenset({"卯", "辰"}): "묘진해",
    frozenset({"申", "亥"}): "신해해", frozenset({"酉", "戌"}): "유술해",
}

TRIPLE_HYEONG_GROUPS = [
    ({"寅", "巳", "申"}, "지세지형(寅巳申)"),
    ({"丑", "戌", "未"}, "무은지형(丑戌未)"),
]
DOUBLE_HYEONG = {frozenset({"子", "卯"}): "무례지형(子卯)"}
SELF_HYEONG_BRANCHES = {"辰", "午", "酉", "亥"}  # 같은 글자 2개 이상이면 자형

PILLAR_KR_STEM = {"year": "연간", "month": "월간", "day": "일간", "hour": "시간"}
PILLAR_KR_BRANCH = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}
ADJACENT_PAIRS = {frozenset({"year", "month"}), frozenset({"month", "day"}), frozenset({"day", "hour"})}


def compute_hyeongchunghae(fp: dict) -> dict:
    """4주 8자 원국에서 천간합·육합·삼합·반합·방합·충·형·파·해를 전부 결정적으로 계산.

    fp: modules["fourPillars"] — {"year":{...},"month":{...},"day":{...},"hour":{...}}
    반환: {"천간합":[...], "육합":[...], "삼합":[...], "반합":[...], "방합":[...],
           "충":[...], "형":[...], "파":[...], "해":[...]} — 각 항목은 사람이 읽을 문자열 리스트.
    """
    order = ["year", "month", "day", "hour"]
    stems, branches = {}, {}
    for key in order:
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if s:
            stems[key] = s
        if b:
            branches[key] = b

    result = {name: [] for name in ("천간합", "육합", "삼합", "반합", "방합", "충", "형", "파", "해")}

    def _adj(k1, k2):
        return "인접" if frozenset({k1, k2}) in ADJACENT_PAIRS else "원격"

    # 천간합
    skeys = list(stems.keys())
    for i in range(len(skeys)):
        for j in range(i + 1, len(skeys)):
            k1, k2 = skeys[i], skeys[j]
            pair = frozenset({stems[k1], stems[k2]})
            if pair in STEM_HAP:
                _, name = STEM_HAP[pair]
                result["천간합"].append(f"{PILLAR_KR_STEM[k1]}-{PILLAR_KR_STEM[k2]} {name}({_adj(k1, k2)})")

    # 지지 2개 조합: 육합·충·파·해·무례지형(자묘)
    bkeys = list(branches.keys())
    for i in range(len(bkeys)):
        for j in range(i + 1, len(bkeys)):
            k1, k2 = bkeys[i], bkeys[j]
            pair = frozenset({branches[k1], branches[k2]})
            label = f"{PILLAR_KR_BRANCH[k1]}-{PILLAR_KR_BRANCH[k2]}"
            adj = _adj(k1, k2)
            if pair in BRANCH_YUKHAP:
                _, name = BRANCH_YUKHAP[pair]
                result["육합"].append(f"{label} {name}({adj})")
            if pair in CHUNG_PAIRS:
                result["충"].append(f"{label} {CHUNG_PAIRS[pair]}({adj})")
            if pair in PA_PAIRS:
                result["파"].append(f"{label} {PA_PAIRS[pair]}({adj})")
            if pair in HAE_PAIRS:
                result["해"].append(f"{label} {HAE_PAIRS[pair]}({adj})")
            if pair in DOUBLE_HYEONG:
                result["형"].append(f"{label} {DOUBLE_HYEONG[pair]}({adj})")

    # 자형 (같은 지지가 2개 이상 겹칠 때만 성립하는 4글자 한정)
    branch_positions = {}
    for k, b in branches.items():
        branch_positions.setdefault(b, []).append(k)
    for b, ks in branch_positions.items():
        if b in SELF_HYEONG_BRANCHES and len(ks) >= 2:
            labels = ", ".join(PILLAR_KR_BRANCH[k] for k in ks)
            result["형"].append(f"{labels} 자형({b}{b})")

    present = set(branches.values())

    # 삼형 (세 글자 조합, 2/3만 있어도 부분 성립으로 표기)
    for group, name in TRIPLE_HYEONG_GROUPS:
        found = group & present
        if len(found) >= 2:
            found_labels = [PILLAR_KR_BRANCH[k] for k, b in branches.items() if b in found]
            status = "완전" if len(found) == 3 else "부분(2/3)"
            result["형"].append(f"{', '.join(found_labels)} {name} {status}")

    # 삼합/반합 (왕지 없는 생지+고지 조합은 불성립 처리 — 왕지 반드시 포함)
    for triad_name, info in TRIAD_GROUPS.items():
        wangji, saengji, goji, elem = info["왕지"], info["생지"], info["고지"], info["오행"]
        has_w, has_s, has_g = wangji in present, saengji in present, goji in present
        if has_w and has_s and has_g:
            result["삼합"].append(f"{triad_name} 삼합({elem}국) 완전 성립")
        elif has_w and has_s:
            result["반합"].append(f"{triad_name} 생지반합({saengji}{wangji})")
        elif has_w and has_g:
            result["반합"].append(f"{triad_name} 고지반합({wangji}{goji})")

    # 방합 (세 글자 모두 있어야 성립)
    for group_set, (_, name) in BANGHAP_GROUPS.items():
        if group_set <= present:
            result["방합"].append(name)

    return result


def format_hyeongchunghae(hch: dict) -> str:
    order = ["천간합", "육합", "삼합", "반합", "방합", "충", "형", "파", "해"]
    lines = []
    for name in order:
        items = hch.get(name, [])
        lines.append(f"  {name}: " + ("; ".join(items) if items else "없음"))
    return "\n".join(lines)


# ── 십성(十星)·12운성(포태법) 결정적 계산 — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ──

STEM_ELEMENT = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}
STEM_YINYANG = {  # True=양, False=음
    "甲": True, "丙": True, "戊": True, "庚": True, "壬": True,
    "乙": False, "丁": False, "己": False, "辛": False, "癸": False,
}
BRANCH_ELEMENT = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
    "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水",
}
BRANCH_YINYANG = {  # 양지: 子寅辰午申戌 / 음지: 丑卯巳未酉亥
    "子": True, "寅": True, "辰": True, "午": True, "申": True, "戌": True,
    "丑": False, "卯": False, "巳": False, "未": False, "酉": False, "亥": False,
}
ELEMENT_GENERATES = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}  # 상생
ELEMENT_CONTROLS = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}  # 상극

# 지지 십성 계산용 — 지장간 정기(正氣) 천간 (지지 자체 음양이 아닌, 정기 천간의 오행·음양을 써야 정확함)
BRANCH_JEONGGI_STEM = {
    "子": "癸", "丑": "己", "寅": "甲", "卯": "乙", "辰": "戊", "巳": "丙",
    "午": "丁", "未": "己", "申": "庚", "酉": "辛", "戌": "戊", "亥": "壬",
}


def sipseong_of(day_stem: str, target_element: str, target_yinyang: bool) -> str | None:
    """일간 대비 대상(천간 또는 지지)의 십성을 오행 관계+음양 일치 여부로 판정."""
    day_element = STEM_ELEMENT.get(day_stem)
    day_yinyang = STEM_YINYANG.get(day_stem)
    if day_element is None or day_yinyang is None:
        return None
    same_yy = day_yinyang == target_yinyang

    if target_element == day_element:
        return "비견" if same_yy else "겁재"
    if ELEMENT_GENERATES.get(day_element) == target_element:  # 일간이 생함
        return "식신" if same_yy else "상관"
    if ELEMENT_CONTROLS.get(day_element) == target_element:  # 일간이 극함
        return "편재" if same_yy else "정재"
    if ELEMENT_CONTROLS.get(target_element) == day_element:  # 대상이 일간을 극함
        return "편관" if same_yy else "정관"
    if ELEMENT_GENERATES.get(target_element) == day_element:  # 대상이 일간을 생함
        return "편인" if same_yy else "정인"
    return None


def compute_sipseong(fp: dict) -> dict:
    """4주 8자의 십성을 전부 계산. 일간 자신은 "일간(본인)"으로 표기(십성 없음).

    지지 십성은 지장간 정기(正氣) 천간의 오행·음양을 기준으로 계산합니다
    (지지 자체의 음양을 쓰면 子·巳·午·亥 네 지지에서 틀린 결과가 나옴 — 정기 천간 기준이 정확).
    반환: {"연간":..., "월간":..., "일간":"일간(본인)", "시간":...,
           "연지":..., "월지":..., "일지":..., "시지":...}
    """
    day_p = fp.get("day")
    if day_p is None:
        return {}
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS)
    if day_stem is None:
        return {}

    result = {}
    stem_labels = {"year": "연간", "month": "월간", "day": "일간", "hour": "시간"}
    branch_labels = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}

    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        if s:
            if key == "day":
                result[stem_labels[key]] = "일간(본인)"
            else:
                result[stem_labels[key]] = sipseong_of(day_stem, STEM_ELEMENT.get(s), STEM_YINYANG.get(s)) or "?"
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if b:
            jg_stem = BRANCH_JEONGGI_STEM.get(b)
            elem = STEM_ELEMENT.get(jg_stem) if jg_stem else BRANCH_ELEMENT.get(b)
            yy = STEM_YINYANG.get(jg_stem) if jg_stem else BRANCH_YINYANG.get(b)
            result[branch_labels[key]] = sipseong_of(day_stem, elem, yy) or "?"

    return result


def format_sipseong(sipseong: dict) -> str:
    order = ["연간", "연지", "월간", "월지", "일간", "일지", "시간", "시지"]
    return "\n".join(f"  {k}: {sipseong.get(k, '-')}" for k in order if k in sipseong)


# 12운성(포태법) — 일간별 장생 위치 + 순행(양간)/역행(음간)
BRANCH_ORDER = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
STAGE_NAMES = ["장생", "목욕", "관대", "건록", "제왕", "쇠", "병", "사", "묘", "절", "태", "양"]
STEM_JANGSAENG = {
    "甲": "亥", "丙": "寅", "戊": "寅", "庚": "巳", "壬": "申",
    "乙": "午", "丁": "酉", "己": "酉", "辛": "子", "癸": "卯",
}


def twelve_stage_of(day_stem: str, branch: str) -> str | None:
    start = STEM_JANGSAENG.get(day_stem)
    if start is None or branch not in BRANCH_ORDER:
        return None
    start_idx = BRANCH_ORDER.index(start)
    branch_idx = BRANCH_ORDER.index(branch)
    if STEM_YINYANG.get(day_stem):  # 양간 순행
        diff = (branch_idx - start_idx) % 12
    else:  # 음간 역행
        diff = (start_idx - branch_idx) % 12
    return STAGE_NAMES[diff]


def compute_twelve_stages(fp: dict) -> dict:
    """4주 지지 각각의 12운성(포태법)을 일간 기준으로 계산.

    반환: {"연지": "장생", "월지": "제왕", "일지": ..., "시지": ...}
    """
    day_p = fp.get("day")
    if day_p is None:
        return {}
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS)
    if day_stem is None:
        return {}

    labels = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}
    result = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if b:
            result[labels[key]] = twelve_stage_of(day_stem, b) or "?"
    return result


def format_twelve_stages(stages: dict) -> str:
    order = ["연지", "월지", "일지", "시지"]
    return "\n".join(f"  {k}: {stages.get(k, '-')}" for k in order if k in stages)


# ── 신살 확장 8종 — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ──────────
# 학파 간 이견이 큰 귀문관살·현침살·홍염살은 제외. 표가 명확히 검증되는 것만 포함.

MUNCHANG_TARGET = {  # 문창귀인: 일간이 생하는 식신 위치 (12운성 양간=병지/음간=장생지)
    "甲": "巳", "乙": "午", "丙": "申", "丁": "酉", "戊": "申",
    "己": "酉", "庚": "亥", "辛": "子", "壬": "寅", "癸": "卯",
}
AMROK_TARGET = {  # 암록: 건록의 육합 상대 지지
    "甲": "亥", "乙": "戌", "丙": "申", "丁": "未", "戊": "申",
    "己": "未", "庚": "巳", "辛": "辰", "壬": "寅", "癸": "丑",
}
GEUMYEO_TARGET = {  # 금여(록): 통용 조견표
    "甲": "辰", "乙": "巳", "丙": "未", "丁": "申", "戊": "未",
    "己": "申", "庚": "戌", "辛": "亥", "壬": "丑", "癸": "寅",
}
YANGIN_TARGET = {  # 양인살: 일간과 같은 오행의 제왕지 (양간만 성립, 음간은 해당없음)
    "甲": "卯", "丙": "午", "戊": "午", "庚": "酉", "壬": "子",
}
GOEGANG_PILLARS = {"庚辰", "庚戌", "壬辰", "壬戌"}  # 지니님 사주첩경 4·5권 요약 자료 기준
BAEKHO_PILLARS = {"甲辰", "乙未", "丙戌", "丁丑", "戊辰", "壬戌", "癸丑"}
WONJIN_PAIRS = {  # 원진살
    frozenset({"子", "未"}): "자미원진", frozenset({"丑", "午"}): "축오원진",
    frozenset({"寅", "酉"}): "인유원진", frozenset({"卯", "申"}): "묘신원진",
    frozenset({"辰", "亥"}): "진해원진", frozenset({"巳", "戌"}): "사술원진",
}
GONGMANG_PAIRS = {  # 순중공망: 각 순(旬)의 시작 지지 -> (공망1, 공망2)
    "子": ("戌", "亥"), "戌": ("申", "酉"), "申": ("午", "未"),
    "午": ("辰", "巳"), "辰": ("寅", "卯"), "寅": ("子", "丑"),
}


def compute_sinsal_extended(fp: dict) -> dict:
    """문창귀인·암록·금여·양인살·괴강살·백호살·원진살·공망을 결정적으로 계산.

    반환: {"문창귀인":[...], "암록":[...], "금여":[...], "양인살":[...],
           "괴강살":[...], "백호살":[...], "원진살":[...], "공망":[...]}
    """
    labels = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}
    branch_labels = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}
    result = {name: [] for name in ("문창귀인", "암록", "금여", "양인살", "괴강살", "백호살", "원진살", "공망")}

    day_p = fp.get("day")
    if day_p is None:
        return result
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS)
    day_branch = _extract_char(day_p.get("earthFull"), BRANCH_CHARS)
    if day_stem is None:
        return result

    branches_present = {}
    pillars_str = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if b:
            branches_present.setdefault(b, []).append(branch_labels[key])
        if s and b:
            pillars_str[key] = s + b

    # 일간 기준 (문창귀인·암록·금여·양인살)
    for name, table in (
        ("문창귀인", MUNCHANG_TARGET), ("암록", AMROK_TARGET),
        ("금여", GEUMYEO_TARGET), ("양인살", YANGIN_TARGET),
    ):
        target = table.get(day_stem)
        if target and target in branches_present:
            result[name] = branches_present[target]

    # 일주(간지 조합) 고정 리스트 — 모든 기둥에서 체크 (백호는 특히 일주에 있을 때 의미 큼)
    for key, pstr in pillars_str.items():
        if pstr in GOEGANG_PILLARS:
            result["괴강살"].append(labels[key])
        if pstr in BAEKHO_PILLARS:
            result["백호살"].append(labels[key])

    # 원진살 (지지 2개 조합)
    bkeys = list(branches_present.keys())
    checked = set()
    for b1 in bkeys:
        for b2 in bkeys:
            if b1 >= b2:
                continue
            pair = frozenset({b1, b2})
            if pair in WONJIN_PAIRS and pair not in checked:
                checked.add(pair)
                result["원진살"].append(
                    f"{'/'.join(branches_present[b1])}-{'/'.join(branches_present[b2])} {WONJIN_PAIRS[pair]}"
                )

    # 공망 (일주 기준, 연지·월지·시지 중 공망 지지가 있으면 표시. 일지 자체는 공망 대상 아님)
    if day_branch:
        # 일주가 속한 순(旬)의 갑(甲) 짝 지지를 역산
        stem_order = "甲乙丙丁戊己庚辛壬癸"
        branch_order12 = "子丑寅卯辰巳午未申酉戌亥"
        day_stem_idx = stem_order.index(day_stem)
        day_branch_idx = branch_order12.index(day_branch)
        gap_branch_idx = (day_branch_idx - day_stem_idx) % 12
        gap_branch = branch_order12[gap_branch_idx]
        gm1, gm2 = GONGMANG_PAIRS.get(gap_branch, (None, None))
        for gm in (gm1, gm2):
            if gm and gm in branches_present:
                for label in branches_present[gm]:
                    if label != "일지":  # 일지 자신은 정의상 공망이 될 수 없음
                        result["공망"].append(f"{label}({gm})")

    return result


# ── 신살 확장 2차 (9종) — 사주첩경 1권 요약 자료로 검증. 쉐도우 모드 ───────────
MUNGOK_TARGET = {  # 문곡귀인 (록후 4위)
    "甲": "亥", "乙": "子", "丙": "寅", "丁": "卯", "戊": "寅",
    "己": "卯", "庚": "巳", "辛": "午", "壬": "申", "癸": "酉",
}
HAKDANG_TARGET = {  # 학당귀인: 일간의 장생궁
    "甲": "亥", "乙": "午", "丙": "寅", "丁": "酉", "戊": "寅",
    "己": "酉", "庚": "巳", "辛": "子", "壬": "申", "癸": "卯",
}
GWIMUNGWAN_PAIRS = {  # 귀문관살 (생일지 기준)
    frozenset({"子", "酉"}): "자유귀문", frozenset({"丑", "午"}): "축오귀문",
    frozenset({"寅", "未"}): "인미귀문", frozenset({"卯", "申"}): "묘신귀문",
    frozenset({"辰", "亥"}): "진해귀문", frozenset({"巳", "戌"}): "사술귀문",
}
GEUPGAK_GROUP_OF = {  # 급각살: 생월 계절 그룹
    "寅": "인묘진", "卯": "인묘진", "辰": "인묘진",
    "巳": "사오미", "午": "사오미", "未": "사오미",
    "申": "신유술", "酉": "신유술", "戌": "신유술",
    "亥": "해자축", "子": "해자축", "丑": "해자축",
}
GEUPGAK_TARGET_BY_GROUP = {"인묘진": {"亥", "子"}, "사오미": {"卯", "未"}, "신유술": {"寅", "戌"}, "해자축": {"丑", "辰"}}
SUOK_TARGET_BY_TRIAD = {"신자진": "午", "인오술": "子", "사유축": "卯", "해묘미": "酉"}  # 수옥살(재살)
TANGHWA_DAY_TARGETS = {"寅": {"巳", "申"}, "午": {"午", "丑", "辰"}, "丑": {"午", "未", "戌"}}  # 탕화살
GORAN_PILLARS = {"甲寅", "乙巳", "丁巳", "戊申", "辛亥"}  # 고란살(신음살) — 여명 기준
NAKJEONG_STEM_GROUP = {
    "甲": "갑기", "己": "갑기", "乙": "을경", "庚": "을경", "丙": "병신",
    "辛": "병신", "丁": "정임", "壬": "정임", "戊": "무계", "癸": "무계",
}
NAKJEONG_TARGET_BY_GROUP = {"갑기": "巳", "을경": "子", "병신": "申", "정임": "戌", "무계": "卯"}  # 낙정관살
EUMYANG_CHACHAK_PILLARS = {  # 음양차착살 (일주 또는 시주)
    "丙子", "丙午", "辛卯", "辛酉", "丁丑", "丁未",
    "壬辰", "壬戌", "戊寅", "戊申", "癸巳", "癸亥",
}


def compute_sinsal_extended2(fp: dict) -> dict:
    """귀문관살·문곡귀인·학당귀인·급각살·수옥살·탕화살·고란살·낙정관살·음양차착살.

    반환 형식은 compute_sinsal_extended()와 동일 (신살명 -> 위치 라벨 리스트).
    """
    branch_labels = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}
    result = {name: [] for name in (
        "귀문관살", "문곡귀인", "학당귀인", "급각살", "수옥살",
        "탕화살", "고란살", "낙정관살", "음양차착살",
    )}

    day_p = fp.get("day")
    if day_p is None:
        return result
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS)
    day_branch = _extract_char(day_p.get("earthFull"), BRANCH_CHARS)
    hour_p = fp.get("hour")
    hour_branch = _extract_char(hour_p.get("earthFull"), BRANCH_CHARS) if hour_p else None
    hour_stem = _extract_char(hour_p.get("skyFull"), STEM_CHARS) if hour_p else None

    branches_present = {}
    pillars_str = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if b:
            branches_present.setdefault(b, []).append(branch_labels[key])
        if s and b:
            pillars_str[key] = s + b

    # 일간 기준 (문곡귀인·학당귀인)
    for name, table in (("문곡귀인", MUNGOK_TARGET), ("학당귀인", HAKDANG_TARGET)):
        target = table.get(day_stem)
        if target and target in branches_present:
            result[name] = branches_present[target]

    # 귀문관살: 생일지 기준, 다른 지지와의 조합
    if day_branch:
        for key, b in [(k, _extract_char((fp.get(k) or {}).get("earthFull"), BRANCH_CHARS))
                        for k in ("year", "month", "hour")]:
            if not b:
                continue
            pair = frozenset({day_branch, b})
            if pair in GWIMUNGWAN_PAIRS:
                result["귀문관살"].append(f"일지-{branch_labels[key]}")

    # 급각살: 생월 계절 그룹 기준으로 년·일·시지 확인
    month_p = fp.get("month")
    month_branch = _extract_char(month_p.get("earthFull"), BRANCH_CHARS) if month_p else None
    if month_branch:
        group = GEUPGAK_GROUP_OF.get(month_branch)
        targets = GEUPGAK_TARGET_BY_GROUP.get(group, set())
        for t in targets:
            if t in branches_present:
                for label in branches_present[t]:
                    if label != "월지":
                        result["급각살"].append(label)

    # 수옥살(재살): 연지·일지 기준 삼합 왕지 대칭 (역마와 같은 방식)
    year_p = fp.get("year")
    year_branch = _extract_char(year_p.get("earthFull"), BRANCH_CHARS) if year_p else None
    for base_branch in (year_branch, day_branch):
        triad = TRIAD_OF_BRANCH.get(base_branch) if base_branch else None
        if triad:
            target = SUOK_TARGET_BY_TRIAD.get(triad)
            if target and target in branches_present:
                for label in branches_present[target]:
                    if label not in result["수옥살"]:
                        result["수옥살"].append(label)

    # 탕화살: 일지가 寅/午/丑일 때 다른 기둥에 대응 지지가 있는지
    if day_branch in TANGHWA_DAY_TARGETS:
        targets = TANGHWA_DAY_TARGETS[day_branch]
        for key in ("year", "month", "hour"):
            p = fp.get(key)
            b = _extract_char((p or {}).get("earthFull"), BRANCH_CHARS) if p else None
            if b in targets:
                result["탕화살"].append(branch_labels[key])

    # 고란살(신음살) — 여명 기준 일주 고정 리스트 (성별 무관하게 계산만, 해석시 여명 한정 적용 권장)
    if pillars_str.get("day") in GORAN_PILLARS:
        result["고란살"].append("일주")

    # 낙정관살: 일간 기준 그룹 -> 목표 지지가 일지 또는 시지에 있는지
    group = NAKJEONG_STEM_GROUP.get(day_stem)
    target = NAKJEONG_TARGET_BY_GROUP.get(group) if group else None
    if target:
        if day_branch == target:
            result["낙정관살"].append("일지")
        if hour_branch == target:
            result["낙정관살"].append("시지")

    # 음양차착살: 일주 또는 시주가 고정 리스트에 있는지
    if pillars_str.get("day") in EUMYANG_CHACHAK_PILLARS:
        result["음양차착살"].append("일주")
    if pillars_str.get("hour") in EUMYANG_CHACHAK_PILLARS:
        result["음양차착살"].append("시주")

    return result


# ── 격국 특수원리 9종 — 사주첩경 6권 요약 자료로 검증. 쉐도우 모드 ──────────────
BUSEONG_IMMYO_TARGETS = {  # 부성입묘: 일간별 관성이 묘궁에 있는 간지
    "甲": ["辛丑"], "乙": ["辛丑"], "丙": ["壬辰"], "丁": ["壬辰"],
    "戊": ["乙未"], "己": ["乙未"], "庚": ["丙戌", "戊戌"], "辛": ["丙戌", "戊戌"],
    "壬": ["戊辰"], "癸": ["戊辰"],
}


def compute_gyeokguk_teukjip(fp: dict, sipseong: dict) -> dict:
    """격국 특수원리 9종: 부성입묘·진상관·가상관·관살혼잡·살인상생·탐재괴인·군겁쟁재·등라계갑·순환상생·살인상정.

    반환: {"부성입묘": True/False, ...} 각 패턴 성립 여부(불리언).
    """
    result = {}
    day_p = fp.get("day")
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS) if day_p else None

    pillars_str = []
    stems_present = []
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if s:
            stems_present.append(s)
        if s and b:
            pillars_str.append(s + b)

    # 1. 부성입묘
    targets = BUSEONG_IMMYO_TARGETS.get(day_stem, [])
    result["부성입묘"] = any(t in pillars_str for t in targets)

    # 2·3. 진상관/가상관 (상호 배타)
    wolji_sipseong = sipseong.get("월지")
    other_values = [v for k, v in sipseong.items() if k not in ("일간", "월지")]
    if wolji_sipseong == "상관":
        result["진상관"], result["가상관"] = True, False
    elif "상관" in other_values:
        result["진상관"], result["가상관"] = False, True
    else:
        result["진상관"], result["가상관"] = False, False

    sipseong_values = [v for k, v in sipseong.items() if k != "일간"]
    has_jae = "정재" in sipseong_values or "편재" in sipseong_values
    has_in = "정인" in sipseong_values or "편인" in sipseong_values

    # 4. 관살혼잡
    result["관살혼잡"] = "정관" in sipseong_values and "편관" in sipseong_values
    # 5. 살인상생
    result["살인상생"] = "편관" in sipseong_values and has_in
    # 6. 탐재괴인
    result["탐재괴인"] = has_jae and has_in
    # 7. 군겁쟁재 (비겁 2개 이상 + 재성)
    bigyeop_count = sum(1 for v in sipseong_values if v in ("비견", "겁재"))
    result["군겁쟁재"] = bigyeop_count >= 2 and has_jae
    # 8. 등라계갑 (을일간 + 갑목 존재)
    result["등라계갑"] = day_stem == "乙" and "甲" in stems_present
    # 9. 순환상생 (오행 5개 전부 존재)
    elem_count_hanja = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if s and s in STEM_ELEMENT:
            elem_count_hanja[STEM_ELEMENT[s]] += 1
        if b and b in BRANCH_ELEMENT:
            elem_count_hanja[BRANCH_ELEMENT[b]] += 1
    result["순환상생"] = all(c >= 1 for c in elem_count_hanja.values())
    # 10. 살인상정 (편관 + 양인살)
    ext = compute_sinsal_extended(fp)
    result["살인상정"] = "편관" in sipseong_values and bool(ext.get("양인살"))

    return result



def format_sinsal_extended(ext: dict) -> str:
    order = ["문창귀인", "암록", "금여", "양인살", "괴강살", "백호살", "원진살", "공망"]
    lines = []
    for name in order:
        items = ext.get(name, [])
        lines.append(f"  {name}: " + ("; ".join(items) if items else "없음"))
    return "\n".join(lines)


def format_sinsal_extended2(ext: dict) -> str:
    order = ["귀문관살", "문곡귀인", "학당귀인", "급각살", "수옥살", "탕화살", "고란살", "낙정관살", "음양차착살"]
    lines = []
    for name in order:
        items = ext.get(name, [])
        lines.append(f"  {name}: " + ("; ".join(items) if items else "없음"))
    return "\n".join(lines)


# ── 지장간·납음오행 — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ────────
# 지장간은 여기/중기/정기 "구성 천간"만 표시 (정확한 날짜 배분은 책마다 이견 있어 제외).
# 오화(午)의 중기(己)는 포함 여부가 갈리는 지점이라 별도 표시.

JIJANGGAN = {  # 각 지지: [(천간, 단계), ...] — 여기/중기/정기 명시적 지정(슬라이싱으로 유추하지 않음)
    "子": [("壬", "여기"), ("癸", "정기")],
    "丑": [("癸", "여기"), ("辛", "중기"), ("己", "정기")],
    "寅": [("戊", "여기"), ("丙", "중기"), ("甲", "정기")],
    "卯": [("甲", "여기"), ("乙", "정기")],
    "辰": [("乙", "여기"), ("癸", "중기"), ("戊", "정기")],
    "巳": [("戊", "여기"), ("庚", "중기"), ("丙", "정기")],
    "午": [("丙", "여기"), ("己", "중기"), ("丁", "정기")],  # 己(중기) 포함 여부는 학파에 따라 갈림
    "未": [("丁", "여기"), ("乙", "중기"), ("己", "정기")],
    "申": [("戊", "여기"), ("壬", "중기"), ("庚", "정기")],
    "酉": [("庚", "여기"), ("辛", "정기")],
    "戌": [("辛", "여기"), ("丁", "중기"), ("戊", "정기")],
    "亥": [("戊", "여기"), ("甲", "중기"), ("壬", "정기")],
}

# 납음오행 — 60갑자 고정표 (이견 없음)
NAYIN_TABLE = {
    "甲子": "해중금", "乙丑": "해중금", "丙寅": "노중화", "丁卯": "노중화",
    "戊辰": "대림목", "己巳": "대림목", "庚午": "노방토", "辛未": "노방토",
    "壬申": "검봉금", "癸酉": "검봉금", "甲戌": "산두화", "乙亥": "산두화",
    "丙子": "간하수", "丁丑": "간하수", "戊寅": "성두토", "己卯": "성두토",
    "庚辰": "백랍금", "辛巳": "백랍금", "壬午": "양류목", "癸未": "양류목",
    "甲申": "천중수", "乙酉": "천중수", "丙戌": "옥상토", "丁亥": "옥상토",
    "戊子": "벽력화", "己丑": "벽력화", "庚寅": "송백목", "辛卯": "송백목",
    "壬辰": "장류수", "癸巳": "장류수", "甲午": "사중금", "乙未": "사중금",
    "丙申": "산하화", "丁酉": "산하화", "戊戌": "평지목", "己亥": "평지목",
    "庚子": "벽상토", "辛丑": "벽상토", "壬寅": "금박금", "癸卯": "금박금",
    "甲辰": "복등화", "乙巳": "복등화", "丙午": "천하수", "丁未": "천하수",
    "戊申": "대역토", "己酉": "대역토", "庚戌": "차천금", "辛亥": "차천금",
    "壬子": "상자목", "癸丑": "상자목", "甲寅": "대계수", "乙卯": "대계수",
    "丙辰": "사중토", "丁巳": "사중토", "戊午": "천상화", "己未": "천상화",
    "庚申": "석류목", "辛酉": "석류목", "壬戌": "대해수", "癸亥": "대해수",
}


def compute_jijanggan(fp: dict) -> dict:
    """4주 각 지지의 지장간(숨은 천간) 구성을 반환. 날짜 배분 없이 단계(여기/중기/정기)만.

    반환: {"연지": [("戊","여기"),("丙","중기"),("甲","정기")], "월지": [...], ...}
    """
    labels = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}
    result = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if b and b in JIJANGGAN:
            result[labels[key]] = JIJANGGAN[b]
    return result


def format_jijanggan(jjg: dict) -> str:
    order = ["연지", "월지", "일지", "시지"]
    lines = []
    for k in order:
        pairs = jjg.get(k)
        if pairs:
            text = ", ".join(f"{stem}({stage})" for stem, stage in pairs)
            lines.append(f"  {k}: {text}")
    return "\n".join(lines)


def compute_nayin(fp: dict) -> dict:
    """4주 각 기둥(간지 조합)의 납음오행을 반환.

    반환: {"연주": "해중금", "월주": ..., "일주": ..., "시주": ...}
    """
    labels = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}
    result = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if s and b:
            pillar = s + b
            result[labels[key]] = NAYIN_TABLE.get(pillar, "?")
    return result


def format_nayin(nayin: dict) -> str:
    order = ["연주", "월주", "일주", "시주"]
    return "\n".join(f"  {k}: {nayin.get(k, '-')}" for k in order if k in nayin)


# ── 출생시각 역사적 보정 — 서머타임 + 표준시 기준 변경 ──────────────────────────
# 1) 서머타임(일광절약시간제) 12개 기간: 시계가 1시간 앞당겨져 있었으므로 -1시간
# 2) 127도30분 표준시 기간(1908~1911, 1954~1961): 당시 UTC+8:30을 표준시로 썼으므로
#    진태양시 보정 시 기준경도를 135도가 아닌 127.5도로 써야 함(둘 다 sajuplus.com 척척사주
#    공개 자료 기준 — 정부 문헌 대조 권장).
DST_PERIODS = [  # (시작, 종료) — 시작 이상 종료 미만이면 서머타임 적용 중
    (datetime(1948, 6, 1, 0, 0), datetime(1948, 9, 13, 0, 0)),
    (datetime(1949, 4, 3, 0, 0), datetime(1949, 9, 11, 0, 0)),
    (datetime(1950, 4, 1, 0, 0), datetime(1950, 9, 10, 0, 0)),
    (datetime(1951, 5, 6, 0, 0), datetime(1951, 9, 9, 0, 0)),
    (datetime(1955, 5, 5, 0, 0), datetime(1955, 9, 9, 0, 0)),
    (datetime(1956, 5, 20, 0, 0), datetime(1956, 9, 30, 0, 0)),
    (datetime(1957, 5, 5, 0, 0), datetime(1957, 9, 22, 0, 0)),
    (datetime(1958, 5, 4, 0, 0), datetime(1958, 9, 21, 0, 0)),
    (datetime(1959, 5, 3, 0, 0), datetime(1959, 9, 20, 0, 0)),
    (datetime(1960, 5, 1, 0, 0), datetime(1960, 9, 18, 0, 0)),
    (datetime(1987, 5, 10, 2, 0), datetime(1987, 10, 11, 3, 0)),
    (datetime(1988, 5, 8, 2, 0), datetime(1988, 10, 9, 3, 0)),
]
STANDARD_1275_PERIODS = [  # 127도30분(UTC+8:30) 표준시 사용 기간
    (datetime(1908, 4, 1, 0, 0), datetime(1912, 1, 1, 0, 0)),
    (datetime(1954, 3, 21, 0, 0), datetime(1961, 8, 10, 0, 0)),
]


def is_dst_period(civil_dt) -> bool:
    return any(start <= civil_dt < end for start, end in DST_PERIODS)


def standard_utc_offset_for(civil_dt) -> float:
    """해당 시점 한국의 실제 공식 표준시 UTC 오프셋 (평상시 9, 127도30분 기간엔 8.5)."""
    if any(start <= civil_dt < end for start, end in STANDARD_1275_PERIODS):
        return 8.5
    return 9.0


def correct_birth_datetime(civil_dt, longitude: float = 126.978, use_solar_time: bool = True):
    """시계에 적힌 출생시각(civil_dt)을 서머타임+표준시 기준 변경까지 반영해 보정.

    1) 서머타임 기간이면 1시간 빼서 당시 공식 표준시로 환산
    2) use_solar_time=True면, 그 시점의 실제 표준경도(127.5 또는 135)를 기준으로
       진태양시 보정(경도 1도당 4분)까지 적용
    반환: (보정된 datetime, 적용내역 dict)
    """
    applied = {"서머타임_보정": False, "표준시_기준": None, "경도보정_분": 0.0}

    dt = civil_dt
    if is_dst_period(civil_dt):
        dt = dt - timedelta(hours=1)
        applied["서머타임_보정"] = True

    std_offset = standard_utc_offset_for(civil_dt)
    std_longitude = std_offset * 15  # UTC+9→135도, UTC+8.5→127.5도
    applied["표준시_기준"] = f"UTC+{std_offset} ({std_longitude}도)"

    if use_solar_time:
        correction_minutes = (longitude - std_longitude) * 4  # 경도 1도=4분
        dt = dt + timedelta(minutes=correction_minutes)
        applied["경도보정_분"] = round(correction_minutes, 2)

    return dt, applied


# ── 대운(大運) — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ────────────
# 다른 모듈과 달리 순수 조견표만으로는 계산이 불가능합니다. 대운수(시작 나이)는
# 생시부터 가장 가까운 절기(節)까지의 정확한 일수(분 단위)가 필요해서, 매년 날짜가
# 바뀌는 실제 천문 데이터(sajupy의 절기 시각 테이블)에 의존합니다. sajupy가 설치되지
# 않은 환경에서는 이 함수들이 동작하지 않습니다(다른 모듈은 전부 순수 파이썬만으로 동작).
# ※ 대운수 계산에 넘기는 birth_dt는 반드시 correct_birth_datetime()을 거친 값이어야 합니다
#   (서머타임·127도30분 표준시 기간 출생자는 보정 없이 계산하면 대운수가 틀릴 수 있음).

JEOL_NAMES = {"입춘", "경칩", "청명", "입하", "망종", "소서", "입추", "백로", "한로", "입동", "대설", "소한"}

_daewoon_calendar_cache = None


def _get_jeol_table():
    """sajupy 내부 절기 시각 테이블을 '절'(節) 12개만 걸러서 반환 (지연 로딩+캐시)."""
    global _daewoon_calendar_cache
    if _daewoon_calendar_cache is not None:
        return _daewoon_calendar_cache
    from sajupy import get_saju_calculator
    calc = get_saju_calculator()
    df = calc.data
    jeol = df[df["solar_term_korean"].isin(JEOL_NAMES)].copy()
    jeol["dt"] = pd.to_datetime(jeol["term_time"].astype("int64").astype(str), format="%Y%m%d%H%M")
    _daewoon_calendar_cache = jeol
    return jeol


def _nearest_jeol(birth_dt, direction: str):
    """direction: 'next'(순행용, 다음 절) 또는 'prev'(역행용, 이전 절)."""
    jeol = _get_jeol_table()
    if direction == "next":
        cand = jeol[jeol["dt"] > birth_dt].sort_values("dt")
    else:
        cand = jeol[jeol["dt"] < birth_dt].sort_values("dt")
    if cand.empty:
        return None
    row = cand.iloc[0] if direction == "next" else cand.iloc[-1]
    return row["solar_term_korean"], row["dt"]


def _ganji_index(stem: str, branch: str) -> int | None:
    for i in range(60):
        if STEM_CHARS[i % 10] == stem and BRANCH_CHARS[i % 12] == branch:
            return i
    return None


def _ganji_at(index: int) -> str:
    index %= 60
    return STEM_CHARS[index % 10] + BRANCH_CHARS[index % 12]


def compute_daewoon(fp: dict, civil_birth_dt, gender: str, longitude: float = 126.978,
                     num_periods: int = 9) -> dict:
    """대운 방향(순행/역행)·대운수(시작 나이)·대운 간지 순서를 계산.

    fp: modules["fourPillars"]
    civil_birth_dt: 시계에 적힌 출생 시각 그대로 (datetime) — 보정은 이 함수가 자동으로 함
        (서머타임 12개 기간 + 1908~1911·1954~1961 127도30분 표준시 기간 + 경도 기반
        진태양시 보정을 correct_birth_datetime()으로 내부에서 전부 적용).
    gender: "남" 또는 "여"
    longitude: 출생지 경도 (기본값 서울)
    반환: {"방향": "순행"/"역행", "대운수_원값": 9.063, "대운수": 9,
           "기준절기": ("입춘", datetime), "periods": [{"나이": 9, "간지": "乙丑"}, ...]}
    """
    birth_dt, _correction_applied = correct_birth_datetime(civil_birth_dt, longitude=longitude)

    year_p, month_p = fp.get("year"), fp.get("month")
    if year_p is None or month_p is None:
        return {}
    year_stem = _extract_char(year_p.get("skyFull"), STEM_CHARS)
    month_stem = _extract_char(month_p.get("skyFull"), STEM_CHARS)
    month_branch = _extract_char(month_p.get("earthFull"), BRANCH_CHARS)
    if not (year_stem and month_stem and month_branch):
        return {}

    year_yang = STEM_YINYANG.get(year_stem)
    is_male = gender in ("남", "남자", "male", "M")
    forward = (is_male and year_yang) or (not is_male and not year_yang)

    direction = "next" if forward else "prev"
    jeol = _nearest_jeol(birth_dt, direction)
    if jeol is None:
        return {}
    jeol_name, jeol_dt = jeol
    days = abs((jeol_dt - birth_dt).total_seconds()) / 86400
    daewoon_su_raw = days / 3
    daewoon_su = round(daewoon_su_raw)

    month_idx = _ganji_index(month_stem, month_branch)
    if month_idx is None:
        return {}
    step = 1 if forward else -1

    PILLAR_KR_BARE = {"year": "연", "month": "월", "day": "일", "hour": "시"}
    periods = []
    for i in range(1, num_periods + 1):
        ganji = _ganji_at(month_idx + step * i)
        dw_stem, dw_branch = ganji[0], ganji[1]
        age = daewoon_su + (i - 1) * 10

        interactions = {name: [] for name in ("천간합", "육합", "충", "형", "파", "해")}
        for key in ("year", "month", "day", "hour"):
            p = fp.get(key)
            if p is None:
                continue
            s = _extract_char(p.get("skyFull"), STEM_CHARS)
            b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
            _accumulate_ganji_interaction(interactions, PILLAR_KR_BARE[key], s, b, dw_stem, dw_branch)

        periods.append({"나이": age, "간지": ganji, "충돌": interactions})

    return {
        "방향": "순행" if forward else "역행",
        "대운수_원값": round(daewoon_su_raw, 3),
        "대운수": daewoon_su,
        "기준절기": (jeol_name, jeol_dt),
        "periods": periods,
    }


def format_daewoon(dw: dict) -> str:
    if not dw:
        return "  (계산 불가 — sajupy 미설치 또는 데이터 부족)"
    jeol_name, jeol_dt = dw["기준절기"]
    lines = [
        f"  방향: {dw['방향']} (기준절기: {jeol_name} {jeol_dt.strftime('%Y-%m-%d %H:%M')})",
        f"  대운수: {dw['대운수']}세 (원값 {dw['대운수_원값']})",
    ]
    for p in dw["periods"]:
        detail = []
        for name in ("천간합", "육합", "충", "형", "파", "해"):
            items = p["충돌"].get(name, [])
            if items:
                detail.append(f"{name}:" + "/".join(items))
        detail_str = " — " + "; ".join(detail) if detail else ""
        lines.append(f"    {p['나이']}세~ : {p['간지']}{detail_str}")
    return "\n".join(lines)


# ── 세운(歲運) — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ────────────
# 연주 자체는 순수 공식(60갑자 주기, 서기 4년=갑자년 기준)만으로 계산 가능해 sajupy 불필요.
# 다만 "어느 날짜가 어느 해의 세운에 속하는지"는 입춘 경계가 필요해 그 판정에서만 sajupy 사용.


def compute_year_ganji(year: int) -> str:
    """서기 연도(사주상 연도, 입춘 기준)의 60갑자 연주. 서기 4년=甲子년 기준 공식."""
    s = STEM_CHARS[(year - 4) % 10]
    b = BRANCH_CHARS[(year - 4) % 12]
    return s + b


def sewoon_year_for_date(dt) -> int:
    """실제 날짜 dt가 사주상 몇 년도 세운에 속하는지 판정 (입춘 이전이면 전년도).

    sajupy 절기 데이터 필요 — 구할 수 없으면 단순히 dt.year 반환(근사치, 1~2월만 부정확할 수 있음).
    """
    try:
        jeol = _get_jeol_table()
        ipchun = jeol[(jeol["solar_term_korean"] == "입춘") & (jeol["dt"].dt.year == dt.year)]
        if not ipchun.empty:
            ipchun_dt = ipchun.iloc[0]["dt"]
            return dt.year if dt >= ipchun_dt else dt.year - 1
    except Exception:
        pass
    return dt.year  # 근사치 폴백


def format_sewoon(se_stem: str, se_branch: str) -> str:
    return f"  세운 간지: {se_stem}{se_branch}"


# ── 자시(子時) 정책 — 지니님 결정: 조자시/야자시 구분 없이 통합해서 씀 (최근 대세) ──
# 23:00~24:00 출생자는 조자시/야자시를 나누지 않고, 23시부터 이미 다음날 자시로
# 하나로 취급합니다. sajupy 호출 시 early_zi_time=False로 고정.
# 원국을 sajupy로 직접 계산하게 될 때(현재는 SAZU API 사용 중) 이 상수를 그대로 넘기세요.
EARLY_ZI_TIME = False


# ── 월운(月運) — 아직 프롬프트/화면에 미연결 (쉐도우 모드, 검증 전용) ────────────
# 월주 자체는 오호둔(五虎遁) 공식으로 직접 계산도 가능하지만(2026년 5개 날짜로 sajupy와
# 교차검증 완료, 전부 일치), 절기 경계 판정까지 다시 구현하는 중복을 피하려고
# sajupy.calculate_saju()를 그대로 호출해 월주를 가져옵니다. sajupy 필요.


def compute_wolwoon_ganji(target_dt) -> tuple[str, str] | None:
    """target_dt(해당 월의 아무 날, 정오 권장)의 월운 간지(월주)를 sajupy로 계산.

    정오(12시) 계산이라 EARLY_ZI_TIME 정책은 영향 없음 — 자시 근처가 아니므로.
    """
    try:
        from sajupy import calculate_saju
        r = calculate_saju(
            year=target_dt.year, month=target_dt.month, day=target_dt.day,
            hour=12, minute=0, use_solar_time=False, early_zi_time=EARLY_ZI_TIME,
        )
        return r["month_stem"], r["month_branch"]
    except Exception:
        return None


def _accumulate_ganji_interaction(result: dict, label: str, s: str | None, b: str | None, t_stem: str, t_branch: str):
    """공통 로직: (s,b) 기둥과 (t_stem,t_branch) 대상 사이의 합·충·형·파·해를 result에 누적.

    대운의 원국 대조에 사용 (세운·월운은 관계분석 없이 간지만 표시하기로 함).
    """
    if s:
        pair = frozenset({s, t_stem})
        if pair in STEM_HAP:
            _, name = STEM_HAP[pair]
            result["천간합"].append(f"{label}간({s}) {name}")
    if b:
        pair = frozenset({b, t_branch})
        if pair in BRANCH_YUKHAP:
            _, name = BRANCH_YUKHAP[pair]
            result["육합"].append(f"{label}지({b}) {name}")
        if pair in CHUNG_PAIRS:
            result["충"].append(f"{label}지({b}) {CHUNG_PAIRS[pair]}")
        if pair in PA_PAIRS:
            result["파"].append(f"{label}지({b}) {PA_PAIRS[pair]}")
        if pair in HAE_PAIRS:
            result["해"].append(f"{label}지({b}) {HAE_PAIRS[pair]}")
        if pair in DOUBLE_HYEONG:
            result["형"].append(f"{label}지({b}) {DOUBLE_HYEONG[pair]}")
        if b == t_branch and b in SELF_HYEONG_BRANCHES:
            result["형"].append(f"{label}지({b}) 자형({b}{b})")


def format_wolwoon(wo_stem: str, wo_branch: str) -> str:
    return f"  월운 간지: {wo_stem}{wo_branch}"


def format_sinsal(sinsal: dict) -> str:
    lines = []
    for name in ("역마", "도화", "화개"):
        by_base = sinsal.get(name, {})
        found = []
        for base_label in ("연지기준", "일지기준"):
            pillars = by_base.get(base_label, [])
            if pillars:
                found.append(f"{', '.join(pillars)}[{base_label}]")
        lines.append(f"  {name}: " + ("있음 (" + "; ".join(found) + ")" if found else "없음"))
    pillars = sinsal.get("천을귀인", [])
    lines.append("  천을귀인: " + ("있음 (" + ", ".join(pillars) + ")" if pillars else "없음"))
    return "\n".join(lines)


# ── 지니님 사주첩경 요약 자료 — 신살/격국/육친 판정에 따라 조건부로만 포함 ──────
# 폴더 구조: references/sinsal/<신살명>.md, references/gyeokguk/{general,종격}.md,
#            references/yukchin/general.md — 파일이 없으면 조용히 건너뜀(에러 없음).
REFERENCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "references")


def _load_ref(*parts: str) -> str | None:
    path = os.path.join(REFERENCES_DIR, *parts)
    if os.path.isfile(path):
        text = open(path, encoding="utf-8").read().strip()
        return text or None
    return None


def _sinsal_found(sinsal: dict, name: str) -> bool:
    """name에 해당하는 신살이 (연지기준이든 일지기준이든) 하나라도 있으면 True."""
    value = sinsal.get(name)
    if isinstance(value, dict):  # 역마·도화·화개: {"연지기준": [...], "일지기준": [...]}
        return any(value.get(base) for base in ("연지기준", "일지기준"))
    return bool(value)  # 천을귀인: 리스트


# 월지 십성 → 정격 파일명 (지니님 사주첩경 4·5권 요약 기준)
JEONGGYEOK_FILE_MAP = {
    "정관": "정관격", "편관": "편관격",
    "정인": "인수격", "편인": "인수격",
    "정재": "정재격", "편재": "편재격",
    "식신": "식신격", "상관": "상관격",
    "겁재": "양인격", "비견": "건록격",
}
# 일귀격·일덕격 — 일주(간지 조합) 고정 리스트 (지니님 요약 자료 기준)
ILGWI_PILLARS = {"癸卯", "癸巳", "丁酉", "丁亥"}
ILDEOK_PILLARS = {"甲寅", "丙辰", "戊辰", "庚辰", "壬戌"}
# 괴강격은 신살용 GOEGANG_PILLARS(위에서 지니님 자료 기준으로 이미 수정됨)를 그대로 재사용
# 오행전왕격 — (일간오행, 필요조건 지지 3~4개, 파일명)
OHAENG_JEONWANG = [
    ("木", {"亥", "卯", "未"}, "오행전왕격"),  # 곡직격
    ("火", {"寅", "午", "戌"}, "오행전왕격"),  # 염상격
    ("土", {"辰", "戌", "丑", "未"}, "오행전왕격"),  # 가색격
    ("金", {"巳", "酉", "丑"}, "오행전왕격"),  # 종혁격
    ("水", {"申", "子", "辰"}, "오행전왕격"),  # 윤하격
]


def collect_references(fp: dict, sinsal: dict, sipseong: dict, sin_strength_score: float | None, gender_label: str) -> str:
    """이 사주에 실제로 해당하는 참고자료만 골라서 반환. 관련 없는 자료는 아예 포함하지 않음."""
    blocks = []

    for name in ("역마", "도화", "천을귀인", "화개"):
        if _sinsal_found(sinsal, name):
            text = _load_ref("sinsal", f"{name}.md")
            if text:
                blocks.append(f"### [신살] {name}\n{text}")

    gen = _load_ref("gyeokguk", "general.md")
    if gen:
        blocks.append(f"### [격국] 총론\n{gen}")

    ys_gen = _load_ref("yongsin", "general.md")
    if ys_gen:
        blocks.append(f"### [용신] 5유형 총론\n{ys_gen}")

    # 월지 십성 기준 정격 10종 중 해당하는 것만
    wolji_sipseong = sipseong.get("월지")
    jeonggyeok_file = JEONGGYEOK_FILE_MAP.get(wolji_sipseong)
    if jeonggyeok_file:
        text = _load_ref("gyeokguk", "정격", f"{jeonggyeok_file}.md")
        if text:
            blocks.append(f"### [격국] {jeonggyeok_file} (월지 십성={wolji_sipseong})\n{text}")

    # 일주 고정 리스트 기준 특수격
    day_p = fp.get("day")
    day_ganji = None
    if day_p:
        s = _extract_char(day_p.get("skyFull"), STEM_CHARS)
        b = _extract_char(day_p.get("earthFull"), BRANCH_CHARS)
        if s and b:
            day_ganji = s + b
    if day_ganji in ILGWI_PILLARS:
        text = _load_ref("gyeokguk", "특수", "일귀격.md")
        if text:
            blocks.append(f"### [격국] 일귀격 (일주={day_ganji})\n{text}")
    if day_ganji in ILDEOK_PILLARS:
        text = _load_ref("gyeokguk", "특수", "일덕격.md")
        if text:
            blocks.append(f"### [격국] 일덕격 (일주={day_ganji})\n{text}")
    if day_ganji in GOEGANG_PILLARS:
        text = _load_ref("gyeokguk", "특수", "괴강격.md")
        if text:
            blocks.append(f"### [격국] 괴강격 (일주={day_ganji})\n{text}")

    # 오행전왕격 — 일간 오행 + 해당 지지 전부 있을 때만
    day_stem = _extract_char(day_p.get("skyFull"), STEM_CHARS) if day_p else None
    day_element = STEM_ELEMENT.get(day_stem) if day_stem else None
    branches_present = set()
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p:
            b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
            if b:
                branches_present.add(b)
    for elem, required, fname in OHAENG_JEONWANG:
        if day_element == elem and required <= branches_present:
            text = _load_ref("gyeokguk", "특수", f"{fname}.md")
            if text:
                blocks.append(f"### [격국] 오행전왕격 (일간오행={elem}, 지지={''.join(sorted(required))})\n{text}")
            break  # 5종 중 하나만 성립 가능

    # 신강신약 점수가 극단(0~20 또는 80~100)일 때만 종격 판단 기준 추가
    if sin_strength_score is not None and (sin_strength_score <= 20 or sin_strength_score >= 80):
        jong = _load_ref("gyeokguk", "종격.md")
        if jong:
            blocks.append(f"### [격국] 종격 판단 기준\n{jong}")

    # 격국 특수원리 9종 — 성립하는 것만
    teukjip = compute_gyeokguk_teukjip(fp, sipseong)
    teukjip_filenames = {
        "부성입묘": "부성입묘.md", "진상관": "진가상관.md", "가상관": "진가상관.md",
        "관살혼잡": "관살혼잡.md", "살인상생": "살인상생.md", "탐재괴인": "탐재괴인.md",
        "군겁쟁재": "군겁쟁재.md", "등라계갑": "등라계갑.md", "순환상생": "순환상생.md",
        "살인상정": "살인상정.md",
    }
    loaded_files = set()
    for name, is_true in teukjip.items():
        if is_true and name in teukjip_filenames:
            fname = teukjip_filenames[name]
            if fname in loaded_files:  # 진상관/가상관처럼 같은 파일을 공유하는 경우 중복 방지
                continue
            text = _load_ref("gyeokguk", "특수원리", fname)
            if text:
                blocks.append(f"### [격국 특수원리] {name}\n{text}")
                loaded_files.add(fname)

    yuk = _load_ref("yukchin", "general.md")
    if yuk:
        blocks.append(f"### [육친] 총론\n{yuk}")

    hwahyeon_file = "여명_화현법.md" if gender_label == "여" else "남명_화현법.md"
    hwahyeon = _load_ref("yukchin", hwahyeon_file)
    if hwahyeon:
        blocks.append(f"### [육친] 화현법 ({gender_label}명 기준)\n{hwahyeon}")

    return "\n\n".join(blocks)


# ── SAZU API ──────────────────────────────────────────────────────────────

def call_sazu(payload: dict) -> dict:
    try:
        resp = requests.post(
            f"{SAZU_BASE_URL}/sazu/calculate",
            json=payload,
            headers={"x-api-key": SAZU_API_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"SAZU API 연결 실패: {e}") from e

    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"SAZU API 응답 오류 (HTTP {resp.status_code})")

    if not body.get("success"):
        err = body.get("error", {})
        msg = err.get("message", "알 수 없는 오류")
        issues = err.get("issues") or []
        if issues:
            msg += " — " + "; ".join(i.get("hint") or i.get("field", "") for i in issues)
        raise RuntimeError(msg)

    return body


# ── 표시용 헬퍼 ───────────────────────────────────────────────────────────

# ── 압축 대시보드(레퍼런스 만세력 앱 스타일) — 원국표 + 대운/세운/월운 가로 스트립 + 신살 배지 ──
ELEMENT_BG = {"목": "#c8e6c9", "화": "#f8bbd0", "토": "#fff59d", "금": "#f5f5f5", "수": "#616161"}
ELEMENT_TEXT_ON_BG = {"수": "#ffffff"}  # 수(水)는 배경이 어두워 흰 글씨, 나머진 기본 검정
HIGHLIGHT_COLOR = "#ff8a3d"


def _char_elem_bg_color(ch: str, is_stem: bool) -> tuple[str, str]:
    elem_hanja = STEM_ELEMENT.get(ch) if is_stem else BRANCH_ELEMENT.get(ch)
    elem = HANJA_ELEMENT_TO_KR.get(elem_hanja)
    return ELEMENT_BG.get(elem, "#eeeeee"), ELEMENT_TEXT_ON_BG.get(elem, "#000000")


SINSAL_HANJA = {
    "역마": "驛馬", "도화": "桃花", "화개": "華蓋", "천을귀인": "天乙",
    "문창귀인": "文昌", "암록": "暗祿", "금여": "金輿", "양인살": "羊刃",
    "괴강살": "魁罡", "백호살": "白虎", "원진살": "怨嗔", "공망": "空亡",
    "귀문관살": "鬼門", "문곡귀인": "文曲", "학당귀인": "學堂", "급각살": "急脚",
    "수옥살": "囚獄", "탕화살": "湯火", "고란살": "孤鸞", "낙정관살": "落井",
    "음양차착살": "差錯",
}


def sinsal_hanja_by_pillar(fp: dict) -> dict:
    """신살을 연지·월지·일지·시지 기둥별로 묶어 한자 태그로 반환.

    반환: {"연지": ["驛馬","空亡"], "월지": [...], "일지": [...], "시지": [...]}
    """
    result = {"연지": [], "월지": [], "일지": [], "시지": []}

    sinsal = compute_sinsal(fp)
    for name in ("역마", "도화", "화개"):
        by_base = sinsal.get(name, {})
        for base in ("연지기준", "일지기준"):
            for pillar_label in by_base.get(base, []):
                if pillar_label in result:
                    result[pillar_label].append(SINSAL_HANJA[name])
    for pillar_label in sinsal.get("천을귀인", []):
        if pillar_label in result:
            result[pillar_label].append(SINSAL_HANJA["천을귀인"])

    ext = compute_sinsal_extended(fp)
    for name in ("문창귀인", "암록", "금여", "양인살", "공망"):
        for item in ext.get(name, []):
            for key in result:
                if item.startswith(key):  # 공망은 "연지(亥)"처럼 지지값이 뒤에 붙어 나옴
                    result[key].append(SINSAL_HANJA[name])
                    break

    zhu_to_ji = {"연주": "연지", "월주": "월지", "일주": "일지", "시주": "시지"}
    for name in ("괴강살", "백호살"):
        for zhu_label in ext.get(name, []):
            ji_label = zhu_to_ji.get(zhu_label)
            if ji_label:
                result[ji_label].append(SINSAL_HANJA[name])

    for item in ext.get("원진살", []):
        prefix = item.split(" ")[0]  # 예: "연지-월지"
        for key in result:
            if key in prefix:
                result[key].append(SINSAL_HANJA["원진살"])

    # 신살 확장 2차 (귀문관살 등 9종)
    ext2 = compute_sinsal_extended2(fp)
    for name in ("문곡귀인", "학당귀인", "급각살", "수옥살", "탕화살"):
        for item in ext2.get(name, []):
            if item in result:
                result[item].append(SINSAL_HANJA[name])
    for item in ext2.get("귀문관살", []):  # "일지-연지" 형태
        for key in result:
            if key in item:
                result[key].append(SINSAL_HANJA["귀문관살"])
    for name in ("고란살", "음양차착살"):  # "일주"/"시주" 형태
        for zhu_label in ext2.get(name, []):
            ji_label = zhu_to_ji.get(zhu_label)
            if ji_label:
                result[ji_label].append(SINSAL_HANJA[name])
    for item in ext2.get("낙정관살", []):  # 이미 "일지"/"시지" 형태
        if item in result:
            result[item].append(SINSAL_HANJA["낙정관살"])

    return result


def render_saju_dashboard_table(fp: dict):
    """시-일-월-연 순서로 십성·간지·신살·지장간·오행분포를 압축 표 하나로 렌더링."""
    order = ["hour", "day", "month", "year"]
    sipseong = compute_sipseong(fp)
    jjg = compute_jijanggan(fp)
    sinsal_by_pillar = sinsal_hanja_by_pillar(fp)
    stem_label = {"hour": "시간", "day": "일간", "month": "월간", "year": "연간"}
    branch_label = {"hour": "시지", "day": "일지", "month": "월지", "year": "연지"}

    def lab(text):
        return f'<td style="text-align:center;font-size:.82rem;padding:4px;color:#555;">{text}</td>'

    def sinsal_cell(tags):
        text = " ".join(tags) if tags else "-"
        return (f'<td style="text-align:center;font-size:.78rem;padding:3px;'
                f'color:#b25a00;font-weight:600;">{text}</td>')

    def big(ch, is_stem):
        bg, color = _char_elem_bg_color(ch, is_stem)
        return (f'<td style="background:{bg};color:{color};text-align:center;'
                f'font-size:1.5rem;font-weight:700;padding:10px 4px;">{ch}</td>')

    top, stems, branches, sinsal_row, bottom, jjgs = [], [], [], [], [], []
    elem_count = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}

    for key in order:
        p = fp.get(key)
        if p is None:
            top.append(lab("-")); stems.append(big("-", True))
            branches.append(big("-", False)); sinsal_row.append(sinsal_cell([]))
            bottom.append(lab("-")); jjgs.append(lab("-"))
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        top.append(lab("일원" if key == "day" else sipseong.get(stem_label[key], "-")))
        stems.append(big(s or "-", True))
        branches.append(big(b or "-", False))
        sinsal_row.append(sinsal_cell(sinsal_by_pillar.get(branch_label[key], [])))
        bottom.append(lab(sipseong.get(branch_label[key], "-")))
        pairs = jjg.get(branch_label[key], [])
        jjgs.append(lab("".join(st for st, _ in pairs)))
        if s and s in STEM_ELEMENT:
            elem_count[STEM_ELEMENT[s]] += 1
        if b and b in BRANCH_ELEMENT:
            elem_count[BRANCH_ELEMENT[b]] += 1

    elem_row = "".join(
        f'<td style="text-align:center;font-size:.82rem;padding:4px;">{ELEMENT_KR_BY_HANJA[e]}({elem_count[e]})</td>'
        for e in ("木", "火", "土", "金", "水")
    )

    st.markdown(
        f"""<table style="width:100%;border-collapse:collapse;">
<tr>{''.join(top)}</tr><tr>{''.join(stems)}</tr><tr>{''.join(branches)}</tr>
<tr>{''.join(sinsal_row)}</tr><tr>{''.join(bottom)}</tr><tr>{''.join(jjgs)}</tr>
</table>
<table style="width:100%;border-collapse:collapse;margin-top:2px;border-top:1px solid #ddd;">
<tr>{elem_row}</tr></table>""",
        unsafe_allow_html=True,
    )


def render_horizontal_strip(rows: list[tuple[str, str, str, str]], current_index: int | None, title: str):
    """rows: [(상단라벨, 천간, 지지, 하단라벨), ...] 왼쪽→오른쪽 표시 순서 그대로."""
    if not rows:
        st.caption(f"{title}: 표시할 데이터 없음")
        return
    top, stems, branches, bottom = [], [], [], []
    for i, (t, s, b, bt) in enumerate(rows):
        hl = f"background:{HIGHLIGHT_COLOR};color:#fff;" if i == current_index else ""
        border = f"border:2px solid {HIGHLIGHT_COLOR};" if i == current_index else ""
        top.append(f'<td style="text-align:center;font-size:.78rem;padding:2px;white-space:nowrap;{hl}">{t}</td>')
        s_bg, s_c = _char_elem_bg_color(s, True) if s in STEM_CHARS else ("#eee", "#000")
        b_bg, b_c = _char_elem_bg_color(b, False) if b in BRANCH_CHARS else ("#eee", "#000")
        stems.append(f'<td style="background:{s_bg};color:{s_c};text-align:center;font-weight:700;padding:6px 3px;{border}">{s}</td>')
        branches.append(f'<td style="background:{b_bg};color:{b_c};text-align:center;font-weight:700;padding:6px 3px;{border}">{b}</td>')
        bottom.append(f'<td style="text-align:center;font-size:.78rem;padding:2px;white-space:nowrap;{hl}">{bt}</td>')
    st.markdown(f"**{title}**")
    st.markdown(
        f"""<div style="overflow-x:auto;"><table style="border-collapse:collapse;min-width:100%;">
<tr>{''.join(top)}</tr><tr>{''.join(stems)}</tr><tr>{''.join(branches)}</tr><tr>{''.join(bottom)}</tr>
</table></div>""",
        unsafe_allow_html=True,
    )


def render_daewoon_strip_from_sazu(dw_module: dict, current_age: int | None):
    """SAZU의 decadeFortune 모듈을 그대로 압축 스트립으로 표시.

    자체 compute_daewoon()은 정확한 양력 시각이 필요한데, SAZU 입력은 음력일 수 있어
    (inp 필드가 음력 그대로일 가능성) 여기서는 SAZU가 이미 음양력 변환까지 마친
    decadeFortune 데이터를 그대로 재사용합니다. compute_daewoon()은 별도로 이미
    회귀 테스트로 검증돼 있으니, 정확한 양력 출생시각이 확보되면 그쪽으로 교체 가능.
    """
    items = dw_module.get("list", [])
    if not items:
        st.caption("대운: 표시할 데이터 없음")
        return
    display = list(reversed(items))  # 레퍼런스처럼 나이 큰 것을 왼쪽에
    current_idx = None
    for i, item in enumerate(display):
        if current_age is not None and item["startAge"] <= current_age < item["startAge"] + 10:
            current_idx = i
            break
    # item["full"]은 SAZU가 한글로 주는 간지 표시문자열(예: "계미") — 색상 조회는 한자
    # 기준(STEM_CHARS/BRANCH_CHARS)이므로 한글→한자로 변환해서 넘긴다.
    rows = [
        (
            str(item["startAge"]),
            STEM_HANGUL_TO_HANJA.get(item["full"][0], item["full"][0]),
            BRANCH_HANGUL_TO_HANJA.get(item["full"][1], item["full"][1]),
            "",
        )
        for item in display
    ]
    render_horizontal_strip(
        rows, current_idx,
        f"대운(大運) — {dw_module.get('direction', '')} · {dw_module.get('startAge', '?')}세부터",
    )


def render_sewoon_strip(birth_year: int, span_before: int = 4, span_after: int = 7):
    this_year = datetime.now().year
    years = list(range(this_year + span_after, this_year - span_before - 1, -1))  # 내림차순
    current_idx = years.index(this_year) if this_year in years else None
    rows = []
    for y in years:
        ganji = compute_year_ganji(y)
        age = y - birth_year + 1  # 세는나이
        rows.append((str(y), ganji[0], ganji[1], f"{age}세"))
    render_horizontal_strip(rows, current_idx, "세운(歲運)")


def render_wolwoon_strip(year: int):
    months = list(range(12, 0, -1))  # 내림차순
    this_month = datetime.now().month if datetime.now().year == year else None
    current_idx = months.index(this_month) if this_month in months else None
    rows = []
    for m in months:
        wo = compute_wolwoon_ganji(datetime(year, m, 15))
        if wo is None:
            continue
        rows.append((f"{m}월", wo[0], wo[1], ""))
    render_horizontal_strip(rows, current_idx, f"월운(月運) — {year}년")


def render_sinsal_badges(fp: dict):
    """있는 신살만 배지로 표시 (없는 항목은 아예 표시 안 함)."""
    sinsal = compute_sinsal(fp)
    ext = compute_sinsal_extended(fp)
    ext2 = compute_sinsal_extended2(fp)
    badges = []
    for name in ("역마", "도화", "화개"):
        by_base = sinsal.get(name, {})
        for base_label in ("연지기준", "일지기준"):
            pillars = by_base.get(base_label, [])
            if pillars:
                badges.append(f"{name}({','.join(pillars)}·{base_label})")
    if sinsal.get("천을귀인"):
        badges.append(f"천을귀인({','.join(sinsal['천을귀인'])})")
    for name in ("문창귀인", "암록", "금여", "양인살", "괴강살", "백호살", "원진살", "공망"):
        items = ext.get(name, [])
        if items:
            badges.append(f"{name}({'; '.join(items)})")
    for name in ("귀문관살", "문곡귀인", "학당귀인", "급각살", "수옥살", "탕화살", "고란살", "낙정관살", "음양차착살"):
        items = ext2.get(name, [])
        if items:
            badges.append(f"{name}({'; '.join(items)})")

    if not badges:
        st.caption("해당하는 신살 없음")
        return
    badge_html = " ".join(
        f'<span style="display:inline-block;background:#fff3e0;border:1px solid #ffb74d;'
        f'border-radius:14px;padding:4px 10px;margin:3px 3px 0 0;font-size:.82rem;">{b}</span>'
        for b in badges
    )
    st.markdown(badge_html, unsafe_allow_html=True)


def render_hyeongchunghae_summary(fp: dict):
    """충·형·파·해만 간단히 요약 (합은 신살 성격이 아니라 제외) — 레퍼런스의 '破(亥寅)' 식 표기."""
    hch = compute_hyeongchunghae(fp)
    tags = []
    for name in ("충", "형", "파", "해"):
        for item in hch.get(name, []):
            tags.append(f"{name}({item})")
    if not tags:
        st.caption("충·형·파·해 없음")
        return
    st.markdown(" · ".join(tags))


# ── 월령(月令) — 지장간 여기/중기/정기 중 "지금 정확히 어느 구간인지" 날짜로 계산 ──
# 월률분야 표준 날짜배분: 생지(寅申巳亥)=7/7/16, 왕지(子卯酉)=10/20(중기없음),
# 오화특례(午)=10/10/10(중기 己 포함 학파), 고지(辰戌丑未)=9/3/18.
# 월지에만 적용되는 개념(연지·일지·시지는 날짜 배분 없이 구성만 있음 — 이미 지장간 함수가 그렇게 처리 중).
BRANCH_CATEGORY = {
    "寅": "생지", "申": "생지", "巳": "생지", "亥": "생지",
    "子": "왕지", "卯": "왕지", "酉": "왕지", "午": "오화특례",
    "辰": "고지", "戌": "고지", "丑": "고지", "未": "고지",
}
DAY_ALLOC = {"생지": [7, 7, 16], "왕지": [10, 20], "오화특례": [10, 10, 10], "고지": [9, 3, 18]}


def compute_wollyeong(month_branch: str, civil_birth_dt, longitude: float = 126.978):
    """월지의 지장간 중 출생일이 실제로 어느 구간(여기/중기/정기)에 해당하는지 계산.

    civil_birth_dt: 시계에 적힌 출생시각 그대로(양력 기준) — 내부에서 자동 보정.
    반환: (천간, 단계, 경과일수, 기준절기명, 기준절기시각) 또는 데이터 부족 시 None.
    """
    if month_branch not in BRANCH_CATEGORY:
        return None
    try:
        birth_dt, _ = correct_birth_datetime(civil_birth_dt, longitude=longitude)
        jeol = _nearest_jeol(birth_dt, "prev")
        if jeol is None:
            return None
        jeol_name, jeol_dt = jeol
        days_elapsed = (birth_dt - jeol_dt).total_seconds() / 86400
        allocs = DAY_ALLOC[BRANCH_CATEGORY[month_branch]]
        stems = JIJANGGAN.get(month_branch)
        if not stems:
            return None
        cum = 0
        for (stem, stage), days in zip(stems, allocs):
            cum += days
            if days_elapsed < cum:
                return stem, stage, round(days_elapsed, 2), jeol_name, jeol_dt
        return stems[-1][0], stems[-1][1], round(days_elapsed, 2), jeol_name, jeol_dt
    except Exception:
        return None


def get_solar_birth_datetime(body: dict):
    """SAZU 입력이 음력이어도 정확한 양력 출생시각을 돌려줌 (월령 등 절기 계산용).

    시간을 모르면 None. 음력이면 sajupy로 변환.
    """
    inp = body["data"]["input"]
    if inp.get("birthHour") is None:
        return None
    y, m, d = inp["birthYear"], inp["birthMonth"], inp["birthDay"]
    if inp.get("isLunar"):
        try:
            from sajupy import lunar_to_solar
            conv = lunar_to_solar(y, m, d, is_leap_month=bool(inp.get("isLeapMonth")))
            y, m, d = conv["solar_year"], conv["solar_month"], conv["solar_day"]
        except Exception:
            return None
    return datetime(y, m, d, inp["birthHour"], inp.get("birthMinute", 0))


@st.dialog("🔮 사주 상세보기")
def show_sinsal_dialog(fp: dict, body: dict | None = None):
    try:
        st.markdown("**사주팔자 원국**")
        render_saju_dashboard_table(fp)

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**12운성**")
            st.text(format_twelve_stages(compute_twelve_stages(fp)))
        with c2:
            st.markdown("**월령(月令)**")
            month_branch = _extract_char((fp.get("month") or {}).get("earthFull"), BRANCH_CHARS)
            civil_dt = get_solar_birth_datetime(body) if body else None
            if month_branch and civil_dt:
                result = compute_wollyeong(month_branch, civil_dt)
                if result:
                    stem, stage, days, jeol_name, jeol_dt = result
                    st.markdown(f"**{stem}**({stage}) — {jeol_name}로부터 {days}일째")
                else:
                    st.caption("계산 불가")
            else:
                st.caption("시간 미상이라 계산 불가")

        st.divider()
        st.markdown("**신살** (있는 것만 표시)")
        render_sinsal_badges(fp)

        st.divider()
        st.markdown("**충·형·파·해**")
        render_hyeongchunghae_summary(fp)

        st.caption("원광만세력·루시아만세력과 대조해보세요.")
    except Exception as e:
        st.exception(e)  # 팝업이 비어 보이는 문제 재발 시 원인을 바로 보여줌


# ── Notion 저장/조회/삭제 — 기존 NOTION_TOKEN·NOTION_PAGE_ID(자동화 작업실) 재사용 ──────
# 데이터베이스가 없으면 그 페이지 아래에 자동으로 생성합니다. 지니님이 노션에서
# 직접 속성을 만들 필요 없음 — 아래 코드가 처음 저장할 때 알아서 만듭니다.
NOTION_DB_TITLE = "사주 고객 DB"
_notion_db_id_cache = None


def _get_notion_client():
    token = _secret("NOTION_TOKEN")
    if not token:
        return None
    try:
        from notion_client import Client
        return Client(auth=token)
    except ImportError:
        return None


def get_or_create_saju_database():
    """자동화 작업실 페이지(NOTION_PAGE_ID) 아래에서 '사주 고객 DB'를 찾고, 없으면 생성.

    기존에 이미 만들어진 데이터베이스라도 '사주팔자'(사람이 읽기 편한 텍스트) 속성이
    없으면 자동으로 추가합니다.
    """
    global _notion_db_id_cache
    if _notion_db_id_cache:
        return _notion_db_id_cache
    client = _get_notion_client()
    page_id = _secret("NOTION_PAGE_ID")
    if client is None or not page_id:
        return None
    try:
        children = client.blocks.children.list(block_id=page_id)
        for block in children.get("results", []):
            if block.get("type") == "child_database":
                title_parts = block.get("child_database", {}).get("title", "")
                if title_parts == NOTION_DB_TITLE:
                    db_id = block["id"]
                    _ensure_saju_property(client, db_id)
                    _notion_db_id_cache = db_id
                    return db_id

        new_db = client.databases.create(
            parent={"type": "page_id", "page_id": page_id},
            title=[{"type": "text", "text": {"content": NOTION_DB_TITLE}}],
            properties={
                "이름": {"title": {}},
                "DB번호": {"rich_text": {}},
                "성별": {"select": {"options": [{"name": "남"}, {"name": "여"}]}},
                "달력": {"select": {"options": [
                    {"name": "양력"}, {"name": "음력"}, {"name": "음력윤달"},
                ]}},
                "생년월일": {"rich_text": {}},
                "생시": {"rich_text": {}},
                "저장일": {"date": {}},
                "사주팔자": {"rich_text": {}},  # 사람이 읽기 편한 표기 (예: 연주:甲辰 월주:乙亥 ...)
                "원국JSON": {"rich_text": {}},  # 재로딩용 압축 데이터 (읽기 불편, 앱 전용)
            },
        )
        _notion_db_id_cache = new_db["id"]
        return new_db["id"]
    except Exception as e:
        st.error(f"Notion 데이터베이스 준비 실패: {e}")
        return None


def _ensure_saju_property(client, db_id: str):
    """기존 데이터베이스에 '사주팔자' 속성이 없으면 추가."""
    try:
        db = client.databases.retrieve(database_id=db_id)
        if "사주팔자" not in db.get("properties", {}):
            client.databases.update(database_id=db_id, properties={"사주팔자": {"rich_text": {}}})
    except Exception:
        pass  # 속성 추가 실패해도 저장 자체는 계속 시도 (구버전 스키마로라도 동작)


def _slim_fp(fp: dict) -> dict:
    """저장/재로딩에 실제로 필요한 간지(천간·지지)만 남겨 압축. SAZU 원본은 부가정보가 많아
    사주에 따라 Notion의 2000자 rich_text 제한에 걸릴 수 있어, 필요한 부분만 추림."""
    slim = {}
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p:
            slim[key] = {"skyFull": p.get("skyFull"), "earthFull": p.get("earthFull")}
    return slim


def _readable_fp(fp: dict) -> str:
    """사람이 읽기 편한 사주팔자 텍스트. 예: 연주:甲辰 월주:乙亥 일주:丙寅 시주:庚寅"""
    labels = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}
    parts = []
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if not p:
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        if s and b:
            parts.append(f"{labels[key]}:{s}{b}")
    return " ".join(parts) if parts else "-"


def save_saju_to_notion(name, db_no, gender_label, calendar_type, birth_str, time_str, fp):
    client = _get_notion_client()
    db_id = get_or_create_saju_database()
    if client is None:
        return None, "NOTION_TOKEN이 설정되지 않았습니다."
    if db_id is None:
        return None, "Notion 데이터베이스를 준비하지 못했습니다."
    slim_json = json.dumps(_slim_fp(fp), ensure_ascii=False)
    if len(slim_json) > 1900:  # 여유를 두고 미리 확인 (한도 2000자)
        return None, f"원국 데이터가 예상보다 커서({len(slim_json)}자) 저장을 건너뜁니다."
    try:
        page = client.pages.create(
            parent={"database_id": db_id},
            properties={
                "이름": {"title": [{"text": {"content": name or "(이름없음)"}}]},
                "DB번호": {"rich_text": [{"text": {"content": db_no or ""}}]},
                "성별": {"select": {"name": gender_label}},
                "달력": {"select": {"name": calendar_type}},
                "생년월일": {"rich_text": [{"text": {"content": birth_str}}]},
                "생시": {"rich_text": [{"text": {"content": time_str}}]},
                "저장일": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
                "사주팔자": {"rich_text": [{"text": {"content": _readable_fp(fp)}}]},
                "원국JSON": {"rich_text": [{"text": {"content": slim_json}}]},
            },
        )
        return page["id"], None
    except Exception as e:
        return None, str(e)


def list_saju_from_notion():
    client = _get_notion_client()
    db_id = get_or_create_saju_database()
    if client is None or db_id is None:
        return []
    try:
        results = client.databases.query(
            database_id=db_id, sorts=[{"property": "저장일", "direction": "descending"}],
        )
        records = []
        for r in results.get("results", []):
            props = r["properties"]

            def _text(prop):
                data = props.get(prop, {})
                arr = data.get("rich_text") or data.get("title") or []
                return arr[0]["plain_text"] if arr else ""

            records.append({
                "id": r["id"],
                "이름": _text("이름"),
                "DB번호": _text("DB번호"),
                "성별": (props.get("성별", {}).get("select") or {}).get("name", ""),
                "달력": (props.get("달력", {}).get("select") or {}).get("name", ""),
                "생년월일": _text("생년월일"),
                "생시": _text("생시"),
                "저장일": (props.get("저장일", {}).get("date") or {}).get("start", ""),
                "사주팔자": _text("사주팔자"),
                "원국JSON": _text("원국JSON"),
            })
        return records
    except Exception as e:
        st.error(f"Notion 조회 실패: {e}")
        return []


def archive_saju_record(page_id):
    client = _get_notion_client()
    if client is None:
        return False
    try:
        client.pages.update(page_id=page_id, archived=True)
        return True
    except Exception as e:
        st.error(f"삭제 실패: {e}")
        return False


@st.dialog("📂 저장된 사주 DB목록")
def show_db_list_dialog():
    try:
        records = list_saju_from_notion()
        if not records:
            st.caption("저장된 기록이 없습니다 (또는 Notion 연결 미설정).")
            return
        table_rows = [
            {"이름": r["이름"], "DB번호": r["DB번호"], "성별": r["성별"], "사주팔자": r["사주팔자"],
             "생년월일": r["생년월일"], "생시": r["생시"], "저장일": r["저장일"]}
            for r in records
        ]
        st.dataframe(pd.DataFrame(table_rows), width='stretch', hide_index=True)

        names = [f"{r['이름']} ({r['생년월일']} {r['생시']})" for r in records]
        picked = st.selectbox("불러오거나 삭제할 기록 선택", range(len(records)), format_func=lambda i: names[i])

        c1, c2 = st.columns(2)
        if c1.button("📖 이 기록 불러오기", width='stretch'):
            try:
                fp = json.loads(records[picked]["원국JSON"])
                st.session_state["loaded_fp_from_notion"] = fp
                st.session_state["loaded_fp_label"] = names[picked]
                st.rerun()
            except Exception as e:
                st.error(f"불러오기 실패: {e}")
        if c2.button("🗑️ 이 기록 삭제", width='stretch'):
            if archive_saju_record(records[picked]["id"]):
                st.success("삭제했습니다.")
                st.rerun()
    except Exception as e:
        st.exception(e)


def pillar_card(label: str, pillar: dict | None):
    if pillar is None:
        st.markdown(f"**{label}**")
        st.info("시간 미상")
        return

    sky_c = ELEMENT_COLOR.get(pillar["skyElement"], "#898781")
    earth_c = ELEMENT_COLOR.get(pillar["earthElement"], "#898781")
    tfi = pillar.get("twelveFortuneInterpretation") or {}

    st.markdown(
        f"""
<div style="border:1px solid rgba(128,128,128,.3); border-radius:12px; padding:16px; text-align:center;">
  <div style="font-size:.8rem; opacity:.65; margin-bottom:8px;">{label}</div>
  <div style="font-size:1.7rem; font-weight:700; line-height:1.35;">
    <span style="color:{sky_c}">{pillar['skyFull']}</span><br/>
    <span style="color:{earth_c}">{pillar['earthFull']}</span>
  </div>
  <div style="font-size:.78rem; margin-top:10px; opacity:.85;">
    십성 {pillar['sippiSeong']}·{pillar['earthSippiSeong']} &nbsp;|&nbsp; 12운성 {pillar['twelveStage']}
  </div>
  <div style="font-size:.72rem; opacity:.6; margin-top:4px;">납음 {pillar['naeeum']}</div>
</div>
""",
        unsafe_allow_html=True,
    )
    if tfi.get("keyword"):
        st.caption(f"💡 {tfi['keyword']} — {tfi.get('energy', '')}")

    jjg = pillar.get("jiJangGan") or {}
    parts = []
    for key, kr in (("residue", "여기"), ("middle", "중기"), ("main", "정기")):
        v = jjg.get(key)
        if v:
            parts.append(f"{kr} {v['stem']}({v['days']}일)")
    if parts:
        with st.expander("지장간"):
            st.write(" · ".join(parts))


def render_elements_chart(elements_module: dict):
    rows = [
        {"오행": ELEMENT_KR[k], "비중": elements_module[k]["total"]["percentage"]}
        for k in ELEMENT_ORDER
    ]
    df = pd.DataFrame(rows)
    domain = [ELEMENT_KR[k] for k in ELEMENT_ORDER]
    color_range = [ELEMENT_COLOR[k] for k in domain]

    base = alt.Chart(df).encode(
        y=alt.Y("오행:N", sort=domain, title=None),
        x=alt.X("비중:Q", title="비중 (%)", scale=alt.Scale(domain=[0, 100])),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=22).encode(
        color=alt.Color("오행:N", scale=alt.Scale(domain=domain, range=color_range), legend=None),
        tooltip=["오행", "비중"],
    )
    labels = base.mark_text(align="left", dx=4).encode(text=alt.Text("비중:Q", format=".1f"))
    st.altair_chart(bars + labels, width='stretch')


def daewoon_dataframe(dw: dict, current_age: int | None) -> pd.DataFrame:
    rows = []
    for item in dw["list"]:
        current = current_age is not None and item["startAge"] <= current_age < item["startAge"] + 10
        rows.append({
            "현재": "▶" if current else "",
            "나이": f"{item['startAge']}세~",
            "대운": item["full"],
            "십성": item["sipseong"]["gan"],
            "12운성": item["twelveFortune"]["name"],
            "포인트": item["twelveFortune"]["keyword"],
        })
    return pd.DataFrame(rows)


# ── Gemini 프롬프트 ──────────────────────────────────────────────────────

def format_sazu_context(body: dict) -> str:
    modules = body["data"]["modules"]
    meta = body["meta"]
    inp = body["data"]["input"]
    fp = modules["fourPillars"]

    calendar_note = ("음력" + (" 윤달" if inp.get("isLeapMonth") else "")) if inp["isLunar"] else "양력"
    if inp["birthHour"] is None:
        time_note = "시간미상"
    else:
        time_note = f"{inp['birthHour']:02d}:{inp.get('birthMinute', 0):02d}"

    lines = []
    lines.append(f"[입력] {inp['birthYear']}년 {inp['birthMonth']}월 {inp['birthDay']}일"
                  f" ({calendar_note}) {time_note} / {'여성' if inp['isFemale'] else '남성'}")
    if inp.get("correctedHour") is not None:
        lines.append(f"[경도·균시차 보정 완료] 보정 시각 {inp['correctedHour']:02d}:{inp['correctedMinute']:02d}"
                      f" — 이미 정밀 계산된 값이므로 시주 재계산 불필요")

    # 아래 십성·12운성·납음·지장간은 SAZU 원본이 아니라 지니님의 자체 엔진 결과입니다.
    # 실제 만세력 앱 스크린샷과 대조해 정확도를 검증한 값이라 이쪽을 신뢰합니다.
    sipseong = compute_sipseong(fp)
    stages = compute_twelve_stages(fp)
    nayin = compute_nayin(fp)
    jjg = compute_jijanggan(fp)
    stem_label = {"year": "연간", "month": "월간", "day": "일간", "hour": "시간"}
    branch_label = {"year": "연지", "month": "월지", "day": "일지", "hour": "시지"}
    pillar_label = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}

    lines.append("\n[사주팔자 원국 — 자체 계산, 검증됨]")
    for key in ("year", "month", "day", "hour"):
        p = fp.get(key)
        if p is None:
            lines.append(f"  {pillar_label[key]}: 시간 미상")
            continue
        s = _extract_char(p.get("skyFull"), STEM_CHARS)
        b = _extract_char(p.get("earthFull"), BRANCH_CHARS)
        jjg_str = "".join(st for st, _ in jjg.get(branch_label[key], []))
        lines.append(
            f"  {pillar_label[key]}: {s}{b} "
            f"천간십성={sipseong.get(stem_label[key], '-')} 지지십성={sipseong.get(branch_label[key], '-')} "
            f"12운성={stages.get(branch_label[key], '-')} 지장간={jjg_str} 납음={nayin.get(pillar_label[key], '-')}"
        )

    el = modules["elements"]
    lines.append("\n[오행 분포] " + " ".join(f"{ELEMENT_KR[k]}{el[k]['total']['percentage']}%" for k in ELEMENT_ORDER))

    summ = modules.get("summary", {})
    ss = modules.get("sinStrength", {})
    if summ:
        dm = summ.get("dayMaster", {})
        eb = summ.get("elementBalance", {})
        fpz = summ.get("fortunePhase", {})
        lines.append(f"\n[일간] {dm.get('char')}({dm.get('element')})")
        lines.append(f"[오행균형] 우세={eb.get('dominant')} 부족={eb.get('lacking')} 점수={eb.get('score')} 등급={eb.get('grade')}")
        lines.append(f"[현재 대운] {fpz.get('current', {}).get('age')}세 {fpz.get('current', {}).get('pillar')}"
                      f" → 다음 {fpz.get('next', {}).get('age')}세 {fpz.get('next', {}).get('pillar')}")
    if ss:
        lines.append(f"[신강신약(API 판정)] {ss.get('strength')} (점수 {ss.get('score')}) — {ss.get('analysis')}")
        lines.append(f"  득령={ss.get('deukryeong')} 득지={ss.get('deukji')} 득세={ss.get('deukse')}")

    lines.append("\n[대운표]")
    for d in modules["decadeFortune"]["list"]:
        lines.append(f"  {d['startAge']}세~ {d['full']} 십성={d['sipseong']['gan']}({d['sipseong']['ganCategory']}) "
                     f"12운성={d['twelveFortune']['name']}")
    lines.append(f"  방향: {modules['decadeFortune']['direction']} / 시작연령 {modules['decadeFortune']['startAge']}세")

    lines.append("\n[신살 — 결정적 계산 결과 (기본 4종: 역마·도화·화개는 연지+일지 기준 / 천을귀인은 일간 기준)]")
    sinsal_result = compute_sinsal(fp)
    lines.append(format_sinsal(sinsal_result))

    lines.append("\n[신살 확장 8종 — 결정적 계산 결과]")
    ext_result = compute_sinsal_extended(fp)
    lines.append(format_sinsal_extended(ext_result))

    lines.append("\n[신살 확장 2차 9종 — 결정적 계산 결과 (귀문관살·문곡귀인·학당귀인·급각살·수옥살·탕화살·고란살·낙정관살·음양차착살)]")
    ext2_result = compute_sinsal_extended2(fp)
    lines.append(format_sinsal_extended2(ext2_result))

    lines.append("\n[형충파해 — 결정적 계산 결과 (천간합·육합·삼합·반합·방합·충·형·파·해)]")
    hch_result = compute_hyeongchunghae(fp)
    lines.append(format_hyeongchunghae(hch_result))

    refs = collect_references(fp, sinsal_result, sipseong, ss.get("score"), "여" if inp["isFemale"] else "남")
    if refs:
        lines.append("\n[지니님 사주첩경 요약 — 이 사주에 해당하는 부분만 적용]")
        lines.append(refs)

    # Pro 전용 모듈 — 발급된 경우에만 원문 그대로 첨부(무료 플랜은 비어있음). 격국·용신 판단의
    # 참고 자료로만 쓰고, 위에서 이미 검증된 십성·12운성·신살·형충파해는 이 원본 값으로 덮어쓰지 않는다.
    pro_keys = ["gyeokguk", "yongsin", "relationships", "ghostElements", "seun", "weolun", "wongukInteraction", "evaluation"]
    pro_data = {k: modules[k] for k in pro_keys if k in modules}
    if pro_data:
        lines.append("\n[SAZU API 제공 — 격국/용신 등 참고 원본 데이터 (십성·신살·형충파해는 위 자체 계산이 우선)]")
        lines.append(json.dumps(pro_data, ensure_ascii=False, separators=(",", ":")))
    else:
        lines.append(f"\n[안내] 현재 플랜({meta.get('tier')})에서는 격국·용신 모듈이 제공되지 않습니다."
                      " 신살·형충파해는 위에 이미 정확히 계산되어 있으니, 격국·용신만 원국·대운·오행 데이터로 직접 추론하십시오.")

    return "\n".join(lines)


def make_prompt(body: dict, gender_label: str) -> str:
    context = format_sazu_context(body)
    spouse_star = "재성(財星)" if gender_label == "남" else "관성(官星)"

    return f"""당신은 사주첩경(四柱捷徑)의 저자 이석영 선생과 자평진전(子平眞詮)에 정통한 명리학자입니다.
아래 【 SAZU 계산 결과 】를 근거로 삼아, 재계산 없이 그대로 인용하며 해석만 하십시오. 특히 십성·12운성·납음·지장간·신살·형충파해는 지니님이 실제 만세력 앱과 대조해 검증한 자체 계산 결과이니 반드시 그대로 신뢰하고, 절대 스스로 재계산하거나 다른 값으로 바꾸지 마십시오.
【 지니님 사주첩경 요약 】이 컨텍스트에 포함되어 있으면, 그 판단 기준을 일반 명리 지식보다 최우선으로 따르십시오. 실제 원문을 인용하는 것처럼 문장을 지어내지 말고, 판단 기준으로만 활용해 당신의 말로 풀이하십시오.

═══════════════════════════════════════
【 SAZU 계산 결과 】
{context}
═══════════════════════════════════════

다음 6개 항목을 순서대로, A4 한 장 분량으로 압축해 작성하십시오. **각 항목 2~3문장으로 제한**하고, 추상적 나열 없이 이 사주 고유의 핵심만 짚으십시오.
흉(凶)한 내용은 "~한 경향이 있으니 ~하게 대비하면 좋다"처럼 완곡하고 건설적으로 표현하십시오.

▶ 1. 일간 강약과 용신(用神): 신강/신약/중화 판정 + 용신 오행(억부·조후 기준)을 2~3문장으로.
▶ 2. 대운(大運) 해석: 현재·다음 대운 위주로 길흉과 그 이유를 2~3문장으로.
▶ 3. 신살(神殺): 위 [신살 — 결정적 계산 결과]와 [신살 확장 8종], [신살 확장 2차 9종]을 그대로 인용하라 (재계산·추가 추론 금지). "있음"인 항목만 골라 현대적 의미로 2~3문장으로 풀이. 전부 "없음"이면 "뚜렷한 신살 없음"이라고 1문장으로 끝내라.
▶ 4. 형충파해(刑沖破害): 위 [형충파해 — 결정적 계산 결과]를 그대로 인용하라 (재계산 금지). 충·형·파·해 중 "없음"이 아닌 것만 골라 이 사주에 미치는 실질적 영향을 2~3문장으로. 전부 없으면 "형충파해 없이 원국이 안정적"이라고 1문장으로 끝내라.
▶ 5. 격국(格局): 기본은 정격(正格)이다. 신강신약이 극단적이고 오행이 한쪽으로 쏠렸을 때만 종격(從格)을 검토하고, 그 외에는 "정격, 1번의 용신을 따름"이라고 1문장으로 끝내라.
▶ 종합 총평: 핵심 테마와 조언을 2~3문장으로. {gender_label}성 배우자성({spouse_star}) 관련 한 줄 포함.

※ 한국어, 전문 용어는 한자 병기. 항목 제목 외의 수식어·서론은 생략하고 바로 본문으로 들어가십시오.
※ 사주첩경·자평진전 등 원문에 실제로 없는 문장을 지어내 따옴표로 인용하지 마십시오. 원리를 설명할 때는 "~라는 원칙에 따라"처럼 서술하고, 직접 인용 형식은 쓰지 마십시오."""


def call_gemini_stream(prompt: str):
    client = genai.Client(api_key=GEMINI_API_KEY)
    for chunk in client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.8,
            max_output_tokens=2048,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        ),
    ):
        if chunk.text:
            yield chunk.text


# ── UI ────────────────────────────────────────────────────────────────────

st.title("🔮 만세력")
st.caption("SAZU 만세력 API로 사주팔자·대운을 정밀 계산하고, Gemini가 사주첩경·자평진전 기반으로 해석합니다.")

# ── 시(時) 선택 옵션 — 조자시/야자시/자시(통합) 구분 (원광만세력 등 참고) ──────────
# 조자시: 00:30~01:30, 당일 그대로 (day_offset=0)
# 야자시: 23:30~24:00, 당일 저녁 그대로 (day_offset=0)
# 자시(통합): 23:30~01:30 전체를 "출생 다음날 아침 자시"로 일괄 처리 (day_offset=+1)
#   — 지니님이 결정하신 "조/야자시 구분 없이 통합"이 이 옵션과 대응합니다. 기본 추천값.
TIME_OPTIONS = [
    ("조자시 00:30~01:30", 1, 0, 0),
    ("축시 01:30~03:30", 2, 30, 0),
    ("인시 03:30~05:30", 4, 30, 0),
    ("묘시 05:30~07:30", 6, 30, 0),
    ("진시 07:30~09:30", 8, 30, 0),
    ("사시 09:30~11:30", 10, 30, 0),
    ("오시 11:30~13:30", 12, 30, 0),
    ("미시 13:30~15:30", 14, 30, 0),
    ("신시 15:30~17:30", 16, 30, 0),
    ("유시 17:30~19:30", 18, 30, 0),
    ("술시 19:30~21:30", 20, 30, 0),
    ("해시 21:30~23:30", 22, 30, 0),
    ("야자시 23:30~24:00 (당일 그대로)", 23, 45, 0),
    ("자시(통합, 추천) 23:30~01:30 — 다음날 아침 자시로 계산", 0, 30, 1),
]
TIME_OPTION_LABELS = [t[0] for t in TIME_OPTIONS]
_DEFAULT_TIME_IDX = TIME_OPTION_LABELS.index("자시(통합, 추천) 23:30~01:30 — 다음날 아침 자시로 계산")

# ── 다중 인물 관리 (세션 상태에 사람별 폼 값 보관, 이름으로 전환) ──────────────────
if "people" not in st.session_state:
    st.session_state["people"] = {"사람1": {}}
if "active_person" not in st.session_state:
    st.session_state["active_person"] = "사람1"

pcol1, pcol2, pcol3 = st.columns([3, 1, 1])
with pcol1:
    active = st.selectbox("인물 선택", list(st.session_state["people"].keys()),
                           index=list(st.session_state["people"].keys()).index(st.session_state["active_person"]))
    st.session_state["active_person"] = active
with pcol2:
    if st.button("➕ 인원 추가", width='stretch'):
        n = len(st.session_state["people"]) + 1
        st.session_state["people"][f"사람{n}"] = {}
        st.session_state["active_person"] = f"사람{n}"
        st.rerun()
with pcol3:
    if len(st.session_state["people"]) > 1 and st.button("🗑️ 이 인물 삭제", width='stretch'):
        del st.session_state["people"][st.session_state["active_person"]]
        st.session_state["active_person"] = list(st.session_state["people"].keys())[0]
        st.rerun()

saved = st.session_state["people"].get(st.session_state["active_person"], {})

with st.form("saju_form"):
    ncol1, ncol2 = st.columns([3, 1])
    name = ncol1.text_input("이름", value=saved.get("name", ""))
    db_no = ncol2.text_input("DB번호", value=saved.get("db_no", ""), help="Notion 연동 시 저장 식별자로 사용 예정")

    c1, c2, c3 = st.columns([1.3, 2, 1])
    with c1:
        calendar_type = st.radio("달력", ["양력", "음력", "음력윤달"], horizontal=False,
                                  index=["양력", "음력", "음력윤달"].index(saved.get("calendar_type", "양력")))
    with c2:
        cc1, cc2, cc3 = st.columns(3)
        year = cc1.number_input("연도", min_value=1900, max_value=2035, value=saved.get("year", 1995), step=1)
        month = cc2.selectbox("월", list(range(1, 13)), index=(saved.get("month", 1) - 1))
        day = cc3.selectbox("일", list(range(1, 32)), index=(saved.get("day", 1) - 1))
    with c3:
        gender_label = st.radio("성별", ["남", "여"], horizontal=True,
                                 index=["남", "여"].index(saved.get("gender_label", "남")))

    time_known = st.checkbox("태어난 시간을 압니다", value=saved.get("time_known", True))
    time_idx = day_offset = None
    if time_known:
        time_idx = st.selectbox("시(時) 입력", range(len(TIME_OPTIONS)),
                                 format_func=lambda i: TIME_OPTIONS[i][0],
                                 index=saved.get("time_idx", _DEFAULT_TIME_IDX))
        st.caption("조자시: 출생 당일 아침 자시 / 야자시: 출생 당일 저녁 자시 / "
                   "자시(통합): 출생 다음날 아침 자시로 계산됩니다.")
    else:
        st.caption("시간을 모르면 시주(時柱)는 제외하고 계산합니다.")

    birthplace = st.selectbox("출생지 선택", ["대한민국(-30분)"], index=0,
                               help="현재는 대한민국 고정(경도 보정 -30분 근사). 해외 출생지는 추후 지원 예정.")

    submitted = st.form_submit_button("사주 계산하기", type="primary", width='stretch')

if submitted:
    # 이 인물의 입력값을 세션에 저장해 다음에 이 인물로 돌아와도 유지되게 함
    st.session_state["people"][st.session_state["active_person"]] = {
        "name": name, "db_no": db_no, "calendar_type": calendar_type,
        "year": int(year), "month": int(month), "day": int(day),
        "gender_label": gender_label, "time_known": time_known, "time_idx": time_idx,
    }

    birth_year, birth_month, birth_day = int(year), int(month), int(day)
    hour = minute = None
    is_lunar_input = calendar_type in ("음력", "음력윤달")
    if time_known:
        _, hour, minute, day_offset = TIME_OPTIONS[time_idx]
        if day_offset:
            if is_lunar_input:
                # 음력 날짜에 그냥 +1일 하면 안 됨(음력 3월30일+1일이 양력 3월31일이 되는 식의
                # 잘못된 계산이 나옴) — sajupy로 먼저 정확히 양력 변환한 뒤 그 양력 날짜를 밀고,
                # 최종적으로는 양력으로 SAZU에 전달한다.
                try:
                    from sajupy import lunar_to_solar
                    conv = lunar_to_solar(birth_year, birth_month, birth_day,
                                           is_leap_month=(calendar_type == "음력윤달"))
                    solar_dt = datetime(conv["solar_year"], conv["solar_month"], conv["solar_day"])
                    shifted = solar_dt + timedelta(days=day_offset)
                    birth_year, birth_month, birth_day = shifted.year, shifted.month, shifted.day
                    is_lunar_input = False  # 변환 완료 — 이후 SAZU에는 양력으로 전달
                except Exception as e:
                    st.error(f"음력→양력 변환 실패로 자시(통합) 날짜 이동을 적용하지 못했습니다: {e}"
                              " 조자시/야자시를 직접 선택해주세요.")
                    st.stop()
            else:
                shifted = datetime(birth_year, birth_month, birth_day) + timedelta(days=day_offset)
                birth_year, birth_month, birth_day = shifted.year, shifted.month, shifted.day

    payload = {
        "birthYear": birth_year,
        "birthMonth": birth_month,
        "birthDay": birth_day,
        "isLunar": is_lunar_input,
        "isFemale": gender_label == "여",
        "modules": SAZU_MODULES,
    }
    if calendar_type == "음력윤달" and is_lunar_input:
        payload["isLeapMonth"] = True
    elif calendar_type == "음력" and is_lunar_input:
        payload["isLeapMonth"] = False
    if time_known:
        payload["birthHour"] = int(hour)
        payload["birthMinute"] = int(minute)

    with st.spinner("SAZU 만세력 계산 중..."):
        try:
            body = call_sazu(payload)
        except RuntimeError as e:
            st.error(str(e))
            st.stop()

    st.session_state["sazu_body"] = body
    st.session_state["gender_label"] = gender_label
    st.session_state.pop("interpretation", None)

if st.session_state.get("loaded_fp_from_notion"):
    st.divider()
    lcol1, lcol2 = st.columns([4, 1])
    with lcol1:
        st.subheader(f"📂 불러온 기록 — {st.session_state.get('loaded_fp_label', '')}")
        st.caption("Notion에 저장된 원국만 표시합니다(SAZU 재계산 없음). 대운·세운·월운은 새로 사주를 계산해야 확인 가능합니다.")
    with lcol2:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("✕ 닫기", width='stretch'):
            del st.session_state["loaded_fp_from_notion"]
            st.session_state.pop("loaded_fp_label", None)
            st.rerun()
    _loaded_fp = st.session_state["loaded_fp_from_notion"]
    render_saju_dashboard_table(_loaded_fp)
    if st.button("🔮 이 기록 상세보기"):
        st.session_state["_open_loaded_detail_dialog"] = True
    if st.session_state.get("_open_loaded_detail_dialog"):
        show_sinsal_dialog(_loaded_fp, None)
        st.session_state["_open_loaded_detail_dialog"] = False

body = st.session_state.get("sazu_body")
if body:
    modules = body["data"]["modules"]
    gender_label = st.session_state["gender_label"]

    inp = body["data"]["input"]
    _cal = "음력" if inp.get("isLunar") else "양력"
    _leap_note = " 🔸윤달" if inp.get("isLunar") and inp.get("isLeapMonth") else ""
    _time_note = f"{inp['birthHour']:02d}:{inp.get('birthMinute', 0):02d}" if inp.get("birthHour") is not None else "시간미상"
    st.info(
        f"**입력 확인**: {inp['birthYear']}년 {inp['birthMonth']}월 {inp['birthDay']}일"
        f" ({_cal}{_leap_note}) {_time_note} · {'여성' if inp['isFemale'] else '남성'}"
        + ("  \n⚠️ 윤달로 입력하셨습니다 — 평달이 맞는지 다시 한 번 확인해주세요." if _leap_note else "")
    )

    st.divider()
    hcol1, hcol2, hcol3, hcol4 = st.columns([3, 1, 1, 1])
    with hcol1:
        st.subheader("📋 압축 대시보드 — 자체 계산 (검증용)")
        st.caption("한눈에 보는 원국·대운·세운·월운. SAZU와 별개로 지니님 코드가 직접 계산한 결과입니다.")
    fp = modules["fourPillars"]
    with hcol2:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🔮 상세보기", width='stretch'):
            st.session_state["_open_sinsal_dialog"] = True
    with hcol3:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("💾 저장", width='stretch'):
            _cal_type = "음력윤달" if (inp.get("isLunar") and inp.get("isLeapMonth")) else ("음력" if inp.get("isLunar") else "양력")
            _birth_str = f"{inp['birthYear']}-{inp['birthMonth']:02d}-{inp['birthDay']:02d}"
            _time_str = _time_note
            _person = st.session_state["people"].get(st.session_state["active_person"], {})
            _pid, _err = save_saju_to_notion(
                _person.get("name", ""), _person.get("db_no", ""),
                "여" if inp["isFemale"] else "남", _cal_type, _birth_str, _time_str, fp,
            )
            if _err:
                st.error(f"저장 실패: {_err}")
            else:
                st.success("Notion에 저장했습니다.")
    with hcol4:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("📂 DB목록", width='stretch'):
            st.session_state["_open_db_list_dialog"] = True

    if st.session_state.get("_open_sinsal_dialog"):
        show_sinsal_dialog(fp, body)
        st.session_state["_open_sinsal_dialog"] = False
    if st.session_state.get("_open_db_list_dialog"):
        show_db_list_dialog()
        st.session_state["_open_db_list_dialog"] = False

    render_saju_dashboard_table(fp)

    st.markdown("&nbsp;", unsafe_allow_html=True)
    cur_age_sazu = modules.get("summary", {}).get("fortunePhase", {}).get("current", {}).get("age")
    render_daewoon_strip_from_sazu(modules["decadeFortune"], cur_age_sazu)

    st.markdown("&nbsp;", unsafe_allow_html=True)
    render_sewoon_strip(inp["birthYear"])  # 참고: inp가 음력이면 나이 라벨이 드물게 ±1 될 수 있음(원국 정확도엔 무관)

    st.markdown("&nbsp;", unsafe_allow_html=True)
    render_wolwoon_strip(datetime.now().year)

    st.divider()
    st.subheader("사주팔자 원국")
    cols = st.columns(4)
    for col, (key, label) in zip(cols, PILLAR_LABELS):
        with col:
            pillar_card(label, fp.get(key))

    st.divider()
    lcol, rcol = st.columns([3, 2])
    with lcol:
        st.subheader("오행 분포")
        render_elements_chart(modules["elements"])
    with rcol:
        summ = modules.get("summary", {})
        ss = modules.get("sinStrength", {})
        st.subheader("신강신약 · 총평")
        dm = summ.get("dayMaster", {})
        eb = summ.get("elementBalance", {})
        m1, m2 = st.columns(2)
        m1.metric("일간", f"{dm.get('char', '-')}({dm.get('element', '-')})")
        m2.metric("신강/신약", ss.get("strength", "-"), f"{ss.get('score', '-')}점")
        m3, m4 = st.columns(2)
        m3.metric("오행균형", eb.get("grade", "-"), f"{eb.get('score', '-')}점")
        fpz = summ.get("fortunePhase", {})
        m4.metric("현재 대운", f"{fpz.get('current', {}).get('age', '-')}세", fpz.get("current", {}).get("pillar", "-"))
        if ss.get("analysis"):
            st.caption(ss["analysis"])

    st.divider()
    st.subheader("대운(大運)")
    dw = modules["decadeFortune"]
    st.caption(f"{dw['direction']} · {dw['startAge']}세부터 시작 · 기준 절기: {dw.get('basisTermsName', '-')}")
    cur_age = modules.get("summary", {}).get("fortunePhase", {}).get("current", {}).get("age")
    st.dataframe(daewoon_dataframe(dw, cur_age), width='stretch', hide_index=True)

    st.divider()
    st.subheader("🔧 확장 분석 — 자체 계산 (검증용)")
    st.caption(
        "SAZU와 무관하게 지니님 코드로 직접 계산한 결과입니다. "
        "원광만세력·루시아만세력과 대조해서 정확도를 확인해주세요. "
        "AI 해석 프롬프트에는 아직 반영되지 않았습니다(신살 4종만 예외)."
    )
    tab1, tab2, tab3, tab4 = st.tabs(["신살", "형충파해", "십성·12운성", "지장간·납음오행"])

    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**역마·도화·화개·천을귀인**")
            st.text(format_sinsal(compute_sinsal(fp)))
        with c2:
            st.markdown("**문창귀인·암록·금여·양인·괴강·백호·원진·공망**")
            st.text(format_sinsal_extended(compute_sinsal_extended(fp)))

    with tab2:
        st.markdown("**천간합·육합·삼합·반합·방합·충·형·파·해**")
        st.text(format_hyeongchunghae(compute_hyeongchunghae(fp)))

    with tab3:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**십성**")
            st.text(format_sipseong(compute_sipseong(fp)))
        with c2:
            st.markdown("**12운성**")
            st.text(format_twelve_stages(compute_twelve_stages(fp)))

    with tab4:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**지장간(여기·중기·정기)**")
            st.text(format_jijanggan(compute_jijanggan(fp)))
        with c2:
            st.markdown("**납음오행**")
            st.text(format_nayin(compute_nayin(fp)))

    tier = body["meta"].get("tier")
    if tier == "free":
        st.caption("무료 플랜: 격국·용신·신살 상세 모듈은 잠겨 있어, 아래 AI 해석이 원국 데이터를 근거로 직접 추론합니다.")

    st.divider()
    if st.button("🧙 만당 스타일 해석 보기", type="primary", width='stretch'):
        prompt = make_prompt(body, gender_label)
        st.subheader("해석")
        try:
            full_text = st.write_stream(call_gemini_stream(prompt))
        except Exception as e:
            st.error(f"Gemini 해석 실패: {e}")
        else:
            st.session_state["interpretation"] = full_text
    elif st.session_state.get("interpretation"):
        st.subheader("해석")
        st.markdown(st.session_state["interpretation"])

    st.divider()
    st.caption("※ 본 해석은 사주첩경·자평진전 이론을 기반으로 AI가 생성했으며, 참고용입니다.")
