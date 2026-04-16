"""
filter_stocks.py
수집된 종목 데이터에서 조건에 맞는 종목 필터링
"""

import sys
import logging
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import FILTER, PROCESSED_DIR

logger = logging.getLogger(__name__)


def filter_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """
    FILTER 조건 적용
    - 등락률 >= min_change_rate
    - 거래량 >= min_volume
    - 거래대금 >= min_trade_amount
    """
    mask = (
        (df["등락률"]   >= FILTER["min_change_rate"])  &
        (df["거래량"]   >= FILTER["min_volume"])        &
        (df["거래대금"] >= FILTER["min_trade_amount"])
    )
    result = df[mask].copy()
    result.sort_values("등락률", ascending=False, inplace=True)
    result.reset_index(drop=True, inplace=True)
    logger.info(f"필터 결과: {len(df)}개 → {len(result)}개")
    return result


def run(raw_paths: dict) -> dict:
    """raw 파일 경로 dict를 받아 필터링 후 processed/ 저장"""
    # TODO: fetch_data.run() 결과를 받아 처리
    pass
