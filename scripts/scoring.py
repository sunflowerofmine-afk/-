# scripts/scoring.py
"""종합 점수 및 체크리스트 계산 모듈"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import MIN_TRADING_VALUE_EOK
from scripts.models import ScoreDetail, ChecklistDetail, ProcessedData, SupplyData, NewsData

MIN_TV_WON = MIN_TRADING_VALUE_EOK * 100_000_000  # 1500억 → 원


def calc_score(
    code:       str,
    trading_value: float,         # 원 단위
    processed:  ProcessedData,
    supply:     SupplyData,
    news:       NewsData,
    in_intersection: bool = False,
) -> ScoreDetail:
    """
    ScoreDetail 계산.
    정렬 우선순위: 교집합 여부 > total_score > 패턴 수 > 거래대금 > 상승률
    """
    s = ScoreDetail(code=code)

    # 뉴스 점수 (0~3)
    s.news_score = max(0, min(3, news.score))

    # 거래대금 점수 (0~3)
    tv_eok = trading_value / 100_000_000
    if tv_eok >= 3000:
        s.trading_value_score = 3
    elif tv_eok >= 2000:
        s.trading_value_score = 2
    elif tv_eok >= 1500:
        s.trading_value_score = 1

    # 캔들 점수 (0~3)
    if processed.big_candle_flag:
        s.candle_score = 3
    elif processed.loose_big_candle_flag:
        s.candle_score = 2

    # 수급 점수 (가점)
    if supply.status == "ok":
        inst = supply.institution_net or 0
        prog = supply.program_net or 0
        if inst > 0:
            s.supply_score += 1
        if prog > 0:
            s.supply_score += 1

    # 보너스 점수
    if in_intersection:
        s.bonus_score += 2
    if processed.volume_peak_60d:
        s.bonus_score += 1
    if processed.trading_value_peak_60d:
        s.bonus_score += 1

    s.calc_total()
    return s


def build_checklist(
    code:          str,
    trading_value: float,
    processed:     ProcessedData,
    supply:        SupplyData,
) -> ChecklistDetail:
    """ChecklistDetail 생성"""
    tv_ok = trading_value >= MIN_TV_WON

    supply_ok = (
        supply.status == "ok" and
        ((supply.institution_net or 0) > 0 or (supply.program_net or 0) > 0)
    )

    return ChecklistDetail(
        code=code,
        big_candle_ok=       processed.big_candle_flag or processed.loose_big_candle_flag,
        first_big_candle_ok= processed.first_big_candle_flag,
        ma_cluster_ok=       processed.ma_cluster_flag,
        trading_value_ok=    tv_ok,
        volume_peak_ok=      processed.volume_peak_60d,
        supply_ok=           supply_ok,
    )
