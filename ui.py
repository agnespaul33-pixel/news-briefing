import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import streamlit as st

SETTINGS_FILE = Path(__file__).parent / "settings.json"
SCRIPT_FILE   = Path(__file__).parent / "news_summary.py"

DEFAULT_KO_KEYWORDS: dict[str, str] = {
    "정치":   "한국 정치",
    "경제":   "한국 경제",
    "사회":   "한국 사회",
    "세계":   "세계 국제",
    "IT과학": "IT 과학 기술",
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"korean_keywords": DEFAULT_KO_KEYWORDS.copy()}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 페이지 설정 ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="뉴스 브리핑", page_icon="📰", layout="centered")
st.title("📰 뉴스 브리핑")

# ── session_state 초기화 ─────────────────────────────────────────────────────
if "running" not in st.session_state:
    st.session_state.running = False
if "run_result" not in st.session_state:
    st.session_state.run_result = None

settings = load_settings()
tab_run, tab_settings = st.tabs(["▶ 수동 실행", "⚙ 카테고리 설정"])

# ── 수동 실행 탭 ─────────────────────────────────────────────────────────────
with tab_run:
    today = date.today()
    weekday_ko = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    st.caption(f"오늘: {today.strftime('%Y년 %m월 %d일')} ({weekday_ko}요일)")
    st.write("")

    clicked = st.button(
        "지금 실행",
        disabled=st.session_state.running,
        type="primary",
        use_container_width=True,
        key="run_btn",
    )

    if clicked:
        st.session_state.running = True
        st.session_state.run_result = None
        with st.spinner("뉴스 수집 및 텔레그램 전송 중... (수 분 소요)"):
            proc = subprocess.run(
                [sys.executable, str(SCRIPT_FILE)],
                capture_output=True,
                text=True,
                cwd=str(SCRIPT_FILE.parent),
            )
        st.session_state.running = False
        st.session_state.run_result = {
            "returncode": proc.returncode,
            "output": (proc.stdout + proc.stderr).strip(),
        }
        st.rerun()

    result = st.session_state.run_result
    if result is not None:
        if result["returncode"] == 0:
            st.success("텔레그램 전송 완료!")
        else:
            st.error(f"실행 실패 (exit code {result['returncode']})")
        with st.expander("실행 로그 보기", expanded=result["returncode"] != 0):
            st.code(result["output"], language="text")

# ── 카테고리 설정 탭 ─────────────────────────────────────────────────────────
with tab_settings:
    st.subheader("국내 뉴스 카테고리 키워드")
    st.caption("Google 뉴스 검색에 사용되는 키워드입니다. 저장 후 다음 실행부터 적용됩니다.")
    st.divider()

    saved_kw = settings.get("korean_keywords", DEFAULT_KO_KEYWORDS.copy())
    new_kw: dict[str, str] = {}

    for cat, default_val in DEFAULT_KO_KEYWORDS.items():
        col_label, col_input = st.columns([1, 3])
        with col_label:
            st.write(f"**{cat}**")
        with col_input:
            new_kw[cat] = st.text_input(
                label=cat,
                value=saved_kw.get(cat, default_val),
                key=f"kw_{cat}",
                label_visibility="collapsed",
            )

    st.divider()
    if st.button("저장", type="primary", key="save_btn"):
        settings["korean_keywords"] = new_kw
        save_settings(settings)
        st.success("저장되었습니다. 다음 실행부터 새 키워드가 적용됩니다.")
