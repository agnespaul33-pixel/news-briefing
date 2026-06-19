import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="생활 대시보드",
    page_icon="🏠",
    layout="wide",
)

_missing = [v for v in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(v)]
if _missing:
    st.error(f"환경변수 누락: {', '.join(_missing)}")
    st.stop()

from news_summary import (
    fetch_candidates, _select_articles, summarize_batch,
    build_messages, send_telegram, save_to_notion,
    KOREAN_FEEDS, WORLD_FEEDS, WORLD_TARGET, CATEGORY_EMOJI,
    _strip_markdown, GEMINI_API_KEY, NOTION_TOKEN,
)
from fridge_bot import get_all, upsert, consume
from google import genai
from notion_client import Client as NotionClient

KST = timezone(timedelta(hours=9))
SETTINGS_FILE = Path(__file__).parent / "settings.json"
DEFAULT_KO_KEYWORDS: dict[str, str] = {
    "경제":      "한국 경제 증시",
    "주식":      "코스피 코스닥 주식시장",
    "부동산":    "부동산 아파트 분양",
    "환율":      "원달러 환율 외환시장",
    "금리":      "금리 한국은행 기준금리",
    "매일경제":    "site:mk.co.kr",
    "한국경제":    "site:hankyung.com",
    "머니투데이":  "site:mt.co.kr",
    "연합인포맥스": "site:einfomax.co.kr",
}

if "news_data" not in st.session_state:
    st.session_state.news_data = None

# ── 사이드바 ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙ 뉴스 키워드 설정")
    st.caption("저장 후 다음 실행부터 적용")
    settings = {}
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    saved_kw = settings.get("korean_keywords", DEFAULT_KO_KEYWORDS.copy())
    new_kw: dict[str, str] = {}
    for cat, default_val in DEFAULT_KO_KEYWORDS.items():
        new_kw[cat] = st.text_input(cat, value=saved_kw.get(cat, default_val), key=f"kw_{cat}")
    if st.button("저장", type="secondary", use_container_width=True):
        settings["korean_keywords"] = new_kw
        SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success("저장됐습니다")

# ── 탭 ───────────────────────────────────────────────────────────────────────
tab_news, tab_fridge = st.tabs(["📈 경제 뉴스", "🧊 냉장고 재고"])


# ════════════════════════════════════════════════════════════════════════════
# 탭 1 : 경제 뉴스
# ════════════════════════════════════════════════════════════════════════════
with tab_news:
    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.title("📈 경제 뉴스 브리핑")
        now_str = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M KST")
        if st.session_state.news_data:
            st.caption(f"업데이트: {st.session_state.news_data['fetched_at']}  ·  현재: {now_str}")
        else:
            st.caption(now_str)
    with col_btn:
        st.write(""); st.write("")
        run_clicked = st.button("🔄 새로고침", type="primary", use_container_width=True)

    st.divider()

    if run_clicked:
        with st.status("뉴스 불러오는 중...", expanded=True) as status:
            st.write("📡 RSS 피드 수집 중...")
            ko_cand    = fetch_candidates(KOREAN_FEEDS)
            world_cand = fetch_candidates(WORLD_FEEDS)

            st.write("🤖 Gemini 요약 중...")
            client     = genai.Client(api_key=GEMINI_API_KEY)
            ko_arts    = _select_articles(ko_cand)
            world_arts = _select_articles(world_cand, limit=WORLD_TARGET)
            all_arts   = summarize_batch(ko_arts + world_arts, client)
            n_ko       = len(ko_arts)
            ko_articles    = all_arts[:n_ko]
            world_articles = all_arts[n_ko:]

            st.write("📨 텔레그램 전송 중...")
            ko_msg, world_msg = build_messages(ko_articles, world_articles)
            t_ok = send_telegram(ko_msg) and send_telegram(world_msg)

            st.write("📝 Notion 저장 중...")
            save_to_notion(ko_articles, world_articles)
            status.update(label="✅ 완료!", state="complete")

        st.session_state.news_data = {
            "ko": ko_articles, "world": world_articles,
            "fetched_at": datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M"),
            "telegram_ok": t_ok,
        }
        st.rerun()

    def render_article(art: dict):
        emoji   = CATEGORY_EMOJI.get(art["category"], "📌")
        title   = _strip_markdown(art.get("ko_title") or art["title"])
        summary = art.get("summary")
        link    = art.get("link", "")
        with st.container(border=True):
            st.markdown(f"**{emoji} {art['category']}**")
            st.markdown(f"**{title}**")
            if summary:
                st.write(_strip_markdown(summary))
            if link:
                st.link_button("기사 보기 →", link, use_container_width=True)

    if st.session_state.news_data:
        d = st.session_state.news_data
        if d["telegram_ok"]:
            st.success("텔레그램 전송 완료")
        col_ko, col_world = st.columns(2)
        with col_ko:
            st.subheader("🇰🇷 국내 경제")
            for art in d["ko"]:
                render_article(art)
        with col_world:
            st.subheader("🌍 해외 경제")
            for art in d["world"]:
                render_article(art)
    else:
        st.info("🔄 새로고침 버튼을 눌러 최신 뉴스를 가져오세요.")


# ════════════════════════════════════════════════════════════════════════════
# 탭 2 : 냉장고 재고
# ════════════════════════════════════════════════════════════════════════════
with tab_fridge:
    st.title("🧊 냉장고 재고")
    st.caption("텔레그램으로 말하면 5분 이내 자동 반영돼요. 여기서 직접 추가·사용 처리도 가능해요.")

    FRIDGE_DB_ID  = "7200e83a-86f9-4844-965b-ac16c55085ae"
    _notion_token = (
        NOTION_TOKEN
        or st.secrets.get("NOTION_TOKEN", "")
        or os.environ.get("NOTION_TOKEN", "")
    )

    if not _notion_token:
        st.warning("NOTION_TOKEN이 설정되지 않았어요.")
    else:
        notion_f  = NotionClient(auth=_notion_token)
        inventory = get_all(notion_f)

        if inventory:
            by_cat: dict[str, list] = {}
            for it in inventory:
                by_cat.setdefault(it["category"], []).append(it)
            for cat, cat_items in by_cat.items():
                st.subheader(cat)
                cols = st.columns(4)
                for i, it in enumerate(cat_items):
                    with cols[i % 4]:
                        with st.container(border=True):
                            st.markdown(f"**{it['name']}**")
                            st.metric(label="수량", value=f"{it['quantity']}{it['unit']}")
        else:
            st.info("등록된 재료가 없어요.\n텔레그램에서 '달걀 10개 샀어' 처럼 입력해보세요! 🥚")

        st.divider()
        col_add, col_use = st.columns(2)

        with col_add:
            with st.expander("➕ 직접 추가", expanded=True):
                name_in = st.text_input("재료명", key="f_name")
                c1, c2  = st.columns(2)
                qty_in  = c1.number_input("수량", min_value=0.0, step=0.5, key="f_qty")
                unit_in = c2.text_input("단위", value="개", key="f_unit")
                cat_in  = st.selectbox("카테고리", ["채소","육류","해산물","유제품","반찬","냉동","기타"], key="f_cat")
                if st.button("추가", type="primary", use_container_width=True, key="f_add"):
                    if name_in and qty_in > 0:
                        upsert(notion_f, name_in, qty_in, unit_in, cat_in)
                        st.success(f"✅ {name_in} {qty_in}{unit_in} 추가됐어요!")
                        st.rerun()

        with col_use:
            with st.expander("➖ 사용/소비", expanded=True):
                if inventory:
                    names = [it["name"] for it in inventory]
                    sel   = st.selectbox("재료 선택", names, key="f_sel")
                    amt   = st.number_input("사용량", min_value=0.0, step=0.5, key="f_amt")
                    if st.button("사용 처리", type="primary", use_container_width=True, key="f_use"):
                        if amt > 0:
                            ok, remaining, unit = consume(notion_f, sel, amt)
                            if ok:
                                st.success(f"✅ {sel} {amt} 사용. 남은 수량: {remaining}{unit}")
                                st.rerun()
                else:
                    st.info("먼저 재료를 추가해주세요.")
