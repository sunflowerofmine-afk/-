# scripts/weekly_research.py
"""금요일 주간 리서치 배치 — 눌림(pullback) 소급 생성.

평일 파이프라인(ENABLE_PULLBACK_OBS=False)에서 제외된 눌림 관찰을, 그 주
영업일에 대해 소급 생성한다. 눌림은 가격·이동평균·거래대금 기반이라 일봉으로
그날 시점을 재현할 수 있다(미래 데이터 누수 없음).

멱등: 실행 전 해당 주 날짜의 로그 라인을 제거 후 재생성 → 중복 누적 방지.

신호·적중률·패턴별 승률·기대수익(weekly_backtest)은 워크플로의 다음 step에서
`python -m scripts.weekly_backtest` 로 실행·발송한다(여기선 눌림만 담당).
"""
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import SIGNALS_DIR, PULLBACK_OBS_DIR
from scripts.market_calendar import get_now_kst, is_trading_day
from scripts import pullback_observer as pb

logger = logging.getLogger(__name__)

_SUMMARY_DIR = Path("data/signals")
_WATCH_LOG   = PULLBACK_OBS_DIR / "low_position_watch_log.jsonl"


def _week_trading_days(today: date) -> list[date]:
    """today가 속한 주(월~금) 중 오늘까지의 거래일 리스트."""
    monday = today - timedelta(days=today.weekday())
    return [
        d for i in range(5)
        if (d := monday + timedelta(days=i)) <= today and is_trading_day(d)
    ]


def _code_name_map(signals_dir: Path) -> dict[str, tuple[str, str]]:
    """signals.csv 전체에서 종목코드 → (종목명, 시장) 맵. 소급 시 종목명 보강용."""
    m: dict[str, tuple[str, str]] = {}
    for f in sorted(signals_dir.glob("*_signals.csv")):
        try:
            df = pd.read_csv(f, dtype={"종목코드": str})
        except Exception:
            continue
        for _, r in df.iterrows():
            c = str(r.get("종목코드", "")).zfill(6)
            if c and c not in m:
                m[c] = (str(r.get("종목명", "")), str(r.get("시장", "")))
    return m


def _load_summary(d: date) -> dict | None:
    """daily_summary_YYYY-MM-DD.json 로드 (그날 시장국면·섹터·지수등락)."""
    p = _SUMMARY_DIR / f"daily_summary_{d.isoformat()}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _purge_week_from_log(days: list[date]) -> None:
    """멱등성: 재생성 전 해당 주 날짜 라인을 watch_log에서 제거."""
    if not _WATCH_LOG.exists():
        return
    day_strs = {d.isoformat() for d in days}
    kept: list[str] = []
    removed = 0
    for line in _WATCH_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            kept.append(line)
            continue
        if rec.get("date") in day_strs:
            removed += 1
            continue
        kept.append(line)
    _WATCH_LOG.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    if removed:
        logger.info(f"기존 로그에서 이번 주 {removed}줄 제거(재생성 대비)")


def run() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    today = get_now_kst().date()
    days = _week_trading_days(today)
    if not days:
        logger.info("이번 주 거래일 없음 — 종료")
        return
    logger.info(f"눌림 소급 대상 거래일: {[d.isoformat() for d in days]}")

    _purge_week_from_log(days)
    name_map = _code_name_map(SIGNALS_DIR)
    # 합성 filtered_df: 종목명/시장 lookup 전용 (거래대금/등락률은 run이 일봉에서 산출)
    fdf = pd.DataFrame(
        [{"종목코드": c, "종목명": n, "시장": mk} for c, (n, mk) in name_map.items()]
    )

    total = 0
    for d in days:
        summ = _load_summary(d)
        if summ is None:
            logger.info(f"{d}: daily_summary 없음 — skip")
            continue
        try:
            res = pb.run(
                date=d.isoformat(),
                filtered_df=fdf,
                code_to_sector=summ.get("code_to_sector", {}),
                market_regime=summ.get("market_regime", ""),
                adl=summ.get("market_adl") or 0.0,
                index_return_1d=summ.get("kospi_chg"),
                signals_dir=SIGNALS_DIR,
                asof_date=d.isoformat(),
            )
            total += len(res)
            logger.info(f"{d}: 눌림 {len(res)}개 소급 생성")
        except Exception as e:
            logger.warning(f"{d}: 눌림 소급 실패 — {e}")

    logger.info(f"=== 눌림 소급 완료: 총 {total}개 / {len(days)}거래일 ===")


if __name__ == "__main__":
    run()
