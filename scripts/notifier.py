# scripts/notifier.py
"""텔레그램 메시지 포맷 및 전송 모듈"""

import sys
import logging
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from scripts.models import SupplyData, NewsData

logger = logging.getLogger(__name__)

MAX_MSG_LEN = 4096


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
    """텔레그램으로 메시지 전송. 4096자 초과 시 자동 분할."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN 미설정")
        return False
    if not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID 미설정")
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
                logger.error(f"텔레그램 전송 실패: {resp.status_code} {resp.text[:200]}")
                success = False
        except Exception as e:
            logger.error(f"텔레그램 전송 예외: {e}")
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
    """SupplyData 객체 또는 dict 모두 처리"""
    if supply is None:
        return "확인불가"
    if isinstance(supply, SupplyData):
        if supply.status == "failed":
            return "확인불가"
        inst = supply.institution_net
        frgn = supply.foreign_net
    else:
        if supply.get("status") == "failed":
            return "확인불가"
        inst = supply.get("institution_net")
        frgn = supply.get("foreign_net")
    inst_s = f"기관 {inst/100_000_000:+.0f}억" if inst is not None else "기관 -"
    frgn_s = f"외국인 {frgn/100_000_000:+.0f}억" if frgn is not None else "외국인 -"
    return f"{inst_s} / {frgn_s}"


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


def format_market_summary(market_totals: dict, run_time: str, run_type: str) -> str:
    # run_time 형식: "YYYY-MM-DD HH:MM"
    parts = run_time.split(" ", 1)
    date_str = parts[0]
    time_str = parts[1] if len(parts) > 1 else run_time
    base_time = _BASE_TIME_MAP.get(run_type, time_str)
    kospi  = market_totals.get("kospi_total_tv_eok", 0)
    kosdaq = market_totals.get("kosdaq_total_tv_eok", 0)
    return (
        f"<b>[종가베팅 스캔 - {date_str} / 기준시각 {base_time} / 실행시각 {time_str}]</b>\n\n"
        f"<b>[시장 요약]</b>\n"
        f"코스피 거래대금: {kospi:,.0f}억\n"
        f"코스닥 거래대금: {kosdaq:,.0f}억\n"
    )


# ── 상승률 Top20 ──────────────────────────────────────────

def format_top_gainers(df, enriched: dict = {}) -> str:
    if df is None or df.empty:
        return "<b>[상승률 Top20]</b>\n데이터 없음\n"
    lines = ["<b>[상승률 Top20]</b>"]
    for i, row in df.iterrows():
        code  = str(row.get("종목코드", ""))
        enr   = enriched.get(code, {})
        ind   = enr.get("indicators", {})
        news  = enr.get("news", [])
        tv    = float(row.get("거래대금", 0))
        lines.append(
            f"{i+1}) {row['종목명']}({code}) [{row.get('시장','')}] "
            f"{_sign(float(row.get('등락률',0)))} | {_tv_eok(tv)} | "
            f"거래량60최고:{_yn(ind.get('vol_peak'))} | "
            f"거래대금60최고:{_yn(ind.get('tv_peak'))} | "
            f"뉴스:{'O' if _has_news(news) else 'X'}"
        )
    return "\n".join(lines) + "\n"


# ── 거래대금 Top20 ────────────────────────────────────────

def format_top_tv(df, enriched: dict = {}) -> str:
    if df is None or df.empty:
        return "<b>[거래대금 Top20]</b>\n데이터 없음\n"
    lines = ["<b>[거래대금 Top20]</b>"]
    for i, row in df.iterrows():
        code  = str(row.get("종목코드", ""))
        enr   = enriched.get(code, {})
        ind   = enr.get("indicators", {})
        news  = enr.get("news", [])
        tv    = float(row.get("거래대금", 0))
        lines.append(
            f"{i+1}) {row['종목명']}({code}) [{row.get('시장','')}] "
            f"{_tv_eok(tv)} | {_sign(float(row.get('등락률',0)))} | "
            f"거래량60최고:{_yn(ind.get('vol_peak'))} | "
            f"거래대금60최고:{_yn(ind.get('tv_peak'))} | "
            f"뉴스:{'O' if _has_news(news) else 'X'}"
        )
    return "\n".join(lines) + "\n"


# ── 교집합 후보 ───────────────────────────────────────────

def format_intersection(df, enriched: dict = {}) -> str:
    if df is None or df.empty:
        return "<b>[상승률 Top20 ∩ 거래대금 Top20]</b>\n교집합 없음\n"
    lines = ["<b>[상승률 Top20 ∩ 거래대금 Top20]</b>"]
    for i, row in df.iterrows():
        code  = str(row.get("종목코드", ""))
        enr   = enriched.get(code, {})
        pat   = enr.get("patterns", {})
        sup   = enr.get("supply", {})
        news  = enr.get("news", [])
        tv    = float(row.get("거래대금", 0))
        lines.append(
            f"{i+1}) {row['종목명']}({code}) | "
            f"{_sign(float(row.get('등락률',0)))} | {_tv_eok(tv)} | "
            f"패턴:{pat.get('pattern_summary','?')} | "
            f"수급:{_supply_str(sup)} | 뉴스:{'O' if news else 'X'}"
        )
    return "\n".join(lines) + "\n"


# ── 핵심 후보 상세 ────────────────────────────────────────

_OFFSET_LABEL = {0: "당일", 1: "1일전", 2: "2일전", 3: "3일전"}
_PATTERN_TYPE_ORDER = ["당일돌파형", "고가횡보형", "눌림관찰형", "없음"]


def _format_candidate_card(seq: int, c: dict) -> str:
    """단일 종목 카드 포맷"""
    ind  = c.get("indicators", {})
    pat  = c.get("patterns", {})
    sup  = c.get("supply", {})
    news = c.get("news", [])
    tv   = float(c.get("trading_value", 0))

    offset      = pat.get("base_candle_day_offset")
    offset_str  = _OFFSET_LABEL.get(offset, "-")
    gap_pct     = pat.get("base_high_gap_pct")
    gap_str     = f"{gap_pct:+.1f}%" if gap_pct is not None else "-"
    vol_dec     = pat.get("post_base_volume_decline_flag", False)
    in_inter    = c.get("in_inter", False)
    tv_ratio    = pat.get("tv_ratio")
    tv_ratio_str = f"{tv_ratio:.2f}" if tv_ratio is not None else "-"
    status      = pat.get("status_summary", "-")
    tv_3d       = pat.get("tv_3d_flow", [])
    tv_3d_str   = " → ".join(_tv_eok(v) for v in tv_3d) if tv_3d else "-"

    tag = "★교집합" if in_inter else ""

    return (
        f"\n{seq}) <b>{c.get('name','')}({c.get('code','')})</b>"
        f" [{c.get('market','')}]{' ' + tag if tag else ''}\n"
        f"- 패턴: {pat.get('pattern_type_label','없음')} | 기준봉: {offset_str} | 상태: {status}\n"
        f"- 상승률: {_sign(float(c.get('change_pct',0)))} | 거래대금: {_tv_eok(tv)}\n"
        f"- 기준봉고가 대비: {gap_str} | 대금ratio: {tv_ratio_str}\n"
        f"- 최근3일 대금흐름: {tv_3d_str}\n"
        f"- 장대: {_yn(ind.get('big_candle'))} / 준장대: {_yn(ind.get('loose_big_candle'))} / "
        f"첫장대: {_yn(ind.get('first_big_candle'))}\n"
        f"- 이평밀집: {_yn(ind.get('ma_cluster'))} | "
        f"거래량60최고: {_yn(ind.get('vol_peak'))} / 거래대금60최고: {_yn(ind.get('tv_peak'))}\n"
        f"- 수급: {_supply_str(sup)}\n"
        f"- 뉴스: {_news_str(news)}"
    )


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
        "고가횡보형": "▶ 1~3일전 기준봉 후 고가횡보형",
        "눌림관찰형": "▶ 눌림 관찰형",
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
    """대시보드 링크 섹션 포맷. links가 없으면 빈 문자열."""
    if not links:
        return ""
    dated  = links.get("dated_url", "")
    latest = links.get("latest_url", "")
    lines = ["<b>[상세 대시보드]</b>"]
    if latest:
        lines.append(f"- 최신: {latest}")
    if dated:
        lines.append(f"- 날짜별: {dated}")
    return "\n".join(lines) + "\n"


# ── 1차 / 2차 알림 조합 ───────────────────────────────────

def build_first_alert(
    market_totals: dict,
    gainers,
    top_tv,
    intersection,
    key_candidates: list = [],
    run_time: str = "",
    enriched: dict = {},
    dashboard_links: dict = {},
) -> str:
    parts = [
        format_market_summary(market_totals, run_time, "1차"),
        format_top_gainers(gainers, enriched),
        format_top_tv(top_tv, enriched),
        format_intersection(intersection, enriched),
        format_key_candidates(key_candidates),
    ]
    link_str = _format_dashboard_links(dashboard_links)
    if link_str:
        parts.append(link_str)
    return "\n".join(parts)


def build_second_alert(
    market_totals: dict,
    gainers,
    top_tv,
    intersection,
    key_candidates: list,
    run_time: str,
    enriched: dict = {},
    dashboard_links: dict = {},
) -> str:
    parts = [
        format_market_summary(market_totals, run_time, "2차"),
        format_top_gainers(gainers, enriched),
        format_top_tv(top_tv, enriched),
        format_intersection(intersection, enriched),
        format_key_candidates(key_candidates),
    ]
    link_str = _format_dashboard_links(dashboard_links)
    if link_str:
        parts.append(link_str)
    return "\n".join(parts)
