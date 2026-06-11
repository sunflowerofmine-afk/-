# scripts/scoring.py
"""종합 점수 및 체크리스트 계산 모듈"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import MIN_TRADING_VALUE_EOK, TV_SCORE_3_MIN_EOK, TV_SCORE_2_MIN_EOK, TV_SCORE_1_MIN_EOK
from scripts.models import ScoreDetail, ChecklistDetail, ProcessedData, SupplyData, NewsData

MIN_TV_WON = MIN_TRADING_VALUE_EOK * 100_000_000  # 1500억 → 원


def calc_supply_label(supply: SupplyData, trading_value: float = 0) -> str:
    """
    수급 텍스트 라벨.
    당일 + 5일 누적 모두 양수 + TV 대비 순매수 비율 ≥ 1% 일 때만 ★ 강조.
    """
    if supply.status != "ok":
        return "확인불가"
    inst_1d = (supply.institution_net    or 0) > 0
    frgn_1d = (supply.foreign_net        or 0) > 0
    inst_5d = (supply.institution_net_5d or 0) > 0
    frgn_5d = (supply.foreign_net_5d     or 0) > 0

    # 당일 기준 기본 라벨
    if inst_1d and frgn_1d:
        base = "쌍매수"
    elif inst_1d:
        base = "기관매수"
    elif frgn_1d:
        base = "외인매수"
    else:
        base = "혼조"

    # TV 대비 순매수 비율 ≥ 1% 조건 (trading_value 미제공 시 스킵)
    _MIN_RATIO = 0.01
    inst_ratio_ok = trading_value <= 0 or (supply.institution_net or 0) / trading_value >= _MIN_RATIO
    frgn_ratio_ok = trading_value <= 0 or (supply.foreign_net     or 0) / trading_value >= _MIN_RATIO

    # 5일 누적 + 비율 조건 모두 충족 시 ★ 강조
    if base == "쌍매수" and inst_5d and frgn_5d and inst_ratio_ok and frgn_ratio_ok:
        return "★쌍매수"
    if base == "기관매수" and inst_5d and inst_ratio_ok:
        return "★기관매수"
    if base == "외인매수" and frgn_5d and frgn_ratio_ok:
        return "★외인매수"
    return base


def calc_score(
    code:       str,
    trading_value: float,         # 원 단위
    processed:  ProcessedData,
    supply:     SupplyData,
    news:       NewsData,
    in_intersection: bool = False,
    patterns:   dict | None = None,
    is_leading_sector: bool = False,
) -> ScoreDetail:
    """
    ScoreDetail 계산.
    정렬 우선순위: 교집합 여부 > total_score > 패턴 수 > 거래대금 > 상승률
    """
    s = ScoreDetail(code=code)

    # 뉴스 점수 (0~3)
    s.news_score = max(0, min(3, news.score))
    if s.news_score > 0:
        s.reasons.append(f"뉴스 +{s.news_score} (뉴스 재료 점수)")

    # 거래대금 점수 (0~3): 1조→3, 5천억→2, 1천억→1
    tv_eok = trading_value / 100_000_000
    if tv_eok >= TV_SCORE_3_MIN_EOK:
        s.trading_value_score = 3
    elif tv_eok >= TV_SCORE_2_MIN_EOK:
        s.trading_value_score = 2
    elif tv_eok >= TV_SCORE_1_MIN_EOK:
        s.trading_value_score = 1
    if s.trading_value_score > 0:
        s.reasons.append(f"대금 +{s.trading_value_score} (거래대금 {tv_eok:,.0f}억)")

    # 캔들 점수 (0~3)
    if processed.big_candle_flag:
        s.candle_score = 3
        s.reasons.append("캔들 +3 (장대양봉 15%↑)")
    elif processed.loose_big_candle_flag:
        s.candle_score = 2
        s.reasons.append("캔들 +2 (양봉 10%↑)")

    # 수급 점수 (가점): 기관 순매수 +1, 외국인 순매수 +1
    if supply.status == "ok":
        if (supply.institution_net or 0) > 0:
            s.supply_score += 1
            s.reasons.append("수급 +1 (기관 순매수)")
        if (supply.foreign_net or 0) > 0:
            s.supply_score += 1
            s.reasons.append("수급 +1 (외국인 순매수)")
    supply.supply_label = calc_supply_label(supply, trading_value)

    # 보너스 점수
    if in_intersection:
        s.bonus_score += 2
        s.reasons.append("보너스 +2 (교집합)")
    if is_leading_sector:
        s.bonus_score += 1
        s.reasons.append("보너스 +1 (주도 섹터)")
    if processed.volume_peak_60d:
        s.bonus_score += 1
        s.reasons.append("보너스 +1 (거래량 60일 최고)")
    if processed.trading_value_peak_60d:
        s.bonus_score += 1
        s.reasons.append("보너스 +1 (거래대금 60일 최고)")
    if patterns:
        if patterns.get("consolidation_flag"):
            s.bonus_score += 1
            s.reasons.append("보너스 +1 (기간조정 패턴)")
        if patterns.get("pullback_support_flag"):
            s.bonus_score += 1
            s.reasons.append("보너스 +1 (되돌림 지지 패턴)")
        if patterns.get("high_tight_consolidation_flag"):
            s.bonus_score += 1
            s.reasons.append("보너스 +1 (고가수축형)")
        if patterns.get("high_tight_reignite_flag"):
            s.bonus_score += 1
            s.reasons.append("보너스 +1 (고가수축 재점화)")

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
        ((supply.institution_net or 0) > 0 or (supply.foreign_net or 0) > 0)
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
