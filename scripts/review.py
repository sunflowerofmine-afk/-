# scripts/review.py
"""T+1 자동 복기 — 어제 신호 성과 측정 + D+1~D+5 멀티데이 백테스트"""

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts.fetch_stock_data import fetch_daily_history

try:
    from config.settings import REQUEST_DELAY
except Exception:
    REQUEST_DELAY = 0.3

logger = logging.getLogger(__name__)

_SIGNALS_DIR   = Path("data/signals")
_MAX_REVIEW    = 15   # 최대 복기 종목 수 (요청 시간 제한)
_BACKFILL_DAYS = 12   # 백필 대상 최대 달력 일수 (≈ 9 거래일)


# ── 유틸 ────────────────────────────────────────────────────────────

def _find_yesterday_signals(today: date) -> tuple[pd.DataFrame | None, str | None]:
    """최근 거래일(최대 7일 이내) signals CSV 탐색. 같은 날 여러 개면 타임스탬프 최신 우선."""
    for days_back in range(1, 8):
        d     = today - timedelta(days=days_back)
        d_str = d.isoformat()
        matches = sorted(_SIGNALS_DIR.glob(f"{d_str}_*_signals.csv"), reverse=True)
        for path in matches:
            try:
                df = pd.read_csv(path, dtype={"종목코드": str})
                logger.info(f"복기 시그널 로드: {path} ({len(df)}개)")
                return df, d_str
            except Exception as e:
                logger.warning(f"시그널 로드 실패 {path}: {e}")
    return None, None


def _classify_fail_reason(gap_pct: float, kospi_chg: float | None) -> str | None:
    """갭 기반 실패 원인 분류. 성공이면 None."""
    if gap_pct >= 0:
        return None
    if kospi_chg is None:
        return "혼조"
    if kospi_chg <= -1.0:
        return "시황하락"
    if kospi_chg >= 0.5:
        return "개별약세"
    return "혼조"


def _load_daily_summary(date_str: str) -> dict | None:
    """daily_summary_{date_str}.json 로드. 없으면 None."""
    path = _SIGNALS_DIR / f"daily_summary_{date_str}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── 멀티데이 수익률 계산 ─────────────────────────────────────────────

def _pct(price, entry_price: float) -> float | None:
    try:
        p = float(price)
        if p > 0 and entry_price > 0:
            return round((p - entry_price) / entry_price * 100, 2)
    except (TypeError, ValueError):
        pass
    return None


def _calc_multiday_returns(hist: pd.DataFrame, entry_price: float, signal_date_str: str) -> dict:
    """
    D+1~D+5 수익률 계산.
    signal_date_str: YYYY-MM-DD 형식.
    반환 dict: 수익률 필드 + 절대값 필드(_d1_date_str 포함).
    """
    sig_dot = signal_date_str.replace("-", ".")
    post = (
        hist[hist["date"] > sig_dot]
        .sort_values("date", ascending=True)
        .reset_index(drop=True)
    )

    def _row(idx) -> pd.Series | None:
        return post.iloc[idx] if idx < len(post) else None

    def _f(row, col) -> float | None:
        if row is None:
            return None
        try:
            v = float(row[col])
            return v if v > 0 else None
        except (TypeError, ValueError, KeyError):
            return None

    r1, r2, r3, r4, r5 = _row(0), _row(1), _row(2), _row(3), _row(4)
    d: dict = {}

    # D+1 수익률
    d["d1_open_pct"]  = _pct(_f(r1, "open"),  entry_price)
    d["d1_high_pct"]  = _pct(_f(r1, "high"),  entry_price)
    d["d1_close_pct"] = _pct(_f(r1, "close"), entry_price)
    d["d1_low_pct"]   = _pct(_f(r1, "low"),   entry_price)
    # 절대가 (alive/failed 조건 판별용, JSON 저장 안 함)
    d["_d1_open"]     = _f(r1, "open")
    d["_d1_close"]    = _f(r1, "close")
    d["_d1_low"]      = _f(r1, "low")
    d["_d1_tv"]       = _f(r1, "trading_value")
    d["_d1_date_str"] = r1["date"] if r1 is not None else None

    # D+2
    d["d2_high_pct"]  = _pct(_f(r2, "high"),  entry_price)
    d["d2_close_pct"] = _pct(_f(r2, "close"), entry_price)

    # D+3
    d["d3_high_pct"]  = _pct(_f(r3, "high"),  entry_price)
    d["d3_close_pct"] = _pct(_f(r3, "close"), entry_price)

    # D+5 (index 4)
    d["d5_high_pct"]  = _pct(_f(r5, "high"),  entry_price)
    d["d5_close_pct"] = _pct(_f(r5, "close"), entry_price)

    # MFE / MAE (D+1~D+5 범위, 확보된 행까지만)
    mfe_val = mae_val = None
    mfe_day = mae_day = None
    day_labels = ["D+1", "D+2", "D+3", "D+4", "D+5"]
    for i, row in enumerate([r1, r2, r3, r4, r5]):
        if row is None:
            continue
        h  = _pct(_f(row, "high"), entry_price)
        lo = _pct(_f(row, "low"),  entry_price)
        if h is not None and (mfe_val is None or h > mfe_val):
            mfe_val, mfe_day = h, day_labels[i]
        if lo is not None and (mae_val is None or lo < mae_val):
            mae_val, mae_day = lo, day_labels[i]

    d["mfe"]     = round(mfe_val, 2) if mfe_val is not None else None
    d["mae"]     = round(mae_val, 2) if mae_val is not None else None
    d["mfe_day"] = mfe_day
    d["mae_day"] = mae_day

    return d


# ── 분류 로직 ────────────────────────────────────────────────────────

def _classify_interim_result_type(r: dict) -> str:
    """D+1~D+4 기반 임시 분류. D+5 전 최종 확정 금지."""
    d1o = r.get("d1_open_pct")
    d1h = r.get("d1_high_pct")
    d1c = r.get("d1_close_pct")
    d2h = r.get("d2_high_pct")
    d3h = r.get("d3_high_pct")
    mfe = r.get("mfe")
    mae = r.get("mae")

    if d1o is None and d1h is None:
        return "pending"

    immed_ok = (d1o is not None and d1o >= 2) or (d1h is not None and d1h >= 2)
    if immed_ok:
        return "과열소멸형" if (d1c is not None and d1c < 0) else "즉시성공형"

    d23_highs = [v for v in [d2h, d3h] if v is not None]
    if d1c is not None and d1c <= 0 and d23_highs and max(d23_highs) >= 3:
        return "눌림후재상승형"

    if mae is not None and mfe is not None and mae >= -5 and mfe >= 3:
        return "스윙전환가능형"

    # D+1 데이터가 있고 부정적 신호 뚜렷한 경우만 실패형 잠정 판정
    if d1c is not None and mfe is not None and mfe < 2 and mae is not None and mae <= -5:
        return "실패형"

    return "pending"


def _classify_final_result_type(r: dict) -> str | None:
    """D+5 데이터 확보 후 최종 분류. d5_high_pct 없으면 None 반환."""
    if r.get("d5_high_pct") is None:
        return None

    d1o = r.get("d1_open_pct")
    d1h = r.get("d1_high_pct")
    d1c = r.get("d1_close_pct")
    d2h = r.get("d2_high_pct")
    d3h = r.get("d3_high_pct")
    d5h = r.get("d5_high_pct")
    mfe = r.get("mfe")
    mae = r.get("mae")

    immed_ok = (d1o is not None and d1o >= 2) or (d1h is not None and d1h >= 2)
    if immed_ok:
        burned = (d1c is not None and d1c < 0) or (mae is not None and mae <= -5)
        return "과열소멸형" if burned else "즉시성공형"

    d25_highs = [v for v in [d2h, d3h, d5h] if v is not None]
    if d1c is not None and d1c <= 0 and d25_highs and max(d25_highs) >= 3:
        return "눌림후재상승형"

    if mae is not None and mfe is not None and mae >= -5 and mfe >= 3:
        return "스윙전환가능형"

    return "실패형"


def _check_failed_structure(entry: dict, r: dict) -> bool:
    """진짜 실패 조건 2개 이상이면 True."""
    count = 0

    d1c      = r.get("d1_close_pct")
    d1_low   = r.get("_d1_low")
    d1_tv    = r.get("_d1_tv")
    d1_open  = r.get("_d1_open")
    d1_close = r.get("_d1_close")

    # 조건 1: D+1 종가 -5% 이하
    if d1c is not None and d1c <= -5:
        count += 1

    # 조건 2: D+1 저가가 기준봉 고가 대비 -8% 이하 (p2/p3/HTC만, base_high_gap_pct 있는 경우)
    base_gap = entry.get("base_high_gap_pct")
    sp = float(entry.get("signal_price") or 0)
    if base_gap is not None and sp > 0 and d1_low is not None and d1_low > 0:
        try:
            base_high = sp / (1 + float(base_gap) / 100)
            if base_high > 0 and (d1_low - base_high) / base_high * 100 <= -8:
                count += 1
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 조건 3: D+1 거래대금 증가 + 음봉
    signal_tv = float(entry.get("signal_tv") or 0)
    if (d1_tv is not None and signal_tv > 0 and d1_tv > signal_tv
            and d1_open is not None and d1_close is not None and d1_close < d1_open):
        count += 1

    return count >= 2


def _check_alive_pullback(r: dict, signal_tv: float, failed: bool) -> bool | None:
    """살아있는 눌림 여부. D+1 데이터 없으면 None."""
    d1c      = r.get("d1_close_pct")
    d1l_pct  = r.get("d1_low_pct")
    d1_tv    = r.get("_d1_tv")
    d1_open  = r.get("_d1_open")
    d1_close = r.get("_d1_close")

    if d1c is None:
        return None
    if failed:
        return False

    # 조건 1: D+1 종가 signal_price 대비 -5% 이내
    if d1c < -5:
        return False

    # 조건 2: D+1 저가 signal_price 대비 -7% 이내
    if d1l_pct is not None and d1l_pct < -7:
        return False

    # 조건 3: D+1 거래대금 300억 이상
    if d1_tv is not None and d1_tv < 30_000_000_000:
        return False

    # 조건 4: 장대음봉 아님 (시가→종가 낙폭 5% 초과)
    if d1_open is not None and d1_close is not None and d1_open > 0:
        if (d1_open - d1_close) / d1_open * 100 > 5:
            return False

    return True


# ── 멀티데이 데이터 entry 보강 ──────────────────────────────────────

def _enrich_entry_with_returns(entry: dict, hist: pd.DataFrame) -> None:
    """entry에 멀티데이 수익률·분류·상태를 in-place 업데이트. 실패 시 조용히 종료."""
    sp = float(entry.get("signal_price") or 0)
    signal_date_str = entry.get("signal_date", "")
    if sp <= 0 or not signal_date_str:
        return

    r = _calc_multiday_returns(hist, sp, signal_date_str)

    # 수익률 필드 업데이트 (None이 아닌 값만 덮어씀)
    _RETURN_FIELDS = [
        "d1_open_pct", "d1_high_pct", "d1_close_pct",
        "d2_high_pct", "d2_close_pct",
        "d3_high_pct", "d3_close_pct",
        "d5_high_pct", "d5_close_pct",
        "mfe", "mae", "mfe_day", "mae_day",
    ]
    for field in _RETURN_FIELDS:
        if r.get(field) is not None:
            entry[field] = r[field]

    # 분류
    failed = _check_failed_structure(entry, r)
    alive  = _check_alive_pullback(r, float(entry.get("signal_tv") or 0), failed)
    entry["failed_structure"]    = failed
    entry["alive_pullback"]      = alive
    entry["interim_result_type"] = _classify_interim_result_type(r)
    entry["final_result_type"]   = _classify_final_result_type(r)  # None if D+5 미확보

    # 섹터 주도성 (D+1 날짜 daily_summary 참조, 없으면 None)
    d1_date = r.get("_d1_date_str")
    if d1_date and entry.get("sector_still_active") is None:
        summary = _load_daily_summary(d1_date.replace(".", "-"))
        if summary is not None:
            sector  = entry.get("sector", "")
            leading = summary.get("leading_sector_names", [])
            entry["sector_still_active"] = bool(sector and sector in leading)


# ── 백필 ────────────────────────────────────────────────────────────

def _backfill_pending_reviews(today: date, exclude_date: str | None = None) -> None:
    """기존 review.json에서 final_result_type=None인 항목을 최신 hist로 업데이트."""
    cutoff = today - timedelta(days=_BACKFILL_DAYS)

    for review_path in sorted(_SIGNALS_DIR.glob("*_review.json")):
        try:
            signal_date_str = review_path.name.replace("_review.json", "")
            signal_date = date.fromisoformat(signal_date_str)
        except ValueError:
            continue

        if signal_date <= cutoff or signal_date >= today:
            continue
        if exclude_date and signal_date_str == exclude_date:
            continue

        try:
            records: list[dict] = json.loads(review_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"백필 로드 실패 {review_path}: {e}")
            continue

        # final 확정된 항목만 있으면 스킵
        pending = [e for e in records if e.get("final_result_type") is None]
        if not pending:
            continue

        updated = False
        for entry in pending:
            code = str(entry.get("code", ""))
            if not code:
                continue
            # signal_date 필드가 없는 구버전 entry 보정
            if not entry.get("signal_date"):
                entry["signal_date"] = signal_date_str

            try:
                hist = fetch_daily_history(code, pages=2)
                time.sleep(REQUEST_DELAY)
                if hist.empty:
                    continue
                _enrich_entry_with_returns(entry, hist)
                updated = True
            except Exception as e:
                logger.warning(f"[{code}] 백필 실패: {e}")

        if updated:
            try:
                review_path.write_text(
                    json.dumps(records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"백필 저장: {review_path} ({len(pending)}개 항목 업데이트)")
            except Exception as e:
                logger.warning(f"백필 저장 실패 {review_path}: {e}")


# ── 메인 ────────────────────────────────────────────────────────────

def run(today: date, kospi_chg_today: float | None) -> list[dict]:
    """
    어제 신호 → D+1 성과 측정 + 멀티데이 백테스트.
    반환: list[dict] — 기존 필드 + 멀티데이 필드 포함.
    """
    signals_df, yesterday_str = _find_yesterday_signals(today)

    if signals_df is None or signals_df.empty:
        logger.info("복기: 어제 시그널 없음")
        try:
            _backfill_pending_reviews(today)
        except Exception as e:
            logger.warning(f"백필 전체 실패 (무시): {e}")
        return []

    today_str = today.strftime("%Y.%m.%d")
    rows      = signals_df.head(_MAX_REVIEW).to_dict("records")
    results   = []

    for row in rows:
        code = str(row.get("종목코드", ""))
        name = str(row.get("종목명", ""))
        sp   = row.get("signal_price")

        entry: dict = {
            # ── 기존 필드 ─────────────────────────────
            "code":         code,
            "name":         name,
            "signal_price": sp,
            "t1_open":      None,
            "t1_close":     None,
            "gap_pct":      None,
            "hold_pct":     None,
            "result":       "pending",
            "fail_reason":  None,
            "pattern_type": str(row.get("pattern_type_label", "")),
            "sector":       str(row.get("sector", "")),
            "total_score":  int(row.get("total_score") or 0),
            # ── 멀티데이용 메타 ────────────────────────
            "signal_date":      yesterday_str,
            "signal_tv":        float(row.get("거래대금") or 0),
            "in_inter":         bool(row.get("in_inter", False)),
            "base_high_gap_pct": row.get("base_high_gap_pct"),
            # ── 멀티데이 수익률 ─────────────────────────
            "d1_open_pct":  None,
            "d1_high_pct":  None,
            "d1_close_pct": None,
            "d2_high_pct":  None,
            "d2_close_pct": None,
            "d3_high_pct":  None,
            "d3_close_pct": None,
            "d5_high_pct":  None,
            "d5_close_pct": None,
            "mfe":          None,
            "mae":          None,
            "mfe_day":      None,
            "mae_day":      None,
            # ── 분류 ───────────────────────────────────
            "interim_result_type": "pending",
            "final_result_type":   None,
            "alive_pullback":      None,
            "failed_structure":    None,
            "sector_still_active": None,
        }

        try:
            hist = fetch_daily_history(code, pages=2)
            time.sleep(REQUEST_DELAY)

            if hist.empty:
                results.append(entry)
                continue

            # ── 기존 D+1 갭/홀드 계산 ──────────────────
            today_rows = hist[hist["date"] == today_str]
            if today_rows.empty:
                results.append(entry)
                continue

            t1       = today_rows.iloc[0]
            t1_open  = float(t1.get("open")  or 0)
            t1_close = float(t1.get("close") or 0)

            entry_price = float(sp) if sp and float(sp) > 0 else 0.0
            if entry_price <= 0:
                prev_rows = hist[hist["date"] != today_str]
                if prev_rows.empty:
                    results.append(entry)
                    continue
                entry_price = float(prev_rows.iloc[0]["close"] or 0)

            if entry_price <= 0 or t1_open <= 0:
                results.append(entry)
                continue

            gap_pct  = (t1_open  / entry_price - 1) * 100
            hold_pct = (t1_close / entry_price - 1) * 100 if t1_close > 0 else None

            entry.update({
                "t1_open":     t1_open,
                "t1_close":    t1_close if t1_close > 0 else None,
                "gap_pct":     round(gap_pct, 2),
                "hold_pct":    round(hold_pct, 2) if hold_pct is not None else None,
                "result":      "성공" if gap_pct >= 0 else "실패",
                "fail_reason": _classify_fail_reason(gap_pct, kospi_chg_today),
            })

            # ── 멀티데이 수익률 + 분류 ──────────────────
            _enrich_entry_with_returns(entry, hist)

            # sector_still_active: D+1=today 기준 daily_summary도 확인
            if entry.get("sector_still_active") is None:
                summary = _load_daily_summary(today.isoformat())
                if summary is not None:
                    sector  = entry.get("sector", "")
                    leading = summary.get("leading_sector_names", [])
                    if sector:
                        entry["sector_still_active"] = sector in leading

        except Exception as e:
            logger.warning(f"[{code}] 복기 수집 실패: {e}")

        results.append(entry)

    success_n = sum(1 for r in results if r.get("result") == "성공")
    total_n   = sum(1 for r in results if r.get("result") in ("성공", "실패"))
    logger.info(f"복기 완료: {success_n}/{total_n} 성공")

    if yesterday_str and results:
        out_path = _SIGNALS_DIR / f"{yesterday_str}_review.json"
        try:
            out_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"복기 결과 저장: {out_path}")
        except Exception as e:
            logger.warning(f"복기 결과 저장 실패: {e}")

    # 백필은 저장 완료 후 실행 (exclude_date=yesterday_str로 중복 방지)
    try:
        _backfill_pending_reviews(today, exclude_date=yesterday_str)
    except Exception as e:
        logger.warning(f"백필 전체 실패 (무시): {e}")

    return results
