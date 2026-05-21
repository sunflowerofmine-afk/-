# scripts/fetch_pension_data.py
"""KRX 연기금 순매수 수집 (pykrx)

- 데이터: T-1 기준 (전일 확정치) — KRX 공식 공개 데이터
- 방식: 전 종목 1회 호출 → 후보 종목 필터 (종목당 개별 호출 X)
- 반환: {종목코드: net_won} — 연기금 순매수 (원 단위, + = 순매수 / - = 순매도)
- 실패 시 빈 dict 반환 (파이프라인 중단 없음)
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_cache: dict = {"date": "", "data": {}}


def _prev_trading_day(offset: int = 1) -> str:
    """T-offset 거래일 (주말 건너뜀, 공휴일 미처리)"""
    dt = datetime.now()
    skipped = 0
    while skipped < offset:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:
            skipped += 1
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


def fetch_pension_bulk(date_str: str | None = None) -> dict[str, float]:
    """
    전 종목 연기금 순매수 일괄 조회.
    Returns: {"005930": 50_000_000_000.0, ...}  # 원 단위
    """
    global _cache

    target_date = date_str or _prev_trading_day(1)

    if _cache["date"] == target_date and _cache["data"]:
        logger.debug(f"연기금 순매수 캐시 사용 ({target_date})")
        return _cache["data"]

    try:
        from pykrx import stock as krx

        logger.info(f"연기금 순매수 조회 시작 ({target_date})")
        df = krx.get_market_net_purchases_of_equities(
            target_date, target_date, "연기금"
        )

        if df is None or df.empty:
            logger.warning(f"연기금 데이터 없음 ({target_date}) — 전전일로 재시도")
            target_date = _prev_trading_day(2)
            df = krx.get_market_net_purchases_of_equities(
                target_date, target_date, "연기금"
            )

        if df is None or df.empty:
            logger.warning("연기금 데이터 없음 — 건너뜀")
            return {}

        # 순매수 컬럼 탐색 (pykrx 버전별 컬럼명 차이 대응)
        net_col = None
        for candidate in ["순매수", "순매수량", "net", "NET"]:
            if candidate in df.columns:
                net_col = candidate
                break
        if net_col is None:
            logger.warning(f"연기금 순매수 컬럼 없음 (columns={list(df.columns)}) — 건너뜀")
            return {}

        result: dict[str, float] = {}
        for code, row in df.iterrows():
            try:
                val = float(row[net_col])
                if val != 0:
                    result[str(code).zfill(6)] = val
            except Exception:
                continue

        _cache["date"] = target_date
        _cache["data"] = result
        logger.info(f"연기금 순매수 {len(result)}개 종목 로드 완료 ({target_date})")
        return result

    except Exception as e:
        logger.warning(f"연기금 순매수 수집 실패: {e}")
        return {}
