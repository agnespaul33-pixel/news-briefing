#!/usr/bin/env python3
"""saju_app.py에 반복적으로 되돌아오는 알려진 회귀 6가지를 한 번에 재적용.

다른 세션/기기에서 작업한 saju_app.py를 넘겨받을 때마다, 이전에 여기서 고친
아래 6가지가 매번 예전 스냅샷 기준으로 되돌아와 있었습니다. 새 파일을 넘기기 전에
이 스크립트를 먼저 돌리면 이미 알려진 부분은 자동으로 정리됩니다.

  1. GEMINI_MODEL 기본값 (gemini-2.5-flash-lite → gemini-2.5-flash, 이미 404)
  2. _extract_char 한글→한자 변환 로직 (SAZU가 천간·지지를 한자가 아닌 한글로 반환)
  3. format_wolwoon의 도달 불가능한 죽은 코드
  4. _char_elem_bg_color의 한자/한글 오행 키 불일치 (배경색이 항상 회색)
  5. render_saju_dashboard_table의 한자/한글 오행 키 불일치 (매 제출마다 크래시)
  6. render_daewoon_strip_from_sazu의 한자/한글 오행 키 불일치 (대운 스트립 색상 미표시)

사용법:
    python saju_fix_known_regressions.py [파일경로]
    파일경로를 생략하면 이 스크립트와 같은 폴더의 saju_app.py를 대상으로 합니다.

이미 고쳐져 있는 항목은 건너뜁니다(멱등 — 여러 번 실행해도 안전).
알려진 패턴을 찾지 못한 항목은 경고만 출력합니다 — 코드가 그 사이 더 바뀌어서
패턴이 안 맞을 수 있으니 그런 경우는 수동으로 확인하세요.
"""

import sys
from pathlib import Path

FIXES = [
    (
        "1. GEMINI_MODEL 기본값",
        '''# 주의: gemini-1.5-flash는 이미 서비스 종료(404). gemini-2.5-flash는 2026-10-16 종료 예정.
# 필요 시 GEMINI_MODEL 환경변수로 gemini-3.1-flash-lite 등으로 전환 테스트 가능.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")''',
        '''# 주의: gemini-1.5-flash, gemini-2.5-flash-lite는 이미 이 계정에서 사용 불가(404).
# gemini-2.5-flash는 2026-10-16 이후 종료 예정(Google 공식, 확정일은 6개월 전 재공지) —
# 그때는 GEMINI_MODEL 환경변수로 gemini-3.6-flash 등으로 전환.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")''',
        False,
    ),
    (
        "2a. HANJA_ELEMENT_TO_KR 상수 추가",
        'PILLAR_LABELS = [("hour", "시주(時)"), ("day", "일주(日)"), ("month", "월주(月)"), ("year", "연주(年)")]',
        '''PILLAR_LABELS = [("hour", "시주(時)"), ("day", "일주(日)"), ("month", "월주(月)"), ("year", "연주(年)")]
# STEM_ELEMENT/BRANCH_ELEMENT(아래 정의)는 오행을 한자(木火土金水)로 반환하는데,
# elem_count 등 집계용 딕셔너리는 한글 키(목화토금수)를 쓰므로 여기서 변환한다.
HANJA_ELEMENT_TO_KR = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}''',
        False,
    ),
    (
        "2b. _extract_char 한글→한자 변환",
        '''def _extract_char(text: str | None, charset: str) -> str | None:
    """표시용 문자열(예: '자(子)', '갑')에서 원본 한자 1글자를 추출."""
    if not text:
        return None
    for ch in text:
        if ch in charset:
            return ch''',
        '''# SAZU API는 skyFull/earthFull을 한자가 아닌 한글로 반환한다(예: 일간 己=='기토', 일지 卯=='묘목').
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
                return mapped''',
        False,
    ),
    (
        "3. format_wolwoon 죽은 코드 제거",
        '''def format_wolwoon(wo_stem: str, wo_branch: str) -> str:
    return f"  월운 간지: {wo_stem}{wo_branch}"
    if len(lines) == 1:
        lines.append("  (원국·세운과 특별한 충돌 없음)")
    return "\\n".join(lines)''',
        '''def format_wolwoon(wo_stem: str, wo_branch: str) -> str:
    return f"  월운 간지: {wo_stem}{wo_branch}"''',
        True,  # new가 old의 앞부분(prefix)이라 "new in text"만으로는 항상 참이 되어
               # 미적용 상태를 못 잡아낸다 — old(전체 죽은 코드 포함)부터 먼저 확인해야 함.
    ),
    (
        "4. _char_elem_bg_color 한자/한글 불일치 (배경색 항상 회색)",
        '''def _char_elem_bg_color(ch: str, is_stem: bool) -> tuple[str, str]:
    elem = STEM_ELEMENT.get(ch) if is_stem else BRANCH_ELEMENT.get(ch)
    return ELEMENT_BG.get(elem, "#eeeeee"), ELEMENT_TEXT_ON_BG.get(elem, "#000000")''',
        '''def _char_elem_bg_color(ch: str, is_stem: bool) -> tuple[str, str]:
    elem_hanja = STEM_ELEMENT.get(ch) if is_stem else BRANCH_ELEMENT.get(ch)
    elem = HANJA_ELEMENT_TO_KR.get(elem_hanja)
    return ELEMENT_BG.get(elem, "#eeeeee"), ELEMENT_TEXT_ON_BG.get(elem, "#000000")''',
        False,
    ),
    (
        "5. render_saju_dashboard_table 한자/한글 불일치 (제출마다 크래시)",
        # elem_count 선언(한글 키)까지 하나의 앵커 블록에 포함시킨다 — 증가 로직
        # 텍스트만 앵커로 쓰면, 이후 버전에서 elem_count 자체를 한자 키로
        # 리팩터링했을 때(증가 로직 텍스트는 우연히 똑같이 남을 수 있음) 이 fix가
        # 오작동해서 이미 한자 키인 딕셔너리에 HANJA_ELEMENT_TO_KR로 또 감싼 한글
        # 키로 접근해 새 KeyError를 만든다(2026-08-28 saju_app (13).py에서 실제로
        # 이렇게 고장난 것을 발견 — 반드시 선언 줄까지 같이 매칭되어야 안전).
        '''    elem_count = {"목": 0, "화": 0, "토": 0, "금": 0, "수": 0}

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
            elem_count[BRANCH_ELEMENT[b]] += 1''',
        '''    elem_count = {"목": 0, "화": 0, "토": 0, "금": 0, "수": 0}

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
            elem_count[HANJA_ELEMENT_TO_KR[STEM_ELEMENT[s]]] += 1
        if b and b in BRANCH_ELEMENT:
            elem_count[HANJA_ELEMENT_TO_KR[BRANCH_ELEMENT[b]]] += 1''',
        False,
    ),
    (
        "6. render_daewoon_strip_from_sazu 한자/한글 불일치 (대운 스트립 색상 미표시)",
        '    rows = [(str(item["startAge"]), item["full"][0], item["full"][1], "") for item in display]',
        '''    # item["full"]은 SAZU가 한글로 주는 간지 표시문자열(예: "계미") — 색상 조회는 한자
    # 기준(STEM_CHARS/BRANCH_CHARS)이므로 한글→한자로 변환해서 넘긴다.
    rows = [
        (
            str(item["startAge"]),
            STEM_HANGUL_TO_HANJA.get(item["full"][0], item["full"][0]),
            BRANCH_HANGUL_TO_HANJA.get(item["full"][1], item["full"][1]),
            "",
        )
        for item in display
    ]''',
        False,
    ),
]


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "saju_app.py"
    if not target.is_file():
        sys.exit(f"파일을 찾을 수 없습니다: {target}")

    text = target.read_text(encoding="utf-8")
    applied, skipped, missing = [], [], []

    for name, old, new, check_old_first in FIXES:
        # 보통은 new가 있는지부터 확인해 "이미 적용됨"을 판정한다. 다만 삭제형 fix
        # (new가 old의 부분 문자열인 경우, check_old_first=True)는 순서를 뒤집어야
        # 한다 — new부터 확인하면 old(미적용 상태)가 남아 있어도 항상 "이미
        # 적용됨"으로 오판하기 때문. 반대로 삽입형 fix(old가 new의 부분 문자열)는
        # 기본 순서(new 먼저)를 써야 재실행 시 중복 삽입을 피할 수 있다.
        first_check, second_check = (old, new) if check_old_first else (new, old)
        if first_check in text:
            if check_old_first:
                text = text.replace(old, new, 1)
                applied.append(name)
            else:
                skipped.append(name)
        elif second_check in text:
            if check_old_first:
                skipped.append(name)
            else:
                text = text.replace(old, new, 1)
                applied.append(name)
        else:
            missing.append(name)

    target.write_text(text, encoding="utf-8")

    if applied:
        print("적용됨:")
        for n in applied:
            print(f"  ✓ {n}")
    if skipped:
        print("이미 적용되어 있어 건너뜀:")
        for n in skipped:
            print(f"  - {n}")
    if missing:
        print("⚠ 패턴을 찾지 못함 (코드가 바뀌었을 수 있음 — 수동 확인 필요):")
        for n in missing:
            print(f"  ? {n}")

    print(f"\n완료: {target}")


if __name__ == "__main__":
    main()
