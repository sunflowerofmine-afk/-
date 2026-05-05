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
    # 2026년 (잠정)
    date(2026,  1,  1), date(2026,  2, 16), date(2026,  2, 17), date(2026,  2, 18),
    date(2026,  3,  1), date(2026,  5,  1), date(2026,  5,  5), date(2026,  5, 25), date(2026,  6,  6),
    date(2026,  8, 17), date(2026,  9, 24), date(2026,  9, 25), date(2026,  9, 28),
    date(2026, 10,  9), date(2026, 12, 25),
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


def get_run_type(now: datetime | None = None) -> str:
    """
    실행 시각 기준 run_type 반환.
    14:40~15:10 → "1차"
    17:40~18:10 → "2차"
    그 외        → "수동"
    """
    if now is None:
        now = get_now_kst()
    hour, minute = now.hour, now.minute
    total = hour * 60 + minute
    if 14 * 60 + 40 <= total <= 15 * 60 + 10:
        return "1차"
    if 17 * 60 + 40 <= total <= 18 * 60 + 10:
        return "2차"
    return "수동"
