# scripts/intraday_snapshot.py
"""장중 시세 스냅샷 저장 — 11:00 / 13:00 KST 실행.

수집·저장만 한다. 후보 산출·점수화·알림 없음.
현재 파이프라인은 14:50/17:50 두 컷만 보기 때문에 "지수는 빠지는데 종목만
오르는" 축(돌팬티 4강 케이스A)이나 "오전 저점 이후 상승"(2026-09-01 사례)을
관측할 수 없다. 그 사각지대를 메우기 위한 원자료 적재 전용.

산출물:
    data/intraday/{날짜}_{HHMM}.csv    거래대금 하한 통과 종목 시세
    data/intraday/{날짜}_index.json    지수 레벨/등락률 (스냅샷 시각별 누적)
"""

import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts import fetch_market_data as fmd
from scripts.market_calendar import get_now_kst, is_trading_day

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_OUT_DIR = Path("data/intraday")

# 스냅샷 시점 거래대금 하한. 장중 100억 미만은 종가베팅 후보가 될 일이 없어
# 저장에서 제외한다 (전 종목 저장 시 연 70MB 이상 누적).
_MIN_TV_WON = 10_000_000_000

_COLS = ["종목코드", "종목명", "현재가", "등락률", "거래대금"]


def run() -> None:
    now = get_now_kst()
    if not is_trading_day(now.date()):
        logger.info("비거래일 — 스냅샷 생략")
        return

    date_str = now.strftime("%Y-%m-%d")
    hhmm     = now.strftime("%H%M")

    raw = fmd.run()
    if not raw:
        logger.warning("시세 수집 실패 — 저장 생략")
        return

    import pandas as pd
    df = pd.concat(raw.values(), ignore_index=True)
    df["거래대금"] = pd.to_numeric(df["거래대금"], errors="coerce").fillna(0)
    df = df[df["거래대금"] >= _MIN_TV_WON][_COLS].sort_values("거래대금", ascending=False)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUT_DIR / f"{date_str}_{hhmm}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")

    # 지수는 하루 한 파일에 시각별로 누적 (종목 스냅샷과 짝을 맞춰 비교하기 위함)
    idx_path = _OUT_DIR / f"{date_str}_index.json"
    idx_all = {}
    if idx_path.exists():
        try:
            idx_all = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"기존 지수 파일 파싱 실패, 새로 씀: {e}")
    idx_all[hhmm] = fmd.fetch_index_levels()
    idx_path.write_text(
        json.dumps(idx_all, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lv = idx_all[hhmm]
    logger.info(
        f"장중 스냅샷 저장: {path} ({len(df)}종목) | "
        f"코스피 {lv.get('kospi_level')} ({lv.get('kospi_chg')}%) "
        f"코스닥 {lv.get('kosdaq_level')} ({lv.get('kosdaq_chg')}%)"
    )


if __name__ == "__main__":
    run()
