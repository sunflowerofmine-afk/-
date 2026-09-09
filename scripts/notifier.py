# scripts/notifier.py
"""텔레그램 메시지 포맷 및 전송 모듈"""

import sys
import logging
import datetime as _dt
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_DEV,
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
    preview 모드: TELEGRAM_CHAT_ID_DEV 단독 발송. 일반: TELEGRAM_CHAT_ID 단독."""
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
        chat_ids = [TELEGRAM_CHAT_ID]
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


def _gate_short(why: str) -> str:
    """게이트 사유 축약 — '조건 — 권고' 구조에서 권고 절을 뺀다.

    권고("평소보다 작게" 등)는 등급 이름에 이미 들어 있어 모바일에서 같은 말을
    두 줄 더 읽게 만든다. 단 ' — ' 뒤에 ' · '로 덧붙는 강등 사유(매수검토 0건,
    미선물 비우호)는 등급만으로 알 수 없으므로 살린다.
    """
    head, sep, tail = why.partition(" — ")
    if not sep:
        return why
    return " · ".join([head, *tail.split(" · ")[1:]])


def _short_sector_name(name: str) -> str:
    """섹터명 단축: '반도체와반도체장비' → '반도체'"""
    return name.split("와")[0] if "와" in name else name


def format_market_summary(market_totals: dict, run_time: str, run_type: str,
                          extra: dict | None = None,
                          leading_sectors: list | None = None,
                          pattern_counts: dict | None = None,
                          core_candidates: list | None = None) -> str:
    parts     = run_time.split(" ", 1)
    date_str  = parts[0]
    time_str  = parts[1] if len(parts) > 1 else run_time
    # 실제 실행 시각을 그대로 쓴다. run_type별 고정 표기는 2차가 15:35·17:50·19:30
    # 세 번 돌게 된 2026-09-07 이후로 전부 '17:50'으로 잘못 찍힌다.
    base_time = time_str
    # 1차는 마감 전이라 종가·거래대금이 확정 전이다. 성과 집계에서도 빠진다.
    if run_type == "1차":
        base_time += " (마감 전 잠정)"
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

    # '폭'(오른 종목 비율) 지표 — 지수 추세 국면과 다른 축이라 강세/약세 단어를 쓰지 않는다.
    # 같은 메시지의 '오늘 판정'(코스닥 추세)과 단어가 충돌해 하락장을 강세로 오독할 위험이 있음.
    # 비율을 숫자로 보여주므로 '우세/열세' 단어는 색만 남기고 뺀다(같은 말 두 번).
    _breadth_map = {"강세": ("🟢", "우세"), "약세": ("🔴", "열세"), "중립": ("⚪", "보통")}
    _b_emoji, _b_word = _breadth_map.get(regime, ("⚪", "보통"))
    breadth_str = (f"{_b_emoji}오른종목 {market_adl*100:.0f}%"
                   if market_adl is not None else f"{_b_emoji}오른종목 {_b_word}")
    subtype_str = f" · {market_subtype}" if market_subtype else ""

    # 날짜에 요일 부착 (한눈에 보기)
    _WD = ["월", "화", "수", "목", "금", "토", "일"]
    try:
        date_disp = f"{date_str}({_WD[_dt.date.fromisoformat(date_str).weekday()]})"
    except Exception:
        date_disp = date_str

    # 거래대금: 억 → 조 환산 (한눈에)
    def _tv_jo(eok):
        try:
            return f"{eok/10000:,.1f}조"
        except Exception:
            return "-"

    # 패턴별 후보 갯수 (라벨 단축)
    pc = pattern_counts or {}
    _pat_short = {"당일돌파형": "당일돌파", "재돌파형": "재돌파",
                  "고가수축형": "고가수축", "고가횡보형": "고가횡보"}
    pat_parts = [
        f"{_pat_short[label]} {pc[label]}"
        for label in ["당일돌파형", "재돌파형", "고가수축형", "고가횡보형"]
        if pc.get(label, 0) > 0
    ]
    etc_n = pc.get("없음", 0)
    if etc_n > 0:
        pat_parts.append(f"기타 {etc_n}")
    cand_str = " · ".join(pat_parts) if pat_parts else "없음"

    # 상한가 — 돌팬티 6.1 점검 4단계는 "상한가와 그 뉴스는 무엇인가"다.
    # 건수만으로는 MTS에서 뭘 찾아봐야 할지 알 수 없어 종목명을 함께 낸다.
    _lu_names = (ex.get("limit_up_names") or [])[:4]
    if limit_up_n > 0:
        _lu_tail = f" — {' · '.join(_lu_names)}" if _lu_names else ""
        limit_up_line = f"상한가 {limit_up_n}{_lu_tail}\n"
    else:
        limit_up_line = ""

    # 주도섹터 — 점검 5단계(테마). 지금까지 대시보드에만 있고 알림에 없었다.
    _sec_bits = []
    for s in (leading_sectors or [])[:3]:
        _r = s.get("market_ratio_pct")
        # 괄호 부연("S7(삼성전자/SK하이닉스 등)")은 한 줄을 넘겨서 뺀다.
        _nm = _short_sector_name(s.get("sector_name", "")).split("(")[0].strip()
        _sec_bits.append(_nm + (f" {_r:.0f}%" if _r is not None else ""))
    sector_line = ("주도 " + " · ".join(_sec_bits) + "\n") if _sec_bits else ""

    # 핵심 후보 종목명 — 대시보드를 열지 않고도 무엇이 걸렸는지 보이게 한다.
    _cand_lines = []
    for i, c in enumerate((core_candidates or [])[:5]):
        _nxt = " ⚡" if c.get("nxt_dominant") else ""
        _cand_lines.append(
            f"  {i+1} {c.get('name','')} {_sign(float(c.get('change_pct', 0)))}"
            f" {_tv_eok(float(c.get('trading_value', 0)))}{_nxt}"
        )
    _rest = len(core_candidates or []) - 5
    if _rest > 0:
        _cand_lines.append(f"  … 외 {_rest}건")
    cand_names = ("\n".join(_cand_lines) + "\n") if _cand_lines else ""

    _KD_PLAIN = {"강세": "강세", "혼조": "혼조", "약세": "약세"}

    # ── 오늘 판정 (게이트) — 대시보드와 동일 산출 (compute_daily_gate) ──
    from scripts._dashboard_sections import compute_daily_gate
    _ir = ex.get("index_regime") or {}
    _kd_gate = _ir.get("kosdaq_regime")
    if _kd_gate is None:
        _kd_gate = {"강세": "강세", "약세": "약세", "중립": "혼조"}.get(regime, None)
    _grade, _, _why = compute_daily_gate(
        ex.get("core_count", 0), _kd_gate, market_adl,
        ex.get("top5_concentration_pct"), ex.get("risk_appetite"),
        ex.get("buy_review_count"),
    )
    _gate_emoji = {"매매 금지": "🔴", "관찰만": "🟠",
                   "소액만": "🟡", "종가베팅 허용": "🟢"}.get(_grade, "⚪")

    # 대형주 트랙은 개별주와 독립 — 개별주 금지가 대형주 금지를 뜻하지 않는다.
    from scripts._dashboard_sections import compute_largecap_gate
    _lc_grade, _, _lc_why = compute_largecap_gate(
        ex.get("largecap_count", 0), ex.get("twotop_count", 0),
        # 파이프라인이 정한 사실을 받는다. 없으면 옛 방식으로 되짚되, 1차는 본 알림
        # 뒤에 관찰이 도니 이 시점엔 안 돌았다는 판정이 맞다.
        bool(ex.get("largecap_ran", run_type in ("2차", "수동"))),
        deferred=bool(ex.get("largecap_deferred")),
    )
    # 실제로 볼 것이 있을 때만 파란불. 미집계·자리없음은 무채색.
    _lc_emoji = "🔵" if _lc_grade in ("과매도 반등 관찰", "추세 관찰") else "⚫"

    # 대시보드가 안 만들어진 날은 알림에 띄운다. 예전엔 로그에만 남아서
    # 9/3 17:50과 9/8 15:35 두 번이 죽은 것을 닷새 뒤에야 알았다.
    _dash_line = ("" if ex.get("dashboard_ok", True)
                  else "⚠️ <b>대시보드 생성 실패 — 오늘은 이 알림만 보고 판단할 것</b>\n")

    # ── 지수방향 (1차만) ──────────────────────────────────────
    _direction_map = {"상승": "📈 상승", "하락": "📉 하락", "횡보": "➡ 횡보"}
    _timing_map = {
        "상승": "3시 즉시 진입 가능",
        "하락": "3시 30분 동시호가 후 신중 진입",
        "횡보": "3시 10분 이후 방향 확인 후 진입",
    }
    direction_str = _direction_map.get(market_direction, "")
    timing_str    = _timing_map.get(market_direction, "")
    if run_type == "1차" and direction_str:
        direction_line = f"방향 {direction_str} → {timing_str}\n"
    else:
        direction_line = ""

    # ── 국면 (코스피·코스닥 독립 판정 — 서로 비교 아님) ────────
    # 별도 줄로 빼지 않고 각 시장 줄 끝에 붙인다. 지수·자금·국면을 시장별로 모아야
    # 한 줄만 읽고 그 시장을 판단할 수 있다.
    index_regime = ex.get("index_regime") or {}
    _emoji_map = {"강세": "🟢", "혼조": "🟡", "약세": "🔴", "?": "⚪"}

    def _regime_chip(key):
        r = index_regime.get(key)
        if not r:
            return ""
        return f" · {_emoji_map.get(r,'')}{_KD_PLAIN.get(r, r)}"

    def _mkt_line(label, level, chg, tv_eok, regime_key):
        s = f"{label} "
        s += f"{level:,.0f}" if level is not None else "-"
        if chg is not None:
            s += f" {'▲' if chg >= 0 else '▼'}{abs(chg):.2f}%"
        return s + f" · {_tv_jo(tv_eok)}{_regime_chip(regime_key)}\n"

    # ── 거시 (환율·WTI·미선물 — 돌팬티 루틴: 미선물·유가·환율 확인) ──
    macro = ex.get("macro") or {}
    macro_bits = []
    if macro.get("usdkrw") is not None:
        _uc = macro.get("usdkrw_chg")
        _s = f"환율 {macro['usdkrw']:,.0f}"
        if _uc is not None:
            _s += f"({'▲' if _uc >= 0 else '▼'}{abs(_uc):.1f})"
        macro_bits.append(_s)
    if macro.get("wti") is not None:
        _wc = macro.get("wti_chg")
        _s = f"WTI {macro['wti']:.1f}"
        if _wc is not None:
            _s += f"({'▲' if _wc >= 0 else '▼'}{abs(_wc):.1f})"
        macro_bits.append(_s)
    _risk = ex.get("risk_appetite")
    if _risk:
        macro_bits.append(f"미선물 {_risk}")
    macro_line = ("거시 " + " · ".join(macro_bits) + "\n") if macro_bits else ""

    # ── 원칙 한 줄 (실행 리마인드) ────────────────────────────
    # 1차만 마감 전이다. 2차·수동은 전부 장 마감 뒤(15:35 이후) 실행이라
    # "종가 진입 준비"가 맞지 않는다 — 19:30 실행이 "수동"으로 떨어지면서
    # 이 문구가 나오던 것을 2026-09-08에 고쳤다.
    if run_type == "1차":
        principle = "💡 종가 진입 준비 · 비중을 먼저 정할 것 · D+1 청산계획"
    else:
        principle = "💡 진입은 NXT 막판 · 청산은 D+1 오전 · 물타기 금지"

    _bar = "━" * 15
    return (
        f"<b>{_bar}</b>\n"
        f"<b>📊 종가베팅 · {date_disp} · {base_time}</b>\n"
        f"<b>{_bar}</b>\n"
        f"{_gate_emoji} <b>개별주 종베: {_grade}</b>\n"
        f"    {_gate_short(_why)}\n"
        f"{_lc_emoji} <b>대형주 트랙: {_lc_grade}</b>\n"
        f"    {_lc_why}\n"
        f"{_dash_line}\n"
        f"{_mkt_line('코스피', kospi_level, kospi_chg, kospi_tv, 'kospi_regime')}"
        f"{_mkt_line('코스닥', kosdaq_level, kosdaq_chg, kosdaq_tv, 'kosdaq_regime')}"
        f"폭 {breadth_str}{subtype_str} · 1500억↑ {tv1500}\n"
        f"{sector_line}"
        f"{limit_up_line}"
        f"{macro_line}"
        f"{direction_line}\n"
        f"<b>후보 {cand_str}</b>\n"
        f"{cand_names}"
        f"{'─' * 16}\n"
        f"{principle}\n"
    )


# ── 대형주 주도주 후속 알림 ─────────────────────────────────

def build_largecap_message(largecap_candidates: list, run_time: str, run_type: str) -> str:
    """대형주 주도주 관찰 후속 메시지 (본 알림 뒤 별도 발송, 1차/2차 공통).

    2026-06-30 백테스트: 신고가근접 67% / 거래대금+외인기관 동시매수 66% (D+1 시가).
    1차는 본 알림 발송 후 실행해 알림 타이밍을 보호(KRX 15시 전후 진입 정보).
    """
    if not largecap_candidates:
        return ""
    lines = [f"🏛 <b>대형주 주도주 관찰</b> — {run_type} ({run_time} KST)",
             "기준: 양봉+거래대금 3천억↑ 이고 (신고가 근접 또는 외인+기관 동시순매수)"]
    if run_type == "1차":
        lines.append("⚠ 1차는 장중 잠정치 기준 (종가 확정 아님)")
    lines.append("")
    for c in largecap_candidates[:5]:
        tv = float(c.get("trading_value", 0) or 0)
        tv_str = f"{tv/1e12:.1f}조" if tv >= 1e12 else f"{tv/1e8:,.0f}억"
        nh = c.get("near_high_pct")
        nh_str = f" · 신고가까지 {nh:+.1f}%" if nh is not None and nh > -900 else ""
        dual = " · 🔥외인+기관" if c.get("dual_buy") else ""
        lines.append(
            f"· <b>{c.get('name','')}</b>({c.get('code','')}) "
            f"{c.get('change_pct', 0):+.2f}% · {tv_str}{nh_str}{dual}"
        )
    if len(largecap_candidates) > 5:
        lines.append(f"…외 {len(largecap_candidates) - 5}개 (대시보드 참조)")
    lines.append("─" * 16)
    lines.append("진입 정석: KRX 15시 전후 일부 + NXT 막판(19:50 이후) 나머지")
    lines.append("검증: D+1 시가 매도 승률 66% 이상 · 관찰 정보 (매수신호 아님)")
    return "\n".join(lines)


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
                              pattern_counts=pattern_counts,
                              core_candidates=list(key_candidates)),
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
                              pattern_counts=pattern_counts,
                              core_candidates=list(key_candidates)),
        format_limit_up_followup(followup_data or []),
    ]
    link_str = _format_dashboard_links(dashboard_links)
    if link_str:
        parts.append(link_str)
    return "\n".join(p for p in parts if p)
