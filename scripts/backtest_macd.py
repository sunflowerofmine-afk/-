#!/usr/bin/env python3
# scripts/backtest_macd.py
"""
MACD 백테스트 — 네이버 증권 일봉 기반
전략 3종 비교:
  A. 기본 골든크로스 (단순 교차)
  B. 0선 위 재골든크로스
  C. 0선 위 + 20일선 지지 (문서 권장 전략)

실행: python -m scripts.backtest_macd
"""

import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 설정 ────────────────────────────────────────────────────────────
UNIVERSE_SIZE  = 100    # 거래대금 상위 N개 종목
MAX_PAGES      = 55     # 1페이지 ≈ 10거래일, 55 ≈ 2.2년
HOLD_DAYS      = 5      # 보유 기간 (거래일)
FETCH_DELAY    = 0.4    # 요청 간격 (초)
CACHE_DIR      = Path("data/backtest_cache")
OUTPUT_PATH    = Path("reports/backtest_macd_result.json")

MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
MA_PERIOD      = 20


# ── 유니버스 구성 ────────────────────────────────────────────────────

def get_universe() -> list[dict]:
    """data/raw/ CSV에서 거래대금 상위 UNIVERSE_SIZE 종목 추출"""
    raw_dir = Path("data/raw")
    if not raw_dir.exists():
        logger.error("data/raw 없음")
        return []

    files = sorted(raw_dir.glob("*.csv"))[-20:]  # 최근 20개 파일
    tv_map: dict[str, float] = {}
    name_map: dict[str, str] = {}

    for f in files:
        try:
            with open(f, encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    code = str(row.get("종목코드", "")).strip().zfill(6)
                    name = str(row.get("종목명", "")).strip()
                    try:
                        tv = float(str(row.get("거래대금", 0)).replace(",", "") or 0)
                    except ValueError:
                        tv = 0.0
                    if code and len(code) == 6:
                        tv_map[code] = tv_map.get(code, 0) + tv
                        name_map[code] = name
        except Exception as e:
            logger.warning(f"CSV 로드 실패 {f.name}: {e}")

    sorted_stocks = sorted(tv_map.items(), key=lambda x: -x[1])
    result = [
        {"code": c, "name": name_map.get(c, ""), "tv_sum": v}
        for c, v in sorted_stocks[:UNIVERSE_SIZE]
    ]
    logger.info(f"유니버스 구성: {len(result)}개")
    return result


# ── 일봉 데이터 수집 ─────────────────────────────────────────────────

def fetch_ohlcv(code: str) -> pd.DataFrame:
    """네이버 sise_day.naver에서 일봉 수집 (캐시 우선)"""
    cache_path = CACHE_DIR / f"{code}.csv"
    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, parse_dates=["date"])
            logger.info(f"[{code}] 캐시 로드 ({len(df)}행)")
            return df
        except Exception:
            pass

    rows = []
    for page in range(1, MAX_PAGES + 1):
        try:
            url = (
                f"https://finance.naver.com/item/sise_day.naver"
                f"?code={code}&page={page}"
            )
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")

            page_rows = 0
            for tr in soup.select("table.type2 tr"):
                tds = tr.select("td")
                if len(tds) < 7:
                    continue
                date_text = tds[0].text.strip()
                if not date_text or "." not in date_text:
                    continue

                def _n(t: str):
                    t = t.strip().replace(",", "")
                    try:
                        return float(t) if t else None
                    except ValueError:
                        return None

                close  = _n(tds[1].text)
                open_  = _n(tds[3].text)
                high   = _n(tds[4].text)
                low    = _n(tds[5].text)
                volume = _n(tds[6].text)

                if close is None:
                    continue

                rows.append({
                    "date":   date_text,
                    "open":   open_,
                    "high":   high,
                    "low":    low,
                    "close":  close,
                    "volume": volume,
                })
                page_rows += 1

            if page_rows == 0:
                break
            time.sleep(FETCH_DELAY)

        except Exception as e:
            logger.warning(f"[{code}] page={page} 실패: {e}")
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d")
    df = df.sort_values("date").reset_index(drop=True)
    df = df.dropna(subset=["close"])

    # 캐시 저장
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False, encoding="utf-8-sig")
    logger.info(f"[{code}] 수집 완료 ({len(df)}행) → 캐시 저장")
    return df


# ── 지표 계산 ────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ema_fast     = df["close"].ewm(span=MACD_FAST,   adjust=False).mean()
    ema_slow     = df["close"].ewm(span=MACD_SLOW,   adjust=False).mean()
    df["macd"]   = ema_fast - ema_slow
    df["signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["ma20"]   = df["close"].rolling(MA_PERIOD).mean()
    return df


# ── 매수 신호 탐지 ───────────────────────────────────────────────────

def find_signals(df: pd.DataFrame, strategy: str) -> list[int]:
    """
    strategy:
      "A" — 기본 골든크로스
      "B" — 0선 위 재골든크로스
      "C" — 0선 위 + 20일선 지지 (문서 권장)
    """
    indices = []
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        if pd.isna(curr["macd"]) or pd.isna(curr["signal"]) or pd.isna(curr["ma20"]):
            continue

        # 골든크로스 판정
        golden = (prev["macd"] < prev["signal"]) and (curr["macd"] >= curr["signal"])
        if not golden:
            continue

        if strategy == "A":
            indices.append(i)
        elif strategy == "B":
            if curr["macd"] > 0:
                indices.append(i)
        elif strategy == "C":
            if curr["macd"] > 0 and curr["close"] >= curr["ma20"]:
                indices.append(i)

    return indices


# ── 매매 시뮬레이션 ──────────────────────────────────────────────────

def simulate_trades(df: pd.DataFrame, signal_indices: list[int]) -> list[dict]:
    trades = []
    n = len(df)
    blocked_until = -1  # 동일 종목 중복 진입 방지

    for idx in signal_indices:
        if idx <= blocked_until:
            continue
        exit_idx = min(idx + HOLD_DAYS, n - 1)

        entry_price = df.iloc[idx]["close"]
        exit_price  = df.iloc[exit_idx]["close"]

        if entry_price <= 0:
            continue

        pct = (exit_price - entry_price) / entry_price * 100
        # 거래세 0.18% + 수수료 0.015% × 2 = 약 0.21% 왕복 비용 반영
        pct -= 0.21

        trades.append({
            "entry_date":  str(df.iloc[idx]["date"].date()),
            "exit_date":   str(df.iloc[exit_idx]["date"].date()),
            "entry_price": round(entry_price, 0),
            "exit_price":  round(exit_price, 0),
            "pct":         round(pct, 2),
            "win":         pct > 0,
        })
        blocked_until = exit_idx

    return trades


# ── 통계 계산 ────────────────────────────────────────────────────────

def calc_stats(all_trades: list[dict]) -> dict:
    """
    다종목 집계 통계.
    CAGR/MDD는 '1종목 1거래당 고정 1단위 투자' 기준으로 산출.
    (전체 자산 복리 방식이 아님 — 다종목 백테스트에선 의미 없음)
    """
    if not all_trades:
        return {"total_trades": 0}

    n    = len(all_trades)
    wins = sum(1 for t in all_trades if t["win"])
    pcts = [t["pct"] for t in all_trades]

    # 손익비 (Profit Factor)
    gross_win  = sum(p for p in pcts if p > 0)
    gross_loss = abs(sum(p for p in pcts if p < 0))
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    # 기간 계산
    try:
        d1    = datetime.strptime(all_trades[0]["entry_date"],  "%Y-%m-%d")
        d2    = datetime.strptime(all_trades[-1]["exit_date"],  "%Y-%m-%d")
        years = max((d2 - d1).days / 365, 0.1)
    except Exception:
        years = 1.0

    # 연간 거래 횟수 (평균 보유일 = HOLD_DAYS 거래일 ≈ HOLD_DAYS × 1.4 역일)
    trades_per_year = round(n / years, 1)

    # 단순 연환산 기대수익 = avg_pct × 연간거래수
    # (동일 자금을 순차 재투자할 때의 근사값 — 겹치는 거래 무시)
    avg_pct = sum(pcts) / n
    annual_return_simple = round(avg_pct * trades_per_year, 1)

    pcts_sorted = sorted(pcts)
    med = pcts_sorted[n // 2]

    return {
        "total_trades":         n,
        "win_rate":             round(wins / n * 100, 1),
        "avg_pct":              round(avg_pct, 2),
        "median_pct":           round(med, 2),
        "profit_factor":        pf,
        "trades_per_year":      trades_per_year,
        "annual_return_simple": annual_return_simple,
        "note": "annual_return_simple = avg_pct × 연간거래수 (단일종목 순차투자 근사값)",
    }


# ── 메인 ─────────────────────────────────────────────────────────────

def run():
    universe = get_universe()
    if not universe:
        logger.error("유니버스 없음 — 종료")
        sys.exit(1)

    all_trades: dict[str, list] = {"A": [], "B": [], "C": []}
    total = len(universe)

    for i, stock in enumerate(universe, 1):
        code = stock["code"]
        name = stock["name"]
        logger.info(f"[{i}/{total}] {name}({code}) 수집 중...")

        df = fetch_ohlcv(code)
        if df.empty or len(df) < MACD_SLOW + MACD_SIGNAL + 5:
            logger.warning(f"[{code}] 데이터 부족, 스킵")
            continue

        df = add_indicators(df)

        for strat in ("A", "B", "C"):
            sig = find_signals(df, strat)
            trades = simulate_trades(df, sig)
            all_trades[strat].extend(trades)

    # 날짜 기준 정렬
    for strat in ("A", "B", "C"):
        all_trades[strat].sort(key=lambda x: x["entry_date"])

    results = {}
    for strat in ("A", "B", "C"):
        stats = calc_stats(all_trades[strat])
        results[strat] = stats

    # 전략명 레이블 추가
    labels = {
        "A": "기본 골든크로스",
        "B": "0선 위 재골든크로스",
        "C": "0선 위 + 20일선 지지",
    }

    # 출력
    print("\n" + "=" * 60)
    print(f"MACD 백테스트 결과  (유니버스 {total}개 / 보유 {HOLD_DAYS}일 / 비용 0.21% 반영)")
    print("=" * 60)
    for strat in ("A", "B", "C"):
        s = results[strat]
        print(f"\n[전략 {strat}] {labels[strat]}")
        print(f"  총 거래수: {s.get('total_trades', 0)}")
        if s.get("total_trades", 0) > 0:
            print(f"  승률:             {s['win_rate']}%")
            print(f"  평균 수익:        {s['avg_pct']}%  (중앙값 {s['median_pct']}%)")
            print(f"  손익비(PF):       {s.get('profit_factor', '-')}")
            print(f"  연간거래수:       {s.get('trades_per_year', '-')}회/년 (전체 종목 합산)")
            print(f"  단순 연환산 수익: {s.get('annual_return_simple', '-')}%  ※ 근사값")
    print("=" * 60)

    # JSON 저장
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "run_date":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe":    total,
        "hold_days":   HOLD_DAYS,
        "cost_pct":    0.21,
        "strategies":  {strat: {"label": labels[strat], **results[strat]} for strat in ("A", "B", "C")},
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"결과 저장: {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
