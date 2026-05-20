# scripts/us_briefing.py
"""미국장 브리핑 — 07:50 KST 발송"""
import glob
import logging
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import SIGNALS_DIR
from scripts.fetch_us_market import fetch_indices, fetch_headlines, fetch_candidate_related
from scripts.llm_analyzer import summarize_us_market
from scripts.notifier import send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# ── 표시 너비 계산 (한글 = 2, 영문 = 1) ───────────────────────
def _dw(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _ljust(s: str, width: int) -> str:
    return s + " " * max(0, width - _dw(s))


# ── 부호/화살표 ────────────────────────────────────────────
def _sign(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def _arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "→"


# ── 전일 signals 로드 ──────────────────────────────────────
def _load_prev_candidates() -> list[dict]:
    files = sorted(glob.glob(str(SIGNALS_DIR / "*_signals.csv")))
    if not files:
        return []
    try:
        try:
            df = pd.read_csv(files[-1], encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(files[-1], encoding="cp949")
        return [
            {
                "name":     str(r.get("종목명", "")),
                "sector":   str(r.get("sector", "")),
                "in_inter": bool(r.get("in_inter", False)),
            }
            for _, r in df.iterrows()
        ]
    except Exception as e:
        logger.warning(f"signals 로드 실패: {e}")
        return []


# ── 지수 테이블 포맷 ───────────────────────────────────────
def _index_table(indices: dict) -> str:
    COL = 14  # 라벨 표시 너비

    def row(label: str, chg: float | None, extra: str = "") -> str:
        chg_str = f"{_sign(chg):>8} {_arrow(chg)}" if chg is not None else "       -"
        return _ljust(label, COL) + chg_str + (f"  {extra}" if extra else "")

    vix = indices.get("VIX", {})
    krw = indices.get("달러/원", {})

    vix_extra = ""
    if vix.get("value"):
        vix_extra = f"VIX {vix['value']:.1f}"
        if vix.get("chg_pct") is not None:
            vix_extra += f" {_arrow(vix['chg_pct'])}"

    krw_extra = ""
    if krw.get("value"):
        krw_extra = f"달러/원 {krw['value']:,.0f}"
        if krw.get("chg_pct") is not None:
            krw_extra += f" {_arrow(krw['chg_pct'])}"

    lines = [
        _ljust("지수", COL) + "    등락",
        "─" * 28,
        row("S&P500",    indices.get("S&P500",            {}).get("chg_pct"), vix_extra),
        row("나스닥",    indices.get("나스닥",             {}).get("chg_pct"), krw_extra),
        row("다우",      indices.get("다우",               {}).get("chg_pct")),
        row("필라반도체", indices.get("필라델피아반도체",   {}).get("chg_pct")),
    ]
    return "\n".join(lines)


# ── 관련 미국주식 테이블 포맷 ───────────────────────────────
def _related_table(related: list[dict]) -> str:
    if not related:
        return "(전일 후보 없음)"

    blocks = []
    for grp in related:
        kr = "·".join(grp["kr_names"])
        # 섹터명이 길면 줄임
        sector = grp["sector"]
        if len(sector) > 20:
            sector = sector[:18] + ".."
        header = f"▶ {sector} │ {kr}"
        stock_lines = []
        for s in grp["stocks"]:
            chg = s["chg_pct"]
            chg_str = f"{_sign(chg):>8} {_arrow(chg)}" if chg is not None else "       -"
            stock_lines.append(f"  {s['ticker']:<8}{chg_str}")
        blocks.append(header + "\n" + "\n".join(stock_lines))

    return "\n\n".join(blocks)


# ── 메시지 조합 ────────────────────────────────────────────
def build_message(indices: dict, summary: str, related: list[dict]) -> str:
    now = datetime.now()
    date_str = f"{now.month:02d}/{now.day:02d} ({_DAY_KO[now.weekday()]})"

    idx_block  = _index_table(indices)
    rel_block  = _related_table(related)

    return (
        f"<b>🌏 미국장 브리핑 | {date_str}</b>\n"
        f"\n<pre>{idx_block}</pre>\n"
        f"\n<b>📰 뉴스요약</b>\n{summary}\n"
        f"\n<b>🔗 전일 후보 관련 미국주식</b>\n<pre>{rel_block}</pre>"
    )


# ── 진입점 ────────────────────────────────────────────────
def main():
    logger.info("미국장 브리핑 시작")

    indices    = fetch_indices()
    headlines  = fetch_headlines()
    candidates = _load_prev_candidates()

    logger.info(f"지수 {len(indices)}개, 헤드라인 {len(headlines)}개, 전일 후보 {len(candidates)}개")

    summary = summarize_us_market(indices, headlines)
    related = fetch_candidate_related(candidates) if candidates else []

    msg = build_message(indices, summary, related)
    logger.info(f"메시지 미리보기:\n{msg}")

    ok = send_message(msg)
    logger.info(f"발송 {'성공' if ok else '실패'}")


if __name__ == "__main__":
    main()
