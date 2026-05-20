# scripts/us_briefing.py
"""미국장 브리핑 — 07:50 KST 발송"""
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_us_market import fetch_indices, fetch_headlines
from scripts.llm_analyzer import summarize_us_market
from scripts.notifier import send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def _sign(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def _arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "→"


def _fmt(d: dict | None) -> str:
    if not d or d.get("chg_pct") is None:
        return "-"
    return f"{_sign(d['chg_pct'])} {_arrow(d['chg_pct'])}"


def build_message(indices: dict, summary: str) -> str:
    now = datetime.now()
    date_str = f"{now.month:02d}/{now.day:02d} ({_DAY_KO[now.weekday()]})"

    vix = indices.get("VIX", {})
    krw = indices.get("달러/원", {})

    vix_val = vix.get("value")
    vix_chg = vix.get("chg_pct")
    vix_str = f"{vix_val:.1f} {_arrow(vix_chg)}" if vix_val and vix_chg is not None else "-"

    krw_val = krw.get("value")
    krw_chg = krw.get("chg_pct")
    krw_str = f"{krw_val:,.0f}원 {_arrow(krw_chg)}" if krw_val and krw_chg is not None else "-"

    return (
        f"<b>[미국장 브리핑] {date_str}</b>\n"
        f"S&P500 {_fmt(indices.get('S&P500'))} | 나스닥 {_fmt(indices.get('나스닥'))} | 다우 {_fmt(indices.get('다우'))}\n"
        f"VIX {vix_str} | 필라델피아반도체 {_fmt(indices.get('필라델피아반도체'))}\n"
        f"달러/원 {krw_str}\n"
        f"\n{summary}"
    )


def main():
    logger.info("미국장 브리핑 시작")
    indices = fetch_indices()
    headlines = fetch_headlines()
    logger.info(f"지수 {len(indices)}개, 헤드라인 {len(headlines)}개 수집")
    summary = summarize_us_market(indices, headlines)
    msg = build_message(indices, summary)
    logger.info(f"메시지:\n{msg}")
    ok = send_message(msg)
    logger.info(f"발송 {'성공' if ok else '실패'}")


if __name__ == "__main__":
    main()
