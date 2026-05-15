# 1450 리포트 기반 종베 시뮬레이션 백테스트
# 각 날짜의 1450 HTML에서 핵심 후보(lc-code)를 추출
# 네이버 일별시세에서 당일 종가 및 익일 종가를 가져와 수익률 계산

import re
import sys
import time
from pathlib import Path
from datetime import date

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY

REPORTS_DIR = Path(__file__).parent.parent / "reports"
OUTPUT_FILE = Path(r"C:\Users\purpl\Desktop\backtest_result.txt")

SISE_DAY_URL = "https://finance.naver.com/item/sise_day.naver"

# 1450 리포트 → (매수일, 매도일) 매핑
# 종베: 당일 1450 종가 매수, 익일 종가 매도
REPORT_DATES = [
    ("2026-04-21_1450.html", "2026.04.21", "2026.04.22"),
    ("2026-04-22_1450.html", "2026.04.22", "2026.04.23"),
    ("2026-04-23_1450.html", "2026.04.23", "2026.04.24"),
    ("2026-04-27_1450.html", "2026.04.27", "2026.04.28"),
    ("2026-04-28_1450.html", "2026.04.28", "2026.04.29"),
    ("2026-04-29_1450.html", "2026.04.29", "2026.04.30"),
    ("2026-04-30_1450.html", "2026.04.30", "2026.05.04"),  # 5/1 근로자의 날 KRX 휴장
    ("2026-05-04_1450.html", "2026.05.04", "2026.05.06"),  # 5/5 어린이날 KRX 휴장
]


def extract_candidates(html_path: Path) -> list[tuple[str, str]]:
    """lc-code / lc-name 쌍을 추출 (핵심 후보만)."""
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    codes = re.findall(r'lc-code[^>]*>([0-9]{6})<', html)
    names = [n.strip() for n in re.findall(r'lc-name[^>]*>([^<]+)<', html) if n.strip()]
    return list(zip(codes, names))


def fetch_close_prices(code: str, target_dates: set[str]) -> dict[str, int]:
    """
    네이버 일별시세 3페이지(≈30일)를 읽어 target_dates에 해당하는 종가 반환.
    {날짜str: 종가int}
    """
    result = {}
    for page in range(1, 4):
        url = f"{SISE_DAY_URL}?code={code}&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.select_one("table.type2")
            if table is None:
                break
            for tr in table.select("tr"):
                cols = tr.select("td")
                if len(cols) < 7:
                    continue
                d = cols[0].text.strip()
                if not re.match(r"\d{4}\.\d{2}\.\d{2}", d):
                    continue
                if d in target_dates:
                    close_str = cols[1].text.strip().replace(",", "")
                    if close_str.isdigit():
                        result[d] = int(close_str)
            if len(result) == len(target_dates):
                break
        except Exception:
            break
        time.sleep(REQUEST_DELAY)
    return result


def main():
    lines = []
    lines.append("=" * 60)
    lines.append("1450 리포트 종베 시뮬레이션 백테스트")
    lines.append(f"분석 날짜: {date.today()}")
    lines.append("전략: 1450 핵심후보 당일종가 매수 → 익일종가 매도")
    lines.append("=" * 60)

    all_results = []

    for report_file, buy_date, sell_date in REPORT_DATES:
        html_path = REPORTS_DIR / report_file
        if not html_path.exists():
            lines.append(f"\n[{buy_date}] 리포트 없음: {report_file}")
            continue

        candidates = extract_candidates(html_path)
        if not candidates:
            lines.append(f"\n[{buy_date}] 후보 없음")
            continue

        lines.append(f"\n{'─'*60}")
        lines.append(f"[{buy_date} → {sell_date}] 후보 {len(candidates)}종목")
        lines.append(f"{'─'*60}")

        day_returns = []
        for code, name in candidates:
            prices = fetch_close_prices(code, {buy_date, sell_date})
            buy_p = prices.get(buy_date)
            sell_p = prices.get(sell_date)

            if buy_p and sell_p:
                ret = (sell_p / buy_p - 1) * 100
                status = f"매수:{buy_p:,}  매도:{sell_p:,}  수익률:{ret:+.2f}%"
                day_returns.append(ret)
                all_results.append((buy_date, code, name, ret))
            elif buy_p and not sell_p:
                status = f"매수:{buy_p:,}  매도:데이터없음"
            else:
                status = "가격 조회 실패"

            lines.append(f"  {code} {name}: {status}")
            time.sleep(REQUEST_DELAY)

        if day_returns:
            avg = sum(day_returns) / len(day_returns)
            win = sum(1 for r in day_returns if r > 0)
            lines.append(f"  → 평균 수익률: {avg:+.2f}%  승률: {win}/{len(day_returns)}")

    # 전체 요약
    lines.append(f"\n{'='*60}")
    lines.append("전체 요약")
    lines.append(f"{'='*60}")
    if all_results:
        all_rets = [r for _, _, _, r in all_results]
        avg_all = sum(all_rets) / len(all_rets)
        win_all = sum(1 for r in all_rets if r > 0)
        best = max(all_results, key=lambda x: x[3])
        worst = min(all_results, key=lambda x: x[3])
        lines.append(f"총 분석 건수: {len(all_results)}건")
        lines.append(f"전체 평균 수익률: {avg_all:+.2f}%")
        lines.append(f"전체 승률: {win_all}/{len(all_results)} ({win_all/len(all_results)*100:.0f}%)")
        lines.append(f"최고: {best[1]} {best[2]} {best[3]:+.2f}% ({best[0]})")
        lines.append(f"최저: {worst[1]} {worst[2]} {worst[3]:+.2f}% ({worst[0]})")

        # 날짜별 요약
        lines.append(f"\n날짜별 평균 수익률:")
        from collections import defaultdict
        by_date = defaultdict(list)
        for d, _, _, r in all_results:
            by_date[d].append(r)
        for d in sorted(by_date):
            rs = by_date[d]
            lines.append(f"  {d}: {sum(rs)/len(rs):+.2f}%  ({len(rs)}종목)")
    else:
        lines.append("데이터 없음")

    output = "\n".join(lines)
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"결과 저장: {OUTPUT_FILE}")
    print("완료")


if __name__ == "__main__":
    main()
