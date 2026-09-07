# scripts/market_calendar.py
"""한국 증권시장 거래일 판단 모듈"""

import sys
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

KST = timezone(timedelta(hours=9))

# KRX 휴장일 (매년 업데이트 필요)
KRX_HOLIDAYS = {
    # 2025년
    date(2025,  1,  1), date(2025,  1, 28), date(2025,  1, 29), date(2025,  1, 30),
    date(2025,  3,  1), date(2025,  5,  5), date(2025,  5,  6), date(2025,  6,  6),
    date(2025,  8, 15), date(2025, 10,  3), date(2025, 10,  5), date(2025, 10,  6),
    date(2025, 10,  7), date(2025, 10,  8), date(2025, 10,  9), date(2025, 12, 25),
    date(2025, 12, 31),
    # 2026년 (KRX 공식 확인 2026-05-08 기준)
    date(2026,  1,  1),                                                              # 신정
    date(2026,  2, 16), date(2026,  2, 17), date(2026,  2, 18),                     # 설날 연휴
    date(2026,  3,  2),                                                              # 삼일절 대체공휴일 (3/1 일요일)
    date(2026,  5,  1), date(2026,  5,  5),                                          # 노동절, 어린이날
    date(2026,  5, 25),                                                              # 부처님오신날 대체공휴일 (5/24 일요일)
    date(2026,  8, 17),                                                              # 광복절 대체공휴일 (8/15 토요일)
    date(2026,  9, 24), date(2026,  9, 25),                                          # 추석 연휴 (대체공휴일 없음)
    date(2026, 10,  5), date(2026, 10,  9),                                          # 개천절 대체공휴일 (10/3 토요일), 한글날
    date(2026, 12, 25), date(2026, 12, 31),                                          # 성탄절, KRX 연말휴장
}


def get_now_kst() -> datetime:
    """현재 KST 시각 반환"""
    return datetime.now(tz=KST)


def is_trading_day(d: date | None = None) -> bool:
    """
    주어진 날짜(또는 오늘 KST)가 거래일이면 True.
    주말, KRX 휴장일이면 False.
    """
    if d is None:
        d = get_now_kst().date()
    if d.weekday() >= 5:   # 토=5, 일=6
        return False
    if d in KRX_HOLIDAYS:
        return False
    return True


def get_next_trading_day(date8: str) -> str | None:
    """
    YYYYMMDD 문자열 기준 다음 거래일을 YYYYMMDD 문자열로 반환.
    최대 7일 탐색 후 없으면 None.
    """
    try:
        d = date(int(date8[:4]), int(date8[4:6]), int(date8[6:]))
    except ValueError:
        return None
    for _ in range(7):
        d += timedelta(days=1)
        if is_trading_day(d):
            return d.strftime("%Y%m%d")
    return None


def get_prev_trading_day(date8: str) -> str | None:
    """
    YYYYMMDD 문자열 기준 직전 거래일을 YYYYMMDD 문자열로 반환.
    최대 7일 탐색 후 없으면 None.
    """
    try:
        d = date(int(date8[:4]), int(date8[4:6]), int(date8[6:]))
    except ValueError:
        return None
    for _ in range(7):
        d -= timedelta(days=1)
        if is_trading_day(d):
            return d.strftime("%Y%m%d")
    return None


def get_run_type(now: datetime | None = None) -> str:
    """
    실행 시각 기준 run_type 반환.
    15:20~15:50 → "2차" (종가 확정 직후)
    17:40~18:10 → "2차" (NXT 애프터마켓 진행 중)
    그 외        → "수동"

    2026-09-08: 장중 14:50 실행을 폐지하고 종가 확정 후 15:35로 옮겼다.
    - 14:50은 장중 잠정치라 종가·거래대금이 확정 전이었고, 그 신호는 리뷰에도
      집계되지 않아(같은 날 마지막 CSV만 읽음) 1년간 검증된 적이 없다.
    - 정규장 마감 전 구간은 사용자가 MTS로 직접 판단하는 훈련 시간으로 두고,
      15:35 알림은 그 판단을 채점하는 용도로 쓴다.
    두 실행 모두 "2차"로 판정해 동일한 상세 분석을 태운다. 실질 차이는
    NXT 애프터마켓(15:40 개장) 반영 여부뿐이며, 그건 시각으로 자연히 갈린다.
    """
    if now is None:
        now = get_now_kst()
    hour, minute = now.hour, now.minute
    total = hour * 60 + minute
    if 15 * 60 + 20 <= total <= 15 * 60 + 50:
        return "2차"
    if 17 * 60 + 40 <= total <= 18 * 60 + 10:
        return "2차"
    return "수동"
