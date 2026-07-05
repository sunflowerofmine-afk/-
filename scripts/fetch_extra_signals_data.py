# scripts/fetch_extra_signals_data.py
"""
신호 종목 보조 데이터 수집 — 매일 장 마감 후 21:00 KST 실행.

수집 내용:
  - 52주 최고가/최저가
  - 신고가 대비 현재 위치 (%)
  - 52주 신고가 근처 여부 (±5%)
  - 신고가 돌파 후 경과 거래일

결과 저장:
  data/signals_extra/YYYY-MM-DD_extra.json

파이프라인(14:50/17:50) 와 완전히 분리. 실패해도 메인 시스템 무영향.
"""
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts.fetch_stock_data import fetch_chart_data

try:
    from config.settings import REQUEST_DELAY
except Exception:
    REQUEST_DELAY = 0.3

logger = logging.getLogger(__name__)

_SIGNALS_DIR = Path("data/signals")
_EXTRA_DIR   = Path("data/signals_extra")
_NEAR_PCT    = 5.0   # 52주 신고가 대비 ±5% 이내 = 신고가 근처
_LOOKBACK    = 252   # 52주 = 약 252 거래일


def _find_target_signals() -> tuple[str | None, pd.DataFrame | None]:
    """가장 최근 2차(17:50) 신호 CSV 로드. 오늘 또는 어제 것."""
    for days_back in range(0, 5):
        d = date.today() - timedelta(days=days_back)
        d_str = d.isoformat()
        matches = sorted(_SIGNALS_DIR.glob(f"{d_str}_1750_signals.csv"), reverse=True)
        for path in matches:
            try:
                df = pd.read_csv(path, dtype={"종목코드": str}, encoding="utf-8-sig")
                if not df.empty:
                    logger.info(f"신호 파일: {path.name} ({len(df)}개)")
                    return d_str, df
            except Exception as e:
                logger.warning(f"로드 실패 {path}: {e}")
    return None, None


def _calc_52w(hist: pd.DataFrame, signal_price: float) -> dict:
    """
    52주(약 252거래일) 기준 신고가/신저가 및 위치 계산.
    hist: fetch_chart_data 반환값 (date 내림차순).
    """
    # 최근 252거래일만 사용
    recent = hist.head(_LOOKBACK)
    if recent.empty:
        return {}

    try:
        high_52w = float(recent["high"].max())
        low_52w  = float(recent["low"].min())
    except Exception:
        return {}

    if high_52w <= 0 or signal_price <= 0:
        return {}

    pct_from_high = round((signal_price - high_52w) / high_52w * 100, 2)
    is_near       = pct_from_high >= -_NEAR_PCT  # -5% 이내

    # 신고가 달성 후 경과 거래일 (최근 신고가 위치)
    days_since_high = None
    try:
        sorted_asc = recent.sort_values("date", ascending=True).reset_index(drop=True)
        idx = sorted_asc["high"].idxmax()
        days_since_high = int(len(sorted_asc) - 1 - idx)
    except Exception:
        pass

    return {
        "52w_high":          round(high_52w),
        "52w_low":           round(low_52w),
        "pct_from_52w_high": pct_from_high,
        "is_near_52w_high":  is_near,
        "days_since_52w_high": days_since_high,
    }


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sig_date, df = _find_target_signals()
    if df is None:
        logger.warning("신호 파일 없음 → skip")
        return

    out_path = _EXTRA_DIR / f"{sig_date}_extra.json"
    if out_path.exists():
        logger.info(f"이미 존재: {out_path} → skip")
        return

    _EXTRA_DIR.mkdir(parents=True, exist_ok=True)
    stocks: dict = {}

    for _, row in df.iterrows():
        code  = str(row.get("종목코드", "")).zfill(6)
        name  = str(row.get("종목명", ""))
        price = float(row.get("entry_reference_price") or row.get("signal_price") or 0)

        if not code or price <= 0:
            continue

        logger.info(f"  [{code}] {name} ...")
        try:
            hist = fetch_chart_data(code)
            time.sleep(REQUEST_DELAY)
            if hist.empty:
                continue
            extra = _calc_52w(hist, price)
            if extra:
                def _b(v):
                    return str(v).strip().lower() in ("true", "1")
                stocks[code] = {
                    "name": name, "signal_price": round(price),
                    # 향후 NXT 대장/후발주 D+1 백테스트용 committed 누적 (signals.csv는 gitignore)
                    "is_nxt":        _b(row.get("is_nxt")),
                    "nxt_dominant":  _b(row.get("nxt_dominant")),
                    "change_pct":    round(float(row.get("등락률", 0) or 0), 2),
                    "sector":        str(row.get("sector", "") or ""),
                    "total_score":   int(float(row.get("total_score", 0) or 0)),
                    **extra,
                }
        except Exception as e:
            logger.warning(f"  [{code}] 실패: {e}")

    result = {
        "date":      sig_date,
        "generated": date.today().isoformat(),
        "stocks":    stocks,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    near_count = sum(1 for v in stocks.values() if v.get("is_near_52w_high"))
    logger.info(f"저장: {out_path} ({len(stocks)}개 / 신고가근처 {near_count}개)")


if __name__ == "__main__":
    run()
