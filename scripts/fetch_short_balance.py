# scripts/fetch_short_balance.py
"""KRX 공매도 잔고 수집 (pykrx)

- 데이터: T+2 기준 (2거래일 전 확정치) — KRX 공식 공개 데이터
- 방식: 전 종목 1회 호출 → 후보 종목 필터 (종목당 개별 호출 X)
- 반환: {종목코드: {"ratio": float, "qty": int}} — 실패 시 빈 dict
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_cache: dict = {"date": "", "data": {}}


def _latest_trading_day(offset: int = 2) -> str:
    """T-offset 거래일 (주말 건너뜀, 공휴일 미처리)"""
    dt = datetime.now()
    skipped = 0
    while skipped < offset:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:  # 월~금
            skipped += 1
    # 추가로 주말이면 금요일로
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


def fetch_short_balance_bulk(date_str: str | None = None) -> dict[str, dict]:
    """
    전 종목 공매도 잔고 일괄 조회.
    Returns: {"005930": {"ratio": 1.23, "qty": 1234567}, ...}
    """
    global _cache

    target_date = date_str or _latest_trading_day(2)

    if _cache["date"] == target_date and _cache["data"]:
        logger.debug(f"공매도 잔고 캐시 사용 ({target_date})")
        return _cache["data"]

    try:
        from pykrx import stock as krx

        logger.info(f"공매도 잔고 조회 시작 ({target_date})")
        df = krx.get_shorting_balance_by_ticker(target_date, market="ALL")

        if df is None or df.empty:
            logger.warning(f"공매도 잔고 데이터 없음 ({target_date}) — 전일로 재시도")
            target_date = _latest_trading_day(3)
            df = krx.get_shorting_balance_by_ticker(target_date, market="ALL")

        if df is None or df.empty:
            logger.warning("공매도 잔고 데이터 없음 — 건너뜀")
            return {}

        # 컬럼명 탐색 (pykrx 버전별 차이 대응)
        ratio_col = next((c for c in df.columns if "비율" in c), None)
        qty_col   = next((c for c in df.columns if "수량" in c), None)
        if ratio_col is None:
            logger.warning(f"공매도 잔고 컬럼 없음 (columns={list(df.columns)})")
            return {}

        result: dict[str, dict] = {}
        for code, row in df.iterrows():
            try:
                ratio = float(row.get(ratio_col, 0) or 0)
                qty   = int(row.get(qty_col, 0) or 0) if qty_col else 0
                if ratio > 0 or qty > 0:
                    result[str(code).zfill(6)] = {"ratio": ratio, "qty": qty}
            except Exception:
                continue

        _cache["date"] = target_date
        _cache["data"] = result
        logger.info(f"공매도 잔고 {len(result)}개 종목 로드 완료 ({target_date})")
        return result

    except Exception as e:
        logger.warning(f"공매도 잔고 수집 실패: {e}")
        return {}


def get_short_balance(code: str, bulk_data: dict[str, dict] | None = None) -> dict:
    """
    단일 종목 공매도 잔고 조회.
    bulk_data: fetch_short_balance_bulk() 결과를 미리 전달하면 재사용.
    Returns: {"ratio": float, "qty": int} or {}
    """
    data = bulk_data if bulk_data is not None else fetch_short_balance_bulk()
    return data.get(str(code).zfill(6), {})
