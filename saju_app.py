#!/usr/bin/env python3
"""사주 분석 대시보드 — SAZU 만세력 API(사주팔자·대운) + Gemini(만당 스타일 해석)

참고: 사주첩경(이석영) · 자평진전
"""

import os

import altair as alt
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="사주 분석", page_icon="🔮", layout="wide")

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
ELEMENT_COLOR = {"목": "#008300", "화": "#e34948", "토": "#eda100", "금": "#2a78d6", "수": "#4a3aa7"}
PILLAR_LABELS = [("hour", "시주(時)"), ("day", "일주(日)"), ("month", "월주(月)"), ("year", "연주(年)")]


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

    lines.append("\n[사주팔자 원국]")
    for key, label in (("year", "연주"), ("month", "월주"), ("day", "일주"), ("hour", "시주")):
        p = fp.get(key)
        if p is None:
            lines.append(f"  {label}: 시간 미상")
            continue
        lines.append(
            f"  {label}: {p['full']}({p['skyFull']}/{p['earthFull']}) "
            f"천간십성={p['sippiSeong']} 지지십성={p['earthSippiSeong']} 12운성={p['twelveStage']} 납음={p['naeeum']}"
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

    # Pro 전용 모듈 — 발급된 경우에만 원문 그대로 첨부(무료 플랜은 비어있음)
    pro_keys = ["gyeokguk", "yongsin", "sinsal", "relationships", "ghostElements", "seun", "weolun", "wongukInteraction", "evaluation"]
    pro_data = {k: modules[k] for k in pro_keys if k in modules}
    if pro_data:
        import json
        lines.append("\n[SAZU API 제공 — 격국/용신/신살 등 상세 원본 데이터]")
        lines.append(json.dumps(pro_data, ensure_ascii=False, indent=1))
    else:
        lines.append(f"\n[안내] 현재 플랜({meta.get('tier')})에서는 격국·용신·신살 모듈이 제공되지 않습니다."
                      " 위 원국·대운·오행 데이터만으로 아래 항목을 직접 추론해 분석하십시오.")

    return "\n".join(lines)


def make_prompt(body: dict, gender_label: str) -> str:
    context = format_sazu_context(body)
    spouse_star = "재성(財星)" if gender_label == "남" else "관성(官星)"

    return f"""당신은 사주첩경(四柱捷徑)의 저자 이석영 선생과 자평진전(子平眞詮)에 정통한 명리학자입니다.
단정적이고 임팩트 있는 화법으로, 아래 SAZU 만세력 API가 정밀 계산한 사주 데이터를 근거로 삼아 분석하십시오.
사주팔자·대운 수치는 이미 정확히 계산되어 있으니 재계산하지 말고 그대로 인용하며 해석에만 집중하십시오.

═══════════════════════════════════════
【 SAZU 계산 결과 】
{context}
═══════════════════════════════════════

다음 5개 항목을 순서대로, 확신에 찬 어조로 분석하십시오. 이 사주 고유의 특징을 구체적으로 짚고, 추상적 나열은 금지합니다.
흉(凶)한 내용을 전달할 때는 직설적 단정을 피하고 "~한 경향이 있으니 ~하게 대비하면 좋다"처럼 완곡하고 건설적으로 우회해서 전달하십시오.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 1. 일간 강약(身强身弱) 판단과 용신(用神) 결정
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 위에 제공된 득령/득지/득세와 신강신약 점수를 근거로 최종 판정을 재확인하고 그 의미를 풀이
• 억부용신(신강→식상/재/관, 신약→비겁/인성)과 조후용신(월지 계절의 한난조습) 두 기준으로 용신을 도출
• 용신의 오행과 사주 내 통근 역량 평가, 희신·기신 구분
• 자평진전/사주첩경 원문을 한 구절 인용해 용신 역량을 평가

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 2. 대운(大運) 흐름과 각 대운 해석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 제공된 대운표 전체를 훑되, 현재 대운과 다음 대운은 특히 깊이 있게 — 용신·기신과의 관계로 길흉 판단
• 대운 중 가장 도약하는 시기와 가장 신중해야 할 시기를 비교
• 흉한 대운은 구체적 이유를 밝히되 완곡하게, 대비책과 함께 제시

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 3. 신살(神殺) 분석 — 현대적 재해석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 원국(연주·일주 지지 기준)에서 역마살, 도화살, 천을귀인, 화개살 등 주요 신살의 유무를 판단
  (SAZU 데이터에 신살 원본이 포함되어 있으면 그것을 근거로, 없으면 사주첩경 신살 조견표 원칙으로 직접 추론)
• 각 신살을 미신적으로 설명하지 말고 현대적 삶(직업 이동성, 대인관계, 예술적 감각, 종교/영성 등)에 대응시켜 재해석
• 흉살은 리스크가 아니라 "주의해서 다루면 강점이 되는 기질"로 프레이밍

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 4. 격국(格局) — 기본은 정격, 종격은 예외적으로만
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 대부분의 사주는 정격(正格)이다. 종격(從格)은 일간이 극도로 무력하고(득령·득지·득세가 전부 실함에 가까움) 동시에 오행 분포가 특정 오행 한두 개로 압도적으로 쏠려 있어, 억부용신으로는 도저히 감당이 안 될 때만 검토하는 예외적 케이스다.
• 위에 제공된 신강신약 점수와 오행 분포를 보고 이 조건에 뚜렷이 해당하는지 먼저 판단하라. 애매하거나 중화·중강·중약 수준이면 종격을 검토할 필요 없이 곧바로 "정격이며 1번 항목의 억부·조후 용신을 그대로 따른다"는 결론을 한두 문장으로 제시하고 다음 항목으로 넘어가라.
• 조건에 뚜렷이 해당할 때만 종왕/종강/종아/종재/종살격 중 어디에 해당하는지, 그 경우 용신이 어떻게 바뀌는지 자평진전 논리로 설명하라.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 종합 총평
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
이 사주의 핵심 테마, 강점, 유의할 점, 삶의 방향을 3~5문장으로 마무리하고,
사주첩경 또는 자평진전의 한 구절을 인용해 이 명(命)을 관통하는 키워드로 맺으십시오.
{gender_label}성 기준 배우자성은 {spouse_star}이니, 총평에 짧게라도 이성운/배우자운 한 줄을 곁들이십시오.

※ 한국어로 작성하되 전문 용어는 한자를 병기하십시오. 나쁜 내용일수록 더 신중하고 배려 있게 표현하십시오."""


def call_gemini_stream(prompt: str):
    client = genai.Client(api_key=GEMINI_API_KEY)
    for chunk in client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(temperature=0.8, max_output_tokens=32768),
    ):
        if chunk.text:
            yield chunk.text


# ── UI ────────────────────────────────────────────────────────────────────

st.title("🔮 사주 분석")
st.caption("SAZU 만세력 API로 사주팔자·대운을 정밀 계산하고, Gemini가 사주첩경·자평진전 기반으로 해석합니다.")

with st.form("saju_form"):
    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        calendar_type = st.radio("달력", ["양력", "음력"], horizontal=True)
        is_leap = st.checkbox("윤달", disabled=(calendar_type == "양력"))
    with c2:
        cc1, cc2, cc3 = st.columns(3)
        year = cc1.number_input("연도", min_value=1900, max_value=2035, value=1995, step=1)
        month = cc2.selectbox("월", list(range(1, 13)))
        day = cc3.selectbox("일", list(range(1, 32)))
    with c3:
        gender_label = st.radio("성별", ["남", "여"], horizontal=True)

    time_known = st.checkbox("태어난 시간을 압니다", value=True)
    hour = minute = None
    if time_known:
        tc1, tc2 = st.columns(2)
        hour = tc1.selectbox("시 (0~23시)", list(range(24)), index=12)
        minute = tc2.number_input("분", min_value=0, max_value=59, value=0, step=1)
    else:
        st.caption("시간을 모르면 시주(時柱)는 제외하고 계산합니다.")

    submitted = st.form_submit_button("사주 계산하기", type="primary", width='stretch')

if submitted:
    payload = {
        "birthYear": int(year),
        "birthMonth": int(month),
        "birthDay": int(day),
        "isLunar": calendar_type == "음력",
        "isFemale": gender_label == "여",
        "modules": SAZU_MODULES,
    }
    if calendar_type == "음력":
        payload["isLeapMonth"] = bool(is_leap)
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

body = st.session_state.get("sazu_body")
if body:
    modules = body["data"]["modules"]
    gender_label = st.session_state["gender_label"]

    st.divider()
    st.subheader("사주팔자 원국")
    cols = st.columns(4)
    fp = modules["fourPillars"]
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
