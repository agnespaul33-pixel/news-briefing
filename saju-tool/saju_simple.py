#!/usr/bin/env python3
"""사주 해설기 — 8글자 직접 입력 → Gemini 해설 출력"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass  # python-dotenv 없으면 환경변수에서 직접 읽음

try:
    from google import genai
except ImportError:
    sys.exit("오류: pip install google-genai")

STEMS    = ['甲','乙','丙','丁','戊','己','庚','辛','壬','癸']
STEMS_KR = ['갑','을','병','정','무','기','경','신','임','계']
BRANCHES    = ['子','丑','寅','卯','辰','巳','午','未','申','酉','戌','亥']
BRANCHES_KR = ['자','축','인','묘','진','사','오','미','신','유','술','해']

ALL_CHARS = STEMS + BRANCHES
ALL_KR    = STEMS_KR + BRANCHES_KR

STEM_EL   = ['木','木','火','火','土','土','金','金','水','水']
BRANCH_EL = ['水','土','木','木','土','火','火','土','金','金','土','水']

def el(c):
    if c in STEMS:   return STEM_EL[STEMS.index(c)]
    if c in BRANCHES: return BRANCH_EL[BRANCHES.index(c)]
    return '?'

def kr(c):
    if c in STEMS:    return STEMS_KR[STEMS.index(c)]
    if c in BRANCHES: return BRANCHES_KR[BRANCHES.index(c)]
    return '?'

def parse_char(raw: str):
    """한자 또는 한글 음으로 천간/지지 인식"""
    raw = raw.strip()
    if raw in ALL_CHARS:
        return raw
    if raw in ALL_KR:
        idx = ALL_KR.index(raw)
        return ALL_CHARS[idx]
    return None

def input_saju():
    print()
    print("━" * 50)
    print("  사주 해설기  (사주첩경 · 고전 명리 기반)")
    print("━" * 50)
    print()
    print("  천간: 甲乙丙丁戊己庚辛壬癸  (또는 갑을병정무기경신임계)")
    print("  지지: 子丑寅卯辰巳午未申酉戌亥  (또는 자축인묘진사오미신유술해)")
    print()

    pillars = ['연주(年柱)', '월주(月柱)', '일주(日柱)', '시주(時柱)']
    result = []
    for p in pillars:
        while True:
            raw = input(f"  {p} 천간: ").strip()
            s = parse_char(raw)
            if s and s in STEMS:
                break
            print(f"    → 인식 불가: '{raw}'. 천간(甲~癸 또는 갑~계)을 입력하세요.")
        while True:
            raw = input(f"  {p} 지지: ").strip()
            b = parse_char(raw)
            if b and b in BRANCHES:
                break
            print(f"    → 인식 불가: '{raw}'. 지지(子~亥 또는 자~해)를 입력하세요.")
        result.append((s, b))
        print()

    while True:
        g = input("  성별 (남/여): ").strip()
        if g in ('남', '여'):
            break
        print("    → '남' 또는 '여'를 입력하세요.")

    return result, g   # [(연간,연지),(월간,월지),(일간,일지),(시간,시지)], 성별

def display(pillars, gender):
    (ys,yb),(ms,mb),(ds,db),(hs,hb) = pillars
    day_yy = '양(陽)' if STEMS.index(ds) % 2 == 0 else '음(陰)'

    print()
    print("━" * 50)
    print(f"  {'남' if gender=='남' else '여'}성  사주팔자")
    print("━" * 50)
    print(f"          시(時)   일(日)   월(月)   연(年)")
    print(f"  천간(天干): {hs}{kr(hs)}({el(hs)})  {ds}{kr(ds)}({el(ds)})  {ms}{kr(ms)}({el(ms)})  {ys}{kr(ys)}({el(ys)})")
    print(f"  지지(地支): {hb}{kr(hb)}({el(hb)})  {db}{kr(db)}({el(db)})  {mb}{kr(mb)}({el(mb)})  {yb}{kr(yb)}({el(yb)})")
    print("━" * 50)
    print(f"  일간(日主): {ds}({kr(ds)}) {day_yy}간 {el(ds)}")
    print("━" * 50)
    print()

def make_prompt(pillars, gender):
    (ys,yb),(ms,mb),(ds,db),(hs,hb) = pillars
    day_yy = '양' if STEMS.index(ds) % 2 == 0 else '음'
    spouse = '재성(財星)' if gender == '남' else '관성(官星)'

    return f"""당신은 사주첩경(四柱捷徑), 명리정종(命理正宗), 자평진전(子平眞詮), 적천수(滴天髓)에 정통한 명리학자입니다.
아래 사주팔자를 고전 이론에 근거하여 전문적이고 임팩트 있게 분석하십시오.

【 사주팔자 (四柱八字) 】
        시(時)   일(日)   월(月)   연(年)
천간(天干):  {hs}({kr(hs)})    {ds}({kr(ds)})    {ms}({kr(ms)})    {ys}({kr(ys)})
지지(地支):  {hb}({kr(hb)})    {db}({kr(db)})    {mb}({kr(mb)})    {yb}({kr(yb)})

일간(日主): {ds}({kr(ds)}) — {day_yy}간 {el(ds)} / {gender}성

다음 5개 항목을 순서대로 분석하십시오. 각 항목은 고전 원문을 적절히 인용하고, 이 사주 고유의 특징을 날카롭게 짚어내십시오.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 1. 신강신약(身强身弱) 판단 및 격국(格局)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 월령 득령/실령, 득지/실지, 득세/실세 기준으로 일간 강약 판정
• 신강·신약·중화 중 판정 및 그 정도
• 격국(格局): 월지 {mb}({kr(mb)}) 지장간 투출로 格 결정, 成格/破格 여부

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 2. 용신(用神) · 기신(忌神) · 희신(喜神)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 억부용신: 신강이면 억제, 신약이면 부조 원칙으로 용신 도출
• 조후용신: {mb}({kr(mb)})월 계절 기준 한난조습 조절 필요 여부
• 용신 오행·십신 명시, 사주 내 통근 여부와 역량
• 희신/기신/한신 구분 및 사주첩경 "용신무기 종신궁핍" 원칙으로 평가

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 3. 대운(大運) 흐름 — 길흉 판단
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• {gender}성 {'양' if STEMS.index(ys) % 2 == 0 else '음'}년생 기준 대운 방향(순행/역행)
• 향후 주요 대운 3개의 천간·지지 제시 및 용신·기신과의 작용으로 길흉 판단
• 특히 주의할 흉운과 그 이유, 합충 관계 분석
• 인생에서 가장 빛나는 대운과 가장 위험한 대운 비교

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 4. 재물운(財物運)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 재성의 위치·강약·통근 여부, 재고(財庫) 존재 여부
• 식상생재 구조 및 재물 생성 경로
• 비겁탈재·재다신약 등 위험 여부
• 재물 축적 스타일(사업형/직장형/투자형)과 재물운 전성기·위험 시기

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 5. 이성운(異性運) · 배우자운
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• {gender}성 배우자성 {spouse} — 위치·강약·형충파해 여부
• 배우자궁(일지 {db}{kr(db)}) 분석 및 배우자 성향
• 인연의 시기, 결혼운 길흉, 부부 안정성
• 주의해야 할 이성 관계 패턴

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 종합 총평
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
핵심 테마·강점·최대 리스크·삶의 방향성을 3~5문장으로 마무리하고,
고전 원문 한 구절로 이 명(命)을 관통하는 키워드를 제시하십시오.

※ 한국어로 작성하되 전문 용어는 한자 병기. 추상적 나열 금지, 이 사주 고유의 특징을 임팩트 있게."""

def analyze(pillars, gender):
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        sys.exit("오류: GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    model_name = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash')
    client = genai.Client(api_key=api_key)

    display(pillars, gender)
    print(f"  Gemini ({model_name}) 분석 중...\n")
    print("━" * 50 + "\n")

    for chunk in client.models.generate_content_stream(
        model=model_name,
        contents=make_prompt(pillars, gender),
    ):
        if chunk.text:
            print(chunk.text, end="", flush=True)

    print("\n\n" + "━" * 50)
    print("  ※ 사주첩경·명리정종·자평진전·적천수 기반 AI 해설")
    print("━" * 50)

def main():
    # 커맨드라인: saju_simple.py 연간 연지 월간 월지 일간 일지 시간 시지 성별
    if len(sys.argv) == 10:
        chars = sys.argv[1:9]
        gender = sys.argv[9]
        parsed = [parse_char(c) for c in chars]
        if any(p is None for p in parsed) or gender not in ('남','여'):
            sys.exit("사용법: python saju_simple.py 甲 子 丙 寅 庚 申 辛 巳 남")
        pillars = [(parsed[i], parsed[i+1]) for i in range(0, 8, 2)]
    else:
        pillars, gender = input_saju()

    analyze(pillars, gender)

if __name__ == '__main__':
    main()
