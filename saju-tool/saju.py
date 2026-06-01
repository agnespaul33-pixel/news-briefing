#!/usr/bin/env python3
"""사주팔자 분석 도구 — 사주첩경 · 명리정종 · 자평진전 · 적천수 기반 / Gemini AI"""

import os
import sys
from pathlib import Path

# .env 파일 로드
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    sys.exit("오류: google-genai 패키지가 필요합니다.\n  pip install google-genai")

# ── 천간 (Heavenly Stems) ────────────────────────────────────────────────────
STEMS    = ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛', '壬', '癸']
STEMS_KR = ['갑', '을', '병', '정', '무', '기', '경', '신', '임', '계']
STEM_EL  = ['木', '木', '火', '火', '土', '土', '金', '金', '水', '水']

# ── 지지 (Earthly Branches) ─────────────────────────────────────────────────
BRANCHES    = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']
BRANCHES_KR = ['자', '축', '인', '묘', '진', '사', '오', '미', '신', '유', '술', '해']
BRANCH_EL   = ['水', '土', '木', '木', '土', '火', '火', '土', '金', '金', '土', '水']

# 월별 절기 시작일 근사치 (소한·입춘·경칩·청명·입하·망종·소서·입추·백로·한로·입동·대설)
SOLAR_TERM_DAY = {
    1: 6,   # 소한(小寒)  ~1/6
    2: 4,   # 입춘(立春)  ~2/4
    3: 6,   # 경칩(驚蟄)  ~3/6
    4: 5,   # 청명(清明)  ~4/5
    5: 6,   # 입하(立夏)  ~5/6
    6: 6,   # 망종(芒種)  ~6/6
    7: 7,   # 소서(小暑)  ~7/7
    8: 7,   # 입추(立秋)  ~8/7
    9: 8,   # 백로(白露)  ~9/8
    10: 8,  # 한로(寒露)  ~10/8
    11: 7,  # 입동(立冬)  ~11/7
    12: 7,  # 대설(大雪)  ~12/7
}

# 지지 인덱스: 절기월 기준 (소한 이후 1월=丑, 입춘 이후 2월=寅 ...)
MONTH_TO_BRANCH = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
                   7: 7, 8: 8, 9: 9, 10: 10, 11: 11, 12: 0}

# 인월(寅=2)을 기준 offset 0으로 하는 월별 오프셋
BRANCH_OFFSET = {2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5,
                 8: 6, 9: 7, 10: 8, 11: 9, 0: 10, 1: 11}


# ── 사주 계산 ────────────────────────────────────────────────────────────────

def _jdn(year: int, month: int, day: int) -> int:
    """그레고리력 → 율리우스 적일수(Julian Day Number)"""
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def year_pillar(year: int):
    idx = (year - 4) % 60
    return STEMS[idx % 10], BRANCHES[idx % 12]


def month_pillar(year: int, month: int, day: int):
    # 절기 이후이면 해당 월, 이전이면 전월 취급
    cur = month if day >= SOLAR_TERM_DAY.get(month, 6) else (month - 1 or 12)
    b_idx = MONTH_TO_BRANCH[cur]

    # 오호둔월법(五虎遁月法): 연간별 인월(寅月) 천간 기준
    # 甲己→丙, 乙庚→戊, 丙辛→庚, 丁壬→壬, 戊癸→甲
    y_stem_idx = STEMS.index(year_pillar(year)[0])
    base = [2, 4, 6, 8, 0, 2, 4, 6, 8, 0][y_stem_idx]
    s_idx = (base + BRANCH_OFFSET[b_idx]) % 10

    return STEMS[s_idx], BRANCHES[b_idx]


def day_pillar(year: int, month: int, day: int):
    jd = _jdn(year, month, day)
    return STEMS[jd % 10], BRANCHES[(jd + 6) % 12]


def hour_pillar(day_stem: str, hour: int):
    # 자시(子時) 23~00시, 축시(丑時) 01~02시 ...
    h_b_idx = 0 if hour == 23 else (hour + 1) // 2

    # 오자둔시법(五子遁時法): 일간별 자시(子時) 천간
    # 甲己→甲子, 乙庚→丙子, 丙辛→戊子, 丁壬→庚子, 戊癸→壬子
    d_s_idx = STEMS.index(day_stem)
    base = [0, 2, 4, 6, 8, 0, 2, 4, 6, 8][d_s_idx]
    h_s_idx = (base + h_b_idx) % 10

    return STEMS[h_s_idx], BRANCHES[h_b_idx]


def kr(char: str) -> str:
    if char in STEMS:
        return STEMS_KR[STEMS.index(char)]
    return BRANCHES_KR[BRANCHES.index(char)]


def el(char: str) -> str:
    if char in STEMS:
        return STEM_EL[STEMS.index(char)]
    return BRANCH_EL[BRANCHES.index(char)]


def daewoon_direction(gender: str, year_stem: str) -> str:
    is_yang_year = STEMS.index(year_stem) % 2 == 0
    forward = (gender == '남' and is_yang_year) or (gender == '여' and not is_yang_year)
    return '순행(順行)' if forward else '역행(逆行)'


def build_chart(year: int, month: int, day: int, hour: int, gender: str) -> dict:
    ys, yb = year_pillar(year)
    ms, mb = month_pillar(year, month, day)
    ds, db = day_pillar(year, month, day)
    hs, hb = hour_pillar(ds, hour)
    return {
        'year':  (ys, yb),
        'month': (ms, mb),
        'day':   (ds, db),
        'hour':  (hs, hb),
        'gender': gender,
        'birth': f"{year}년 {month}월 {day}일 {hour}시",
        'dw_dir': daewoon_direction(gender, ys),
    }


# ── 출력 ─────────────────────────────────────────────────────────────────────

def display_chart(c: dict):
    ys, yb = c['year']; ms, mb = c['month']
    ds, db = c['day'];  hs, hb = c['hour']
    day_yin_yang = '양(陽)' if STEMS.index(ds) % 2 == 0 else '음(陰)'

    print()
    print("━" * 54)
    print(f"  생년월일시: {c['birth']}  ({c['gender']}성)")
    print("━" * 54)
    print(f"          시(時)    일(日)    월(月)    연(年)")
    print(f"  천간(天干): {hs}{kr(hs)}({el(hs)})   {ds}{kr(ds)}({el(ds)})   {ms}{kr(ms)}({el(ms)})   {ys}{kr(ys)}({el(ys)})")
    print(f"  지지(地支): {hb}{kr(hb)}({el(hb)})   {db}{kr(db)}({el(db)})   {mb}{kr(mb)}({el(mb)})   {yb}{kr(yb)}({el(yb)})")
    print("━" * 54)
    print(f"  일간(日主): {ds}({kr(ds)}) {day_yin_yang}간 {el(ds)}  │  대운: {c['dw_dir']}")
    print("━" * 54)
    print()


# ── 프롬프트 ─────────────────────────────────────────────────────────────────

def make_prompt(c: dict) -> str:
    ys, yb = c['year']; ms, mb = c['month']
    ds, db = c['day'];  hs, hb = c['hour']
    gender = c['gender']
    day_yin_yang = '양' if STEMS.index(ds) % 2 == 0 else '음'
    spouse_star = '재성(財星)' if gender == '남' else '관성(官星)'

    return f"""당신은 사주첩경(四柱捷徑), 명리정종(命理正宗), 자평진전(子平眞詮), 적천수(滴天髓)에 정통한 최고 수준의 명리학자입니다.
아래 사주팔자를 고전 명리 원칙에 따라 엄밀하고 심층적으로 분석하십시오.

═══════════════════════════════════════════════
【 사주팔자 (四柱八字) 】
생년월일시: {c['birth']} ({gender}성)

        시(時)    일(日)    월(月)    연(年)
천간(天干):  {hs}({kr(hs)})     {ds}({kr(ds)})     {ms}({kr(ms)})     {ys}({kr(ys)})
지지(地支):  {hb}({kr(hb)})     {db}({kr(db)})     {mb}({kr(mb)})     {yb}({kr(yb)})

일간(日主): {ds}({kr(ds)}) — {day_yin_yang}간 {el(ds)}
대운 방향: {c['dw_dir']} ({gender}성 {'양' if STEMS.index(ys) % 2 == 0 else '음'}년생 기준)
═══════════════════════════════════════════════

다음 5개 항목을 순서대로, 각 항목을 충분한 깊이와 임팩트 있게 분석하십시오.
고전 원문을 적절히 인용하고, 단순 나열이 아닌 이 사주 고유의 유기적 해석으로 작성하십시오.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 1. 신강신약(身强身弱) 판단 및 격국(格局)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 월령(月令) — 득령(得令)/실령(失令): {ms}({kr(ms)})월 {el(ms)}기운이 일간 {el(ds)}을 생하는가 극하는가
• 득지(得地)/실지(失地): 일지(日支) {db}({kr(db)})의 통근(通根) 여부와 강약에 미치는 영향
• 득세(得勢)/실세(失勢): 비겁·인성의 생조 총량 vs 식상·재성·관성의 극설 총량 비교
• 최종 판정 — 신강·신약·중화 중 어디에 해당하며 그 정도를 명확히 판정
• 격국(格局): 월지({mb}{kr(mb)}) 지장간의 투출 여부로 格 결정, 成格/破格/變格 판단

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 2. 용신(用神) · 기신(忌神) · 희신(喜神) 분석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 억부용신(抑扶用神): 신강이면 식상·재·관으로 억제, 신약이면 비겁·인성으로 부조하는 원칙으로 용신 도출
• 조후용신(調候用神): {mb}({kr(mb)})월 {el(mb)}계절 기준 한난조습(寒暖燥濕) 조절 오행 필요 여부
• 용신의 오행·십신을 명시하고, 사주 원국 내 통근(通根) 여부와 역량 평가
• 희신(喜神, 용신을 생조) / 기신(忌神, 용신을 극설) / 한신(閑神) 구분
• 사주첩경 원칙: "용신무기 종신궁핍(用神無氣 終身窮乏)" — 이 사주의 용신 역량을 이 기준으로 평가

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 3. 대운(大運) 흐름 — 길흉 판단
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 대운 방향: {c['dw_dir']} — 월주({ms}({kr(ms)}){mb}({kr(mb)}))를 기점으로 대운 흐름 전개
• 현재(2026년 기준) 나이에서의 현행 대운 추정
• 향후 3개 대운의 천간·지지를 제시하고, 각 대운의 오행이 용신·기신과 어떻게 작용하는지 길흉 판단
• 특히 주의해야 할 흉운: 구체적 이유, 합충(合沖) 관계, 경계해야 할 사안 명시
• 일생에서 가장 빛나는 대운과 가장 험난한 대운 비교

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 4. 재물운(財物運)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 재성(財星)의 위치·강약·통근 여부 분석 — 재고(財庫) 존재 여부 포함
• 식상생재(食傷生財) 구조 존재 여부 — 재물 생성 경로와 역량
• 비겁탈재(比劫奪財), 관살파재(官殺破財), 재다신약(財多身弱) 위험 여부
• 재물 축적 스타일: 사업형 / 직장형 / 투자형 중 어느 경로가 적합한가
• 재물운의 시기적 흐름 — 전성기 대운과 재산 손실 위험 시기

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 5. 이성운(異性運) · 배우자운
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• {gender}성 배우자성: {spouse_star} — 위치·강약·형충파해(刑沖破害) 여부 분석
• 배우자궁(일지 {db}{kr(db)}) 분석 — 배우자의 성향, 인연의 성격, 궁과 성의 일치 여부
• 천간·지지 합(合)으로 보는 이성 인연의 시기와 만남의 패턴
• 결혼운의 길흉 — 결혼 시기, 배우자 유형, 부부 관계의 안정성
• 주의해야 할 이성 관계 패턴, 배우자와의 갈등 요인, 이별·이혼 위험 여부

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 종합 총평 및 인생 조언
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
이 사주의 핵심 테마, 강점, 최대 리스크, 삶의 방향성을 3~5문장으로 마무리하십시오.
고전 원문(적천수, 사주첩경 등) 한 구절을 인용하여 이 명(命)을 관통하는 키워드로 마무리하십시오.

※ 분석은 구체적이고 날카롭게. 추상적 나열은 지양하고 이 사주 고유의 특징을 임팩트 있게 짚어내십시오.
※ 한국어로 작성하되 전문 용어는 반드시 한자를 병기하십시오."""


# ── Gemini API 호출 ──────────────────────────────────────────────────────────

def analyze(c: dict):
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        sys.exit(
            "오류: GEMINI_API_KEY 환경변수가 설정되지 않았습니다.\n"
            "  export GEMINI_API_KEY='your-api-key'  (Linux/Mac)\n"
            "  set GEMINI_API_KEY=your-api-key       (Windows CMD)\n"
            "Google AI Studio에서 무료로 발급: https://aistudio.google.com/app/apikey"
        )

    model_name = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')

    client = genai.Client(api_key=api_key)

    display_chart(c)
    print(f"  Gemini ({model_name}) 분석 중... 잠시 기다려주세요.\n")
    print("━" * 54 + "\n")

    response = client.models.generate_content(
        model=model_name,
        contents=make_prompt(c),
        config=genai_types.GenerateContentConfig(
            temperature=0.75,
            max_output_tokens=8192,
        ),
    )

    print(response.text)
    print("\n" + "━" * 54)
    print("  ※ 본 분석은 사주첩경·명리정종·자평진전·적천수 이론을 기반으로 AI가 생성했습니다.")
    print("━" * 54)


# ── 입력 ─────────────────────────────────────────────────────────────────────

def ask_int(prompt: str, lo: int, hi: int) -> int:
    while True:
        try:
            v = int(input(prompt))
            if lo <= v <= hi:
                return v
            print(f"  {lo}~{hi} 사이의 값을 입력하세요.")
        except ValueError:
            print("  숫자를 입력하세요.")


def prompt_user():
    print()
    print("━" * 54)
    print("  사주팔자 분석 시스템")
    print("  사주첩경 · 명리정종 · 자평진전 · 적천수 기반")
    print("━" * 54)

    year  = ask_int("\n출생 연도 (예: 1990): ", 1900, 2025)
    month = ask_int("출생 월   (1~12): ", 1, 12)
    day   = ask_int("출생 일   (1~31): ", 1, 31)
    print("출생 시   (0~23) — 자시(子時)는 23 또는 0, 모르면 12 입력")
    hour  = ask_int("출생 시: ", 0, 23)

    while True:
        gender = input("성별      (남/여): ").strip()
        if gender in ('남', '여'):
            break
        print("  '남' 또는 '여'를 입력하세요.")

    return year, month, day, hour, gender


# ── 진입점 ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) == 6:
        try:
            year, month, day, hour = map(int, sys.argv[1:5])
            gender = sys.argv[5]
            if gender not in ('남', '여'):
                raise ValueError
        except ValueError:
            sys.exit(
                "사용법: python saju.py <연도> <월> <일> <시> <남|여>\n"
                "  예시: python saju.py 1990 3 15 10 여"
            )
    else:
        year, month, day, hour, gender = prompt_user()

    chart = build_chart(year, month, day, hour, gender)
    analyze(chart)


if __name__ == '__main__':
    main()
