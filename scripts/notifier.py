# scripts/notifier.py
"""텔레그램 메시지 포맷 및 전송 모듈"""

import sys
import logging
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_2, TELEGRAM_CHAT_ID_3, TELEGRAM_CHAT_ID_DEV,
    GITHUB_PAGES_BASE_URL,
)
from scripts.models import SupplyData, NewsData

logger = logging.getLogger(__name__)

MAX_MSG_LEN = 4096
_preview_mode = False


def set_preview_mode(enabled: bool) -> None:
    global _preview_mode
    _preview_mode = enabled


def _chunks(text: str, size: int = MAX_MSG_LEN) -> list[str]:
    """텍스트를 size 단위로 분할"""
    parts = []
    while len(text) > size:
        split = text.rfind("\n", 0, size)
        if split == -1:
            split = size
        parts.append(text[:split])
        text = text[split:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def send_message(text: str) -> bool:
    """텔레그램으로 메시지 전송. 4096자 초과 시 자동 분할.
    preview 모드: TELEGRAM_CHAT_ID_DEV 단독 발송. 일반: CHAT_ID + CHAT_ID_2."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN 미설정")
        return False

    if _preview_mode:
        if not TELEGRAM_CHAT_ID_DEV:
            logger.error("TELEGRAM_CHAT_ID_DEV 미설정 (--preview 사용 시 .env에 추가 필요)")
            return False
        chat_ids = [TELEGRAM_CHAT_ID_DEV]
        logger.info("[preview] 발송 대상: TELEGRAM_CHAT_ID_DEV")
    else:
        if not TELEGRAM_CHAT_ID:
            logger.error("TELEGRAM_CHAT_ID 미설정")
            return False
        chat_ids = [cid for cid in [TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_2, TELEGRAM_CHAT_ID_3] if cid]
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    success = True
    for chunk in _chunks(text):
        for chat_id in chat_ids:
            try:
                resp = requests.post(
                    url,
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.error(f"텔레그램 전송 실패 [{chat_id}]: {resp.status_code} {resp.text[:200]}")
                    success = False
            except Exception as e:
                logger.error(f"텔레그램 전송 예외 [{chat_id}]: {e}")
                success = False
    return success


def send_private(text: str) -> bool:
    """TELEGRAM_CHAT_ID 단독 발송 — 공유 그룹 제외."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    success = True
    for chunk in _chunks(text):
        try:
            resp = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.error(f"텔레그램 전송 실패 [private]: {resp.status_code} {resp.text[:200]}")
                success = False
        except Exception as e:
            logger.error(f"텔레그램 전송 예외 [private]: {e}")
            success = False
    return success


# ── 포맷 헬퍼 ─────────────────────────────────────────────

def _tv_eok(won: float) -> str:
    return f"{won / 100_000_000:.0f}억"


def _sign(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def _yn(flag) -> str:
    if flag is True:
        return "O"
    if flag is False:
        return "X"
    return "-"


def _supply_str(supply) -> str:
    """SupplyData 객체 또는 dict 모두 처리. supply_label + 5일 누적 표시."""
    if supply is None:
        return "확인불가"
    if isinstance(supply, SupplyData):
        if supply.status == "failed":
            return "확인불가"
        label    = getattr(supply, "supply_label", "") or ""
        inst     = supply.institution_net
        frgn     = supply.foreign_net
        inst_5d  = supply.institution_net_5d
        frgn_5d  = supply.foreign_net_5d
        inst_con = getattr(supply, "institution_consecutive_days", 0)
        frgn_con = getattr(supply, "foreign_consecutive_days", 0)
        date     = supply.supply_date or ""
    else:
        if supply.get("status") == "failed":
            return "확인불가"
        label    = supply.get("supply_label", "") or ""
        inst     = supply.get("institution_net")
        frgn     = supply.get("foreign_net")
        inst_5d  = supply.get("institution_net_5d")
        frgn_5d  = supply.get("foreign_net_5d")
        inst_con = supply.get("institution_consecutive_days", 0)
        frgn_con = supply.get("foreign_consecutive_days", 0)
        date     = supply.get("supply_date") or ""

    def _fmt(v1d, v5d, con, label_name):
        if v1d is None:
            return f"{label_name} -"
        s = f"{label_name} {v1d/100_000_000:+.0f}억"
        if v5d is not None:
            s += f"(5d{v5d/100_000_000:+.0f}억)"
        if con and abs(con) >= 2:
            s += f"({abs(con)}일연속{'매수' if con > 0 else '매도'})"
        return s

    inst_s  = _fmt(inst, inst_5d, inst_con, "기관")
    frgn_s  = _fmt(frgn, frgn_5d, frgn_con, "외국인")
    date_s  = f" ({date})" if date else ""
    label_s = f"[{label}] " if label else ""
    base    = f"{label_s}{inst_s} / {frgn_s}{date_s}"

    return base


def _news_str(news) -> str:
    """NewsData 객체 또는 list 모두 처리"""
    if isinstance(news, NewsData):
        if not news.titles:
            return "뉴스없음"
        return " | ".join(
            f"[{news.keyword_tags[i] if i < len(news.keyword_tags) else '기타'}]{news.titles[i][:20]}"
            for i in range(min(2, len(news.titles)))
        )
    if not news:
        return "뉴스없음"
    return " | ".join(f"[{n.get('keyword','기타')}]{n.get('title','')[:20]}" for n in news[:2])


def _has_news(news) -> bool:
    if isinstance(news, NewsData):
        return bool(news.titles)
    return bool(news)


# ── 시장 요약 ─────────────────────────────────────────────

_BASE_TIME_MAP = {"1차": "14:50", "2차": "17:50"}


def _short_sector_name(name: str) -> str:
    """섹터명 단축: '반도체와반도체장비' → '반도체'"""
    return name.split("와")[0] if "와" in name else name


def format_market_summary(market_totals: dict, run_time: str, run_type: str,
                          extra: dict | None = None,
                          leading_sectors: list | None = None,
                          pattern_counts: dict | None = None) -> str:
    parts     = run_time.split(" ", 1)
    date_str  = parts[0]
    time_str  = parts[1] if len(parts) > 1 else run_time
    base_time = _BASE_TIME_MAP.get(run_type, time_str)
    kospi_tv  = market_totals.get("kospi_total_tv_eok", 0)
    kosdaq_tv = market_totals.get("kosdaq_total_tv_eok", 0)

    ex               = extra or {}
    tv1500           = ex.get("tv_1500_count", 0)
    regime           = ex.get("market_regime", "")
    market_subtype   = ex.get("market_subtype", "")
    market_adl       = ex.get("market_adl")
    market_direction = ex.get("market_direction", "")
    limit_up_n       = ex.get("limit_up_count", 0)
    kospi_level      = ex.get("kospi_level")
    kosdaq_level     = ex.get("kosdaq_level")
    kospi_chg        = ex.get("kospi_chg")
    kosdaq_chg       = ex.get("kosdaq_chg")

    _regime_map = {"강세": "🟢 강세", "약세": "🔴 약세", "중립": "⚪ 중립"}
    regime_str  = _regime_map.get(regime, regime)
    adl_str     = f" (ADL {market_adl*100:.0f}%)" if market_adl is not None else ""
    subtype_str = f" · {market_subtype}" if market_subtype else ""

    def _idx(level, chg):
        if level is None:
            return "-"
        s = f"{level:,.2f}"
        if chg is not None:
            arrow = "▲" if chg >= 0 else "▼"
            s += f" {arrow}{abs(chg):.2f}%"
        return s

    # 패턴별 후보 갯수
    pc = pattern_counts or {}
    pat_parts = [
        f"{label} {pc[label]}개"
        for label in ["당일돌파형", "재돌파형", "고가수축형", "고가횡보형"]
        if pc.get(label, 0) > 0
    ]
    etc_n = pc.get("없음", 0)
    if etc_n > 0:
        pat_parts.append(f"기타 {etc_n}개")
    pat_line = ("  ".join(pat_parts) + "\n") if pat_parts else "후보 없음\n"

    limit_up_line = f"상한가 {limit_up_n}개\n" if limit_up_n > 0 else ""

    _direction_map = {
        "상승": "📈 상승",
        "하락": "📉 하락",
        "횡보": "➡ 횡보",
    }
    _timing_map = {
        "상승": "3시 즉시 진입 가능",
        "하락": "3시 30분 동시호가 후 신중 진입",
        "횡보": "3시 10분 이후 방향 확인 후 진입",
    }
    direction_str = _direction_map.get(market_direction, "")
    timing_str    = _timing_map.get(market_direction, "")

    if run_type == "1차" and direction_str:
        direction_line = f"[지수방향] {direction_str} → {timing_str}\n"
    else:
        direction_line = ""

    if run_type == "2차":
        checklist = "─" * 16 + "\n[원칙] NXT 거래여부 우선확인 / 추격금지 / 물타기금지\n"
    else:
        checklist = "─" * 16 + "\n[원칙] 종가진입 / 물타기금지 / D+1장초계획\n"

    # 지수 5일선·추세 국면 (백테스트 검증: 코스닥 국면이 종베 승률 결정)
    index_regime = ex.get("index_regime")
    regime_line = ""
    if index_regime:
        _kd = index_regime.get("kosdaq_regime", "?")
        _emoji = {"강세": "🟢", "혼조": "🟡", "약세": "🔴", "?": "⚪"}.get(_kd, "")
        _guide = index_regime.get("guide", "")
        regime_line = f"[코스닥국면] {_emoji} {_kd} — {_guide}\n"
        if index_regime.get("decoupled_largecap"):
            regime_line += "  ⚠ 코스피 강세·코스닥 약세 디커플링 → 추세 좋은 대형주 우위 국면\n"

    return (
        f"<b>[종가베팅 스캔] {date_str} · {base_time} KST</b>\n"
        f"코스피 {_idx(kospi_level, kospi_chg)} | 코스닥 {_idx(kosdaq_level, kosdaq_chg)}\n"
        f"거래대금: 코스피 {kospi_tv:,.0f}억 | 코스닥 {kosdaq_tv:,.0f}억\n"
        f"[시장] {regime_str}{adl_str}{subtype_str} | 1500억↑ {tv1500}개\n"
        f"{regime_line}"
        f"{direction_line}"
        f"{limit_up_line}"
        f"{pat_line}"
        f"{checklist}"
    )


# ── 섹터 섹션 (#3) ───────────────────────────────────────

def format_sector_section(leading_sectors: list) -> str:
    """주도 섹터 거래대금 요약"""
    if not leading_sectors:
        return ""
    lines = ["<b>[주도섹터]</b>"]
    for s in leading_sectors:
        name       = _short_sector_name(s.get("sector_name", ""))
        tv         = float(s.get("tv_eok", 0))
        avg_chg    = float(s.get("change_pct", 0))
        mkt_r      = s.get("market_ratio_pct")
        tv1500     = s.get("tv1500_count")
        gainer_n   = s.get("gainer_top20_count")
        tv20_n     = s.get("tv_top20_count")
        ratio      = f"{mkt_r:.1f}%" if mkt_r is not None else "-"
        chg_str    = f"+{avg_chg:.1f}%" if avg_chg >= 0 else f"{avg_chg:.1f}%"
        detail_parts = []
        if tv1500 is not None:
            detail_parts.append(f"1500억↑{tv1500}개")
        if gainer_n is not None:
            detail_parts.append(f"상승Top{gainer_n}개")
        if tv20_n is not None:
            detail_parts.append(f"대금Top{tv20_n}개")
        detail = f" [{' · '.join(detail_parts)}]" if detail_parts else ""
        lines.append(f"  {name} {_tv_eok(tv*1e8)} (시장{ratio}) {chg_str}{detail}")
    return "\n".join(lines) + "\n"


# ── 상한가 섹션 (#1) ─────────────────────────────────────

def format_limit_up_section(extra: dict, code_to_sector: dict = {}) -> str:
    limit_up_list  = extra.get("limit_up_list", [])
    limit_up_count = extra.get("limit_up_count", 0)
    if not limit_up_list or limit_up_count == 0:
        return ""
    lines = [f"<b>[상한가 {limit_up_count}개]</b>"]
    for r in limit_up_list:
        name   = r.get("종목명", "")
        code   = str(r.get("종목코드", ""))
        market = r.get("시장", "")
        chg    = float(r.get("등락률", 0))
        tv     = float(r.get("거래대금", 0))
        sector = code_to_sector.get(code, "")
        sec_s  = f"[{sector}] " if sector else ""
        lines.append(f"  {name}({code}) {sec_s}[{market}] {_sign(chg)} {_tv_eok(tv)}")
    return "\n".join(lines) + "\n"


# ── 상승률 Top20 ──────────────────────────────────────────

def format_top_gainers(df, enriched: dict = {}, inter_codes: set = set()) -> str:
    if df is None or df.empty:
        return "<b>[상승률 Top20]</b>\n데이터 없음\n"
    rows = [(i+1, row) for i, (_, row) in enumerate(df.iterrows())
            if str(row.get("종목코드", "")) not in inter_codes]
    if not rows:
        return ""
    lines = [f"<b>[상승률 Top{len(rows)}]</b>"]
    for rank, row in rows:
        tv = float(row.get("거래대금", 0))
        lines.append(
            f"  {rank}) {row['종목명']}({str(row.get('종목코드',''))}) [{row.get('시장','')}]"
            f" {_sign(float(row.get('등락률',0)))} | {_tv_eok(tv)}"
        )
    return "\n".join(lines) + "\n"


# ── 거래대금 Top20 ────────────────────────────────────────

def format_top_tv(df, enriched: dict = {}, inter_codes: set = set(), code_to_sector: dict = {}) -> str:
    if df is None or df.empty:
        return "<b>[거래대금 Top20]</b>\n데이터 없음\n"
    rows = [(i+1, row) for i, (_, row) in enumerate(df.iterrows())
            if str(row.get("종목코드", "")) not in inter_codes]
    if not rows:
        return ""
    lines = [f"<b>[거래대금 Top{len(rows)}]</b>"]
    for rank, row in rows:
        code   = str(row.get("종목코드", ""))
        tv     = float(row.get("거래대금", 0))
        sector = code_to_sector.get(code, "")
        sec_s  = f"[{sector}] " if sector else ""
        lines.append(
            f"  {rank}) {row['종목명']}({code}) {sec_s}[{row.get('시장','')}]"
            f" {_tv_eok(tv)} | {_sign(float(row.get('등락률',0)))}"
        )
    return "\n".join(lines) + "\n"


# ── 교집합 후보 ───────────────────────────────────────────

def format_intersection(df, enriched: dict = {}, code_to_sector: dict = {}) -> str:
    if df is None or df.empty:
        return ""
    lines = ["<b>[★ 교집합]</b>"]
    for i, (_, row) in enumerate(df.iterrows()):
        code   = str(row.get("종목코드", ""))
        tv     = float(row.get("거래대금", 0))
        sector = code_to_sector.get(code, "")
        sec_s  = f" [{sector}]" if sector else ""
        lines.append(
            f"  {i+1}) <b>{row['종목명']}</b>{sec_s}"
            f" {_sign(float(row.get('등락률',0)))} {_tv_eok(tv)}"
        )
    return "\n".join(lines) + "\n"


# ── 핵심 후보 상세 ────────────────────────────────────────

_OFFSET_LABEL = {0: "당일", 1: "1일전", 2: "2일전", 3: "3일전"}
_PATTERN_TYPE_ORDER = ["당일돌파형", "재돌파형", "고가수축형", "고가횡보형", "없음"]


def _position_guide(c: dict) -> str:
    """손절남 기준 비중 가이드 한 줄."""
    chg   = float(c.get("change_pct", 0))
    _sc   = c.get("score")
    score = int(_sc.total_score) if _sc and hasattr(_sc, "total_score") else int(c.get("total_score") or 0)
    inter = c.get("in_inter", False)
    if chg >= 25:
        return "\n  💼 비중: ⚠ 축소 권고 (급등25%↑ · 승률 50%)"
    if score >= 13 or (score >= 10 and inter):
        return "\n  💼 비중: 강한 후보 (30~50%)"
    if score >= 10:
        return "\n  💼 비중: 일반 후보 (20~30%)"
    if score >= 7:
        return "\n  💼 비중: 소액 테스트 (10~20%)"
    return ""


def _format_candidate_card(seq: int, c: dict) -> str:
    """단일 종목 카드 — 실전 매매용"""
    pat  = c.get("patterns", {})
    sup  = c.get("supply", {})
    news = c.get("news", [])
    cl   = c.get("checklist")
    tv   = float(c.get("trading_value", 0))

    # ── Line 1: 종목명 + 핵심 태그 ──────────────────────────
    in_inter    = c.get("in_inter", False)
    new_high    = pat.get("new_high_60d", False)
    near_high   = pat.get("near_high_60d", False)
    near_h52w   = c.get("near_high_52w", False)
    ma_aligned  = c.get("indicators", {}).get("ma_aligned", False)
    consol_flag = pat.get("consolidation_flag", False)
    pbs_flag    = pat.get("pullback_support_flag", False)

    # 단발성 감지: LLM이 (단순수급)으로 분류한 경우
    llm_text    = getattr(news, "llm_summary", None) or ""
    is_danbal   = "(단순수급)" in llm_text

    htc_flag    = pat.get("high_tight_consolidation_flag", False)
    htc_reignite = pat.get("high_tight_reignite_flag", False)

    is_nxt = c.get("is_nxt", False)

    tags = []
    if in_inter:    tags.append("★교집합")
    if new_high:    tags.append("🔺신고가")
    elif near_high: tags.append("📍고점권")
    if near_h52w:   tags.append("📈52w")
    if ma_aligned:  tags.append("📶정배열")
    if tv >= 1_000_000_000_000: tags.append("💰1조+")
    if consol_flag: tags.append("📊기간조정")
    if pbs_flag:    tags.append("↩되돌림지지(±5%)")
    if htc_flag:    tags.append("🔶고가수축" + ("⚡" if htc_reignite else ""))
    if is_danbal:   tags.append("⚡단발")
    prog_net = c.get("prog_net_eok")
    if prog_net is not None and prog_net > 0: tags.append("💹프로그램매수")
    # 재료 신선도 (과거 signals 등장 횟수 근사)
    from config.settings import FRESHNESS_STALE_MIN_COUNT
    _fresh = c.get("freshness_count")
    if _fresh == 0:
        tags.append("🆕신규등장")
    elif _fresh is not None and _fresh >= FRESHNESS_STALE_MIN_COUNT:
        tags.append(f"♻️{_fresh}일째")
    if is_nxt:
        if c.get("nxt_dominant"):
            tags.append("🔵NXT대장")
        else:
            tags.append("🔵NXT")
    elif c.get("nxt_fetch_ran"):
        tags.append("⚠KRX전용(15:20전)")

    # ── 리스크 태그 ────────────────────────────────────────
    change_pct_val = float(c.get("change_pct", 0))
    _sc2           = c.get("score")
    score_val      = int(_sc2.total_score) if _sc2 and hasattr(_sc2, "total_score") else int(c.get("total_score") or 0)
    if change_pct_val >= 25:
        tags.append("⚠급등25%↑")
    if 0 < score_val <= 9:
        tags.append("⚠저스코어")
    if 0 < tv < 250_000_000_000:
        tags.append("⚠대금근접")

    tag_str = "  " + "  ".join(tags) if tags else ""

    # ── Line 2: 등락률 / 거래대금 / 섹터 / 패턴 ──────────────
    pattern_label  = pat.get("pattern_type_label", "없음")
    offset_str     = _OFFSET_LABEL.get(pat.get("base_candle_day_offset"), "-")
    if pattern_label != "없음":
        pattern_str = f"{pattern_label}({offset_str})"
    elif (pat.get("today_close_from_high_pct") or 0) <= -5.0:
        pattern_str = "5%↑윗꼬리"
    else:
        pattern_str = "패턴없음"
    sector         = c.get("sector", "")
    is_leading     = c.get("is_leading_sector", False)
    theme_role     = c.get("theme_role", "")
    if sector and is_leading:
        role_badge = f"·{theme_role}" if theme_role else ""
        sector_str = f"[{sector}{role_badge}✓] | "
    else:
        sector_str = f"[{sector}] | " if sector else ""

    # ── Line 3: 재료 (LLM summary) ─────────────────────────
    llm_line = f"\n  {llm_text}" if llm_text else ""

    # ── Line 3-1: 고가수축형 상세 ────────────────────────────
    htc_line = ""
    if htc_flag:
        avg_r  = pat.get("high_tight_tv_ratio_avg")
        chg_h  = pat.get("high_tight_close_from_base_high_pct")
        parts  = []
        if avg_r  is not None: parts.append(f"대금수축 {avg_r*100:.0f}%")
        if chg_h  is not None: parts.append(f"고가대비 {chg_h:+.1f}%")
        if htc_reignite:       parts.append("재점화 조짐")
        if parts: htc_line = f"\n  고가수축: {' | '.join(parts)}"

    # ── Line 4: 수급 ──────────────────────────────────────
    supply_str  = _supply_str(sup)
    supply_line = ""
    if supply_str != "확인불가":
        _sup_obj   = sup if hasattr(sup, "institution_consecutive_days") else None
        _consec_parts = []
        if _sup_obj:
            ic = _sup_obj.institution_consecutive_days or 0
            fc = _sup_obj.foreign_consecutive_days     or 0
            if ic >= 2:
                _consec_parts.append(f"기관{ic}일연속")
            if fc >= 2:
                _consec_parts.append(f"외인{fc}일연속")
        _consec_str = f" ({', '.join(_consec_parts)})" if _consec_parts else ""
        # 오버수급 비율 (상장주식수 대비 5일 누적)
        from config.settings import OVERSUPPLY_RATIO_PCT
        _over_parts = []
        _inst_ov = c.get("inst_oversupply_pct")
        _frgn_ov = c.get("frgn_oversupply_pct")
        if _inst_ov is not None and _inst_ov >= OVERSUPPLY_RATIO_PCT:
            _over_parts.append(f"기관 {_inst_ov:.1f}%")
        if _frgn_ov is not None and _frgn_ov >= OVERSUPPLY_RATIO_PCT:
            _over_parts.append(f"외인 {_frgn_ov:.1f}%")
        _over_str = f"\n  오버수급(상장주식수 대비 5일): {', '.join(_over_parts)} 🔥" if _over_parts else ""
        supply_line = f"\n  수급: {supply_str}{_consec_str}{_over_str}"

    # ── Line 5: 체크리스트 ────────────────────────────────
    checklist_line = ""
    if cl is not None:
        def _c(flag, label): return f"{label}✓" if flag else f"{label}✗"
        n = cl.required_pass_count
        checklist_line = (
            f"\n  체크({n}/4): "
            f"{_c(cl.big_candle_ok,'大')} "
            f"{_c(cl.first_big_candle_ok,'첫봉')} "
            f"{_c(cl.ma_cluster_ok,'MA')} "
            f"{_c(cl.trading_value_ok,'대금')}"
            f" | {_c(cl.volume_peak_ok,'Peak')} {_c(cl.supply_ok,'수급')}"
        )

    # ── Line 6: DART 공시 ──────────────────────────────────
    dart_notices = c.get("dart_notices", [])
    dart_line = ""
    if dart_notices:
        dart_line = "\n  " + "\n  ".join(dart_notices[:3])
    elif c.get("dart_notices") is not None:
        dart_line = "\n  📋 공시: 없음"

    # ── Line 7: 공매도 잔고 ────────────────────────────────
    short_ratio = c.get("short_ratio")
    short_line  = ""
    if short_ratio is not None:
        flag = " ⚠" if short_ratio >= 5 else ""
        short_line = f"\n  공매도잔고(T+2): {short_ratio:.2f}%{flag}"

    # ── Line 8: 연기금 순매수 ──────────────────────────────
    pension_net  = c.get("pension_net")
    pension_line = ""
    if pension_net is not None:
        eok  = pension_net / 1e8
        flag = " 🟢" if eok > 0 else " 🔴"
        pension_line = f"\n  연기금(T-1): {eok:+.0f}억{flag}"

    position_line = _position_guide(c)

    # ── 청산 참고선 (전일고가·전일종가·당일시가) ─────────────────
    exit_line = ""
    _ph  = c.get("prev_high")
    _pc  = c.get("prev_close")
    _top = c.get("today_open_price")
    _parts = []
    if _ph:  _parts.append(f"전일고가 {_ph:,.0f}")
    if _pc:  _parts.append(f"전일종가 {_pc:,.0f}")
    if _top: _parts.append(f"당일시가 {_top:,.0f}")
    if _parts:
        exit_line = "\n  청산참고: " + " | ".join(_parts)

    return (
        f"\n{seq}) <b>{c.get('name','')}({c.get('code','')})</b>"
        f" [{c.get('market','')}]{tag_str}\n"
        f"  {_sign(float(c.get('change_pct', 0)))} | {_tv_eok(tv)} | {sector_str}{pattern_str}"
        f"{llm_line}"
        f"{htc_line}"
        f"{supply_line}"
        f"{exit_line}"
        f"{checklist_line}"
        f"{dart_line}"
        f"{short_line}"
        f"{pension_line}"
        f"{position_line}"
    )


def format_limit_up_followup(followup_data: list) -> str:
    """상한가 리더 기반 테마 후속 후보 섹션."""
    if not followup_data:
        return ""
    lines = ["<b>[테마 후속 후보]</b>"]
    for item in followup_data:
        leader = f"{item['leader_name']}({item['leader_code']})"
        sector = item.get("sector", "")
        lines.append(f"  ▶ 리더: {leader} 상한가 [{sector}]")
        for f in item["followups"][:3]:
            chg = float(f.get("등락률", 0))
            tv  = float(f.get("거래대금", 0))
            lines.append(
                f"    └ {f.get('종목명','')}({f.get('종목코드','')}) {_sign(chg)} {_tv_eok(tv)}"
            )
    return "\n".join(lines) + "\n"


def format_watch_candidates(candidates: list[dict]) -> str:
    """관심 후보 — 1줄 요약 (장세 상한 초과 종목)."""
    if not candidates:
        return ""
    lines = [f"<b>[관심 후보 {len(candidates)}개]</b>"]
    for c in candidates:
        tv      = c.get("trading_value", 0)
        pat     = c.get("patterns", {}).get("pattern_type_label", "없음")
        pct     = float(c.get("change_pct", 0))
        sign    = "+" if pct >= 0 else ""
        sector  = c.get("sector", "")
        is_lead = c.get("is_leading_sector", False)
        role    = c.get("theme_role", "")
        if sector and is_lead and role:
            sec_s = f"[{sector}·{role}✓] "
        elif sector and is_lead:
            sec_s = f"[{sector}✓] "
        else:
            sec_s = f"[{sector}] " if sector else ""
        nxt_tag = " 🔵NXT" if c.get("is_nxt") else ""
        lines.append(
            f"  • {c['name']}({c['code']}) "
            f"{sign}{pct:.1f}% | {tv/100_000_000:.0f}억 | {sec_s}{pat}{nxt_tag}"
        )
    return "\n".join(lines) + "\n"


def format_key_candidates(candidates: list[dict]) -> str:
    """
    핵심 후보를 패턴 타입별로 그룹화하여 출력.
    candidates: [
      {name, code, market, change_pct, trading_value(원),
       indicators, patterns, supply, news, in_inter}
    ]
    """
    if not candidates:
        return "<b>[핵심 후보]</b>\n없음\n"

    # 패턴 타입별 그룹화
    groups: dict[str, list] = {t: [] for t in _PATTERN_TYPE_ORDER}
    for c in candidates:
        label = c.get("patterns", {}).get("pattern_type_label", "없음")
        groups.setdefault(label, []).append(c)

    lines = [f"<b>[핵심 후보 {len(candidates)}개]</b>"]
    seq = 1

    section_titles = {
        "당일돌파형": "▶ 당일 돌파형",
        "재돌파형":   "▶ 재돌파형 (구조붕괴 후 전고점 복귀 + 양매수 · 단기 청산 권장)",
        "고가수축형": "▶ 고가수축형 (거래대금 수축 대기)",
        "고가횡보형": "▶ 1~3일전 기준봉 후 고가횡보형",
        "없음":       "▶ 기타 (교집합)",
    }

    for label in _PATTERN_TYPE_ORDER:
        group = groups.get(label, [])
        if not group:
            continue
        lines.append(f"\n<b>{section_titles[label]}</b>")
        for c in group:
            lines.append(_format_candidate_card(seq, c))
            seq += 1

    return "\n".join(lines) + "\n"


# ── 대시보드 링크 ─────────────────────────────────────────

def _format_dashboard_links(links: dict) -> str:
    """대시보드 링크 포맷. dated_url 우선 (CDN 캐시 회피), 없으면 latest_url."""
    if not links:
        return ""
    url = links.get("dated_url") or links.get("latest_url", "")
    if not url:
        return ""
    return f"🔗 대시보드: {url}\n"


# ── 1차 / 2차 알림 조합 ───────────────────────────────────

def _count_by_pattern(candidates: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in candidates:
        label = c.get("patterns", {}).get("pattern_type_label", "없음")
        counts[label] = counts.get(label, 0) + 1
    return counts


def build_first_alert(
    market_totals: dict,
    gainers,
    top_tv,
    intersection,
    key_candidates: list = [],
    run_time: str = "",
    enriched: dict = {},
    dashboard_links: dict = {},
    market_summary_extra: dict | None = None,
    leading_sectors: list | None = None,
    watch_candidates: list = [],
    followup_data: list | None = None,
) -> str:
    ex = market_summary_extra or {}
    pattern_counts = _count_by_pattern(list(key_candidates) + list(watch_candidates))
    parts = [
        format_market_summary(market_totals, run_time, "1차", extra=ex,
                              leading_sectors=leading_sectors,
                              pattern_counts=pattern_counts),
        format_limit_up_followup(followup_data or []),
    ]
    link_str = _format_dashboard_links(dashboard_links)
    if link_str:
        parts.append(link_str)
    return "\n".join(p for p in parts if p)


def build_second_alert(
    market_totals: dict,
    gainers,
    top_tv,
    intersection,
    key_candidates: list,
    run_time: str,
    enriched: dict = {},
    dashboard_links: dict = {},
    market_summary_extra: dict | None = None,
    leading_sectors: list | None = None,
    watch_candidates: list = [],
    run_type: str = "2차",
    followup_data: list | None = None,
) -> str:
    ex = market_summary_extra or {}
    pattern_counts = _count_by_pattern(list(key_candidates) + list(watch_candidates))
    parts = [
        format_market_summary(market_totals, run_time, run_type, extra=ex,
                              leading_sectors=leading_sectors,
                              pattern_counts=pattern_counts),
        format_limit_up_followup(followup_data or []),
    ]
    link_str = _format_dashboard_links(dashboard_links)
    if link_str:
        parts.append(link_str)
    return "\n".join(p for p in parts if p)
