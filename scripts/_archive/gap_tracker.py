# 교집합 종목 익일 09:30 갭업 결과 자동 기록
# 실행: python -m scripts.gap_tracker
#   --date YYYYMMDD  : 특정 날짜만 처리 (기본: 오늘)
#   --all            : reports/ 전체 날짜 일괄 처리 (과거 누적용)
#
# 저장: data/gap_results/YYYYMMDD.csv
# 누적: data/gap_results/all.csv (append)

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY
from scripts.market_calendar import get_next_trading_day

REPORTS_DIR  = Path(__file__).parent.parent / "reports"
GAP_DIR      = Path(__file__).parent.parent / "data" / "gap_results"
GAP_ALL_FILE = GAP_DIR / "all.csv"

FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"

CSV_COLS = [
    "entry_date", "exit_date", "code", "name",
    "change_pct",           # 진입일 당일 등락률
    "in_inter",             # 교집합 여부 (True/False)
    "entry_price",          # 진입일 종가
    "exit_open",            # 익일 시가
    "exit_0930",            # 익일 09:30 분봉 종가 (없으면 시가)
    "exit_basis",           # "0930" | "open"
    "return_pct",           # (exit_0930 / entry_price - 1) * 100
    "win",                  # True/False
    "kospi_chg",            # 익일 KOSPI 등락률
    "kosdaq_chg",           # 익일 KOSDAQ 등락률
]


# ── 데이터 수집 ─────────────────────────────────────────────

def _fchart_day(code: str, count: int = 30) -> list[tuple]:
    """(date8, open, close) 리스트."""
    url = f"{FCHART_URL}?symbol={code}&timeframe=day&count={count}&requestType=0"
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    return re.findall(r'<item data="(\d{8})\|(\d+)\|[^|]+\|[^|]+\|(\d+)\|', r.text)


def _fchart_minute(code: str, count: int = 2500) -> list[tuple]:
    """(date8, time4, close) 리스트."""
    url = f"{FCHART_URL}?symbol={code}&timeframe=minute&count={count}&requestType=0"
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    return re.findall(r'<item data="(\d{8})(\d{4})\|[^|]*\|[^|]*\|[^|]*\|(\d+)\|', r.text)


def get_day_prices(code: str, date8: str) -> tuple[int | None, int | None]:
    """(open, close) for date8."""
    for d, o, c in _fchart_day(code, 30):
        if d == date8:
            return int(o), int(c)
    return None, None


def get_0930_price(code: str, date8: str) -> tuple[int | None, str]:
    """익일 09:30 근처 분봉 종가. 없으면 (None, '')."""
    items = _fchart_minute(code)
    for d, t, c in items:
        if d == date8 and "0928" <= t <= "0932":
            return int(c), t
    return None, ""


def get_index_chg(symbol: str, date8: str) -> float | None:
    """KOSPI / KOSDAQ 당일 등락률."""
    items = re.findall(
        r'<item data="(\d{8})\|([0-9.]+)\|[^|]+\|[^|]+\|([0-9.]+)\|',
        requests.get(
            f"{FCHART_URL}?symbol={symbol}&timeframe=day&count=30&requestType=0",
            headers=HEADERS, timeout=REQUEST_TIMEOUT,
        ).text,
    )
    for i, (d, o, c) in enumerate(items):
        if d == date8 and i > 0:
            prev = float(items[i - 1][2])
            return round((float(c) - prev) / prev * 100, 2)
    return None


# ── HTML 파싱 ────────────────────────────────────────────────

def extract_inter_candidates(html_path: Path) -> list[dict]:
    """교집합(★교집합) 포함 여부 포함한 후보 목록."""
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # list-card 단위로 파싱
    cards = re.findall(
        r'lc-name[^>]*>([^<]+)</span><span class="lc-code">(\d{6})</span>'
        r'.*?lc-stats[^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    results = []
    for name, code, stats_html in cards:
        in_inter = "★교집합" in stats_html
        chg_m = re.search(r'([+-]?\d+\.?\d*)%', stats_html)
        chg = float(chg_m.group(1)) if chg_m else None
        results.append({"code": code, "name": name.strip(), "change_pct": chg, "in_inter": in_inter})
    return results


def pick_report(entry_date8: str) -> Path | None:
    """1750 우선, 없으면 1450."""
    d = f"{entry_date8[:4]}-{entry_date8[4:6]}-{entry_date8[6:]}"
    for suffix in ("1750", "1829", "1450"):
        p = REPORTS_DIR / f"{d}_{suffix}.html"
        if p.exists():
            return p
    return None


# ── 핵심 처리 ────────────────────────────────────────────────

def process_date(entry_date8: str) -> list[dict]:
    """한 날짜 처리 → 결과 row 리스트."""
    report = pick_report(entry_date8)
    if report is None:
        print(f"[{entry_date8}] 리포트 없음 — 스킵")
        return []

    candidates = extract_inter_candidates(report)
    if not candidates:
        print(f"[{entry_date8}] 후보 없음 — 스킵")
        return []

    exit_date8 = get_next_trading_day(entry_date8)
    if exit_date8 is None:
        print(f"[{entry_date8}] 다음 거래일 계산 실패")
        return []

    # 인덱스 등락률 (1회 조회)
    kospi_chg  = get_index_chg("KOSPI",  exit_date8)
    time.sleep(REQUEST_DELAY)
    kosdaq_chg = get_index_chg("KOSDAQ", exit_date8)
    time.sleep(REQUEST_DELAY)

    rows = []
    for c in candidates:
        code = c["code"]

        # 진입가 (당일 종가)
        _, entry_price = get_day_prices(code, entry_date8)
        time.sleep(REQUEST_DELAY)

        # 익일 시가 + 09:30
        exit_open, exit_close = get_day_prices(code, exit_date8)
        time.sleep(REQUEST_DELAY)
        exit_0930, t_label   = get_0930_price(code, exit_date8)
        time.sleep(REQUEST_DELAY)

        # 09:30 우선, 없으면 시가
        if exit_0930:
            exit_price = exit_0930
            basis = f"0930({t_label})"
        elif exit_open:
            exit_price = exit_open
            basis = "open"
        else:
            exit_price = None
            basis = "N/A"

        if entry_price and exit_price:
            ret = round((exit_price / entry_price - 1) * 100, 2)
            win = ret > 0
        else:
            ret = win = None

        rows.append({
            "entry_date":  entry_date8,
            "exit_date":   exit_date8,
            "code":        code,
            "name":        c["name"],
            "change_pct":  c["change_pct"],
            "in_inter":    c["in_inter"],
            "entry_price": entry_price,
            "exit_open":   exit_open,
            "exit_0930":   exit_0930,
            "exit_basis":  basis,
            "return_pct":  ret,
            "win":         win,
            "kospi_chg":   kospi_chg,
            "kosdaq_chg":  kosdaq_chg,
        })
        inter_tag = "★" if c["in_inter"] else " "
        ret_s = f"{ret:+.2f}%" if ret is not None else "N/A"
        ep_s  = f"{entry_price:,}" if entry_price else "N/A"
        ex_s  = f"{exit_price:,}" if exit_price else "N/A"
        print(f"  {inter_tag} {c['name'][:12]:<12} ({code})  {ep_s} -> {basis}:{ex_s}  {ret_s}")

    return rows


def save_rows(rows: list[dict]) -> None:
    GAP_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    # 날짜별 파일
    for entry_date in set(r["entry_date"] for r in rows):
        day_rows = [r for r in rows if r["entry_date"] == entry_date]
        day_file = GAP_DIR / f"{entry_date}.csv"
        pd.DataFrame(day_rows, columns=CSV_COLS).to_csv(day_file, index=False, encoding="utf-8-sig")
        print(f"저장: {day_file}")

    # all.csv 누적
    new_df = pd.DataFrame(rows, columns=CSV_COLS)
    if GAP_ALL_FILE.exists():
        existing = pd.read_csv(GAP_ALL_FILE, dtype=str)
        key = ["entry_date", "code"]
        existing = existing[~existing.set_index(key).index.isin(new_df.set_index(key).index)]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(GAP_ALL_FILE, index=False, encoding="utf-8-sig")
    print(f"누적 파일 갱신: {GAP_ALL_FILE}  (총 {len(combined)}행)")


# ── 진입점 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="교집합 갭업 기록")
    parser.add_argument("--date", help="YYYYMMDD (기본: 오늘)")
    parser.add_argument("--all",  action="store_true", help="reports/ 전체 일괄 처리")
    args = parser.parse_args()

    if args.all:
        # reports/에서 1750 또는 1450 파일 날짜 전체 수집
        dates = sorted({
            re.sub(r"-", "", m.group(1))
            for f in REPORTS_DIR.iterdir()
            if (m := re.match(r"(\d{4}-\d{2}-\d{2})_(1750|1829|1450)\.html", f.name))
        })
        # 이미 처리된 날짜 스킵
        done = set()
        if GAP_ALL_FILE.exists():
            done = set(pd.read_csv(GAP_ALL_FILE, usecols=["entry_date"], dtype=str)["entry_date"])
        dates = [d for d in dates if d not in done]
        print(f"처리할 날짜 {len(dates)}개")
    else:
        target = args.date or datetime.today().strftime("%Y%m%d")
        dates = [target]

    all_rows = []
    for d in dates:
        print(f"\n[{d}]")
        rows = process_date(d)
        all_rows.extend(rows)

    save_rows(all_rows)
    print("\n완료")


if __name__ == "__main__":
    main()
