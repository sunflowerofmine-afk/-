# scripts/storage.py
"""데이터 저장/로드 모듈"""

import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import RAW_DIR, PROCESSED_DIR, SIGNALS_DIR, RESULTS_DIR

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def get_timestamp_str() -> str:
    """현재 KST 기준 'YYYY-MM-DD_HHMM' 문자열 반환"""
    now = datetime.now(tz=KST)
    return now.strftime("%Y-%m-%d_%H%M")


def _safe_save(df: pd.DataFrame, path: Path) -> Path:
    """중복 방지: 같은 경로가 존재하면 _v2, _v3 suffix 부여"""
    if not path.exists():
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"저장: {path}")
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 100):
        new_path = path.parent / f"{stem}_v{i}{suffix}"
        if not new_path.exists():
            df.to_csv(new_path, index=False, encoding="utf-8-sig")
            logger.info(f"저장(버전): {new_path}")
            return new_path
    raise RuntimeError(f"저장 경로 생성 실패: {path}")


def save_raw(df: pd.DataFrame, market: str, timestamp_str: str) -> Path:
    """data/raw/YYYY-MM-DD_HHMM_KOSPI.csv 저장"""
    path = RAW_DIR / f"{timestamp_str}_{market}.csv"
    return _safe_save(df, path)


def save_processed(df: pd.DataFrame, label: str, timestamp_str: str) -> Path:
    """data/processed/YYYY-MM-DD_HHMM_{label}.csv 저장"""
    path = PROCESSED_DIR / f"{timestamp_str}_{label}.csv"
    return _safe_save(df, path)


def save_signals(df: pd.DataFrame, timestamp_str: str) -> Path:
    """data/signals/YYYY-MM-DD_HHMM_signals.csv 저장"""
    path = SIGNALS_DIR / f"{timestamp_str}_signals.csv"
    return _safe_save(df, path)


def load_recent_raw(code: str, days: int = 60) -> pd.DataFrame:
    """
    raw/ 폴더의 최근 N일 CSV에서 특정 종목코드 히스토리 로드.
    반환 DataFrame 컬럼: date, close, open, high, low, volume, trading_value, change_pct
    데이터가 없으면 빈 DataFrame 반환.
    """
    frames = []
    csv_files = sorted(RAW_DIR.glob("*.csv"), reverse=True)[:days * 2]

    for f in csv_files:
        try:
            df = pd.read_csv(f, encoding="utf-8-sig", dtype={"종목코드": str})
            if "종목코드" not in df.columns:
                continue
            row = df[df["종목코드"] == code]
            if row.empty:
                continue
            # 파일명에서 날짜 추출
            date_str = f.stem.split("_")[0]
            row = row.copy()
            row["date"] = date_str
            frames.append(row)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result.sort_values("date", ascending=False, inplace=True)
    result.reset_index(drop=True, inplace=True)
    return result
