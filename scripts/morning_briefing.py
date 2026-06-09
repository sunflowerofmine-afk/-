# scripts/morning_briefing.py
"""익일 아침 브리핑 — 09:00 KST 발송
전일 신호 종목 현황 + 갭 기준 행동 가이드.
"""

import glob
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import SIGNALS_DIR
from scripts.fetch_us_market import fetch_indices
from scripts.notifier import send_message, send_private

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def _sign(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def _gap_guide(gap_pct: float | None) -> str:
    """갭 기준 행동 가이드."""
    if gap_pct is None:
        return "→ 시가 미확인"
    if gap_pct >= 3.0:
        return "→ 강한 갭업 ☑ 시가 익절 고려"
    if gap_pct >= 1.0:
        return "→ 적정 갭업 ☑ 장 흐름 보고 판단"
    if gap_pct >= 0.0:
        return "→ 갭 없음 ☑ 수급 지속 여부 확인"
    if gap_pct >= -2.0:
        return "→ 소폭 갭다운 ⚠ 기준봉 고가 이탈 여부 확인"
    return "→ 갭다운 ⛔ 손절 기준 확인"


def _load_prev_signals() -> tuple[pd.DataFrame | None, str]:
    """가장 최근 signals CSV 로드. (2차 우선)"""
    files = sorted(glob.glob(str(SIGNALS_DIR / "*_signals.csv")))
    if not files:
        return None, ""
    # 최신 파일
    try:
        try:
            df = pd.read_csv(files[-1], encoding="utf-8-sig", dtype={"종목코드": str})
        except Exception:
            df = pd.read_csv(files[-1], encoding="cp949", dtype={"종목코드": str})
        fname = Path(files[-1]).stem   # e.g. "2026-05-20_1750"
        date_part = fname[:10]
        return df, date_part
    except Exception as e:
        logger.warning(f"signals 로드 실패: {e}")
        return None, ""


def _stock_current_price(code: str) -> float | None:
    """yfinance로 당일 시가 조회 (KRX: code.KS / code.KQ)."""
    try:
        import yfinance as yf
        for suffix in [".KS", ".KQ"]:
            ticker = yf.Ticker(f"{code}{suffix}")
            hist = ticker.history(period="2d")
            if not hist.empty:
                return float(hist["Open"].iloc[-1])
    except Exception as e:
        logger.debug(f"[{code}] yfinance 조회 실패: {e}")
    return None


def build_message(df: pd.DataFrame, signal_date: str) -> str:
    now = datetime.now()
    date_str = f"{now.month:02d}/{now.day:02d} ({_DAY_KO[now.weekday()]})"

    lines = [f"<b>🌅 아침 브리핑 | {date_str}</b>"]
    lines.append(f"전일({signal_date}) 신호 종목 현황\n")

    # 핵심 후보만 (in_inter 또는 점수 상위)
    if "in_inter" in df.columns:
        key_df = df[df["in_inter"] == True]
        if key_df.empty:
            key_df = df
    else:
        key_df = df

    key_df = key_df.head(5)   # 최대 5종목

    for _, row in key_df.iterrows():
        code        = str(row.get("종목코드", "")).zfill(6)
        name        = str(row.get("종목명", ""))
        entry_price = float(row.get("signal_price", 0) or row.get("entry_reference_price", 0) or 0)
        sector      = str(row.get("sector", ""))
        pattern     = str(row.get("pattern_type_label", "없음"))

        # 당일 시가 조회 (없으면 "-")
        cur_price = _stock_current_price(code)
        if cur_price and entry_price > 0:
            gap_pct = (cur_price - entry_price) / entry_price * 100
            price_str = f"시가 {cur_price:,.0f}원  {_sign(gap_pct)}"
        else:
            gap_pct   = None
            price_str = "시가 조회 중"

        guide = _gap_guide(gap_pct)
        sector_str = f"[{sector}] " if sector else ""
        lines.append(
            f"• <b>{name}</b>({code}) {sector_str}{pattern}\n"
            f"  진입가 {entry_price:,.0f}원 | {price_str}\n"
            f"  {guide}"
        )

    # 오늘 미국장 지수 (전일 종가 기준)
    try:
        indices = fetch_indices()
        idx_parts = []
        for name, d in indices.items():
            chg = d.get("chg_pct")
            if name in ("S&P500", "나스닥", "필라델피아반도체") and chg is not None:
                arr = "▲" if chg > 0 else "▼"
                idx_parts.append(f"{name} {chg:+.1f}%{arr}")
        if idx_parts:
            lines.append(f"\n📊 미국(전일) {' | '.join(idx_parts)}")
    except Exception as e:
        logger.debug(f"지수 조회 실패: {e}")

    return "\n".join(lines)


def main():
    logger.info("아침 브리핑 시작")
    df, signal_date = _load_prev_signals()

    if df is None or df.empty:
        logger.info("전일 신호 없음 — 브리핑 생략")
        return

    logger.info(f"전일 신호 {len(df)}개 ({signal_date})")
    msg = build_message(df, signal_date)
    logger.info(f"메시지 미리보기:\n{msg}")

    ok = send_message(msg)
    logger.info(f"발송 {'성공' if ok else '실패'}")

    # 복기 링크 — 봇 전용 채널(TELEGRAM_CHAT_ID)에만 발송
    review_link = '📝 <a href="https://docs.google.com/forms/d/e/1FAIpQLSdBJ9Nel88ILckZzCqTVuROPACKaFYaBz8wAlRzZ22MKl_pWA/viewform">어제 복기하기</a>'
    send_private(review_link)
    logger.info("복기 링크 봇 전용 채널 발송 완료")


if __name__ == "__main__":
    main()
