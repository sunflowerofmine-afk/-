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


# ── 신호 파일 스냅샷 해석 (분 단위 드리프트 허용) ───────────────────────────
# GitHub Actions 실행 지연으로 파이프라인 시각이 분 경계를 넘으면 파일명이
# 1750 → 1751, 1450 → 1451 로 바뀐다(2026-07-03부터 실제 발생).
# 소비자가 "_1750_"을 정확히 일치로 찾으면 신호를 통째로 놓치므로,
# market_calendar.get_run_type과 동일한 시각 창으로 판정한다.
_FIRST_WINDOW  = (1440, 1510)   # 1차 14:40~15:10
_SECOND_WINDOW = (1740, 1810)   # 2차 17:40~18:10


def snapshot_kind(snap: str) -> str | None:
    """신호 파일명의 HHMM → "2차" | "1차" | None(수동 실행)."""
    try:
        v = int(snap)
    except (TypeError, ValueError):
        return None
    if _SECOND_WINDOW[0] <= v <= _SECOND_WINDOW[1]:
        return "2차"
    if _FIRST_WINDOW[0] <= v <= _FIRST_WINDOW[1]:
        return "1차"
    return None


def find_signal_file(date_str: str, kind: str = "2차", signals_dir: Path | None = None) -> Path | None:
    """해당 날짜의 자동 실행 신호 CSV 반환. 분 드리프트(1750/1751) 허용. 없으면 None."""
    d = Path(signals_dir) if signals_dir else SIGNALS_DIR
    cands = [f for f in sorted(d.glob(f"{date_str}_*_signals.csv"))
             if snapshot_kind(f.name[11:15]) == kind]
    return cands[-1] if cands else None


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
