# scripts/models.py
"""전체 파이프라인에서 사용하는 타입 정의 (dataclass 기반)"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StockData:
    """수집된 종목 기본 데이터"""
    date:          str
    time:          str              # "1450" 또는 "1750"
    market:        str              # "KOSPI" / "KOSDAQ"
    code:          str
    name:          str
    close:         float
    change_pct:    float
    open:          float = 0.0
    high:          float = 0.0
    low:           float = 0.0
    volume:        int   = 0
    trading_value: float = 0.0     # 원 단위
    market_cap:    float = 0.0     # 원 단위


@dataclass
class SupplyData:
    """수급 데이터"""
    code:            str
    institution_net: Optional[float] = None   # 원 단위
    foreign_net:     Optional[float] = None
    program_net:     Optional[float] = None
    status:          str = "failed"           # "ok" / "failed"


@dataclass
class NewsData:
    """뉴스 데이터 및 점수"""
    code:         str
    titles:       list[str]  = field(default_factory=list)
    timestamps:   list[str]  = field(default_factory=list)
    score:        int        = 0       # 0~3
    keyword_tags: list[str]  = field(default_factory=list)
    status:       str        = "ok"   # "ok" / "failed" / "empty"
    llm_summary:  Optional[str]  = None  # USE_LLM_NEWS=True 시 채워짐 (예: "재료: [AI] 수요 증가 (섹터확산)")


@dataclass
class ProcessedData:
    """지표 계산 결과"""
    code:                    str
    ma5:                     Optional[float] = None
    ma10:                    Optional[float] = None
    ma20:                    Optional[float] = None
    ma60:                    Optional[float] = None
    ma_cluster_flag:         bool  = False
    volume_peak_60d:         bool  = False
    trading_value_peak_60d:  bool  = False
    candle_body_ratio:       float = 0.0
    upper_shadow_ratio:      float = 0.0
    big_candle_flag:         bool  = False
    loose_big_candle_flag:   bool  = False
    first_big_candle_flag:   bool  = False
    pattern_type:            str   = "없음"   # 패턴 요약 문자열
    data_ok:                 bool  = False    # 히스토리 데이터 충분 여부


@dataclass
class ScoreDetail:
    """종합 점수 상세"""
    code:                 str
    news_score:           int   = 0   # 0~3
    trading_value_score:  int   = 0   # 0~3
    chart_score:          int   = 0   # 가점
    candle_score:         int   = 0   # 0~3
    supply_score:         int   = 0   # 가점
    bonus_score:          int   = 0   # 교집합, 거래량60최고
    total_score:          int   = 0

    def calc_total(self) -> int:
        self.total_score = (
            self.news_score +
            self.trading_value_score +
            self.chart_score +
            self.candle_score +
            self.supply_score +
            self.bonus_score
        )
        return self.total_score


@dataclass
class ChecklistDetail:
    """필수/보조 조건 체크리스트"""
    code:                str
    # 필수 조건 (4개)
    big_candle_ok:       bool = False
    first_big_candle_ok: bool = False
    ma_cluster_ok:       bool = False
    trading_value_ok:    bool = False
    # 보조 조건
    volume_peak_ok:      bool = False
    supply_ok:           bool = False

    @property
    def required_pass_count(self) -> int:
        return sum([
            self.big_candle_ok,
            self.first_big_candle_ok,
            self.ma_cluster_ok,
            self.trading_value_ok,
        ])

    @property
    def is_candidate(self) -> bool:
        """필수 조건 70% (4개 중 3개 이상) 충족 시 후보"""
        return self.required_pass_count >= 3
