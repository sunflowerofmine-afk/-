# scripts/backfill_tv_concentration.py
"""거래대금 Top5 집중도 백필 — 커밋된 리포트 HTML 파싱.

일별로 [Top5 거래대금 합 / (코스피+코스닥 전체 거래대금)] 과 Top5 섹터 구성을 산출.
같은 날짜에 1750(2차) 리포트가 있으면 우선, 없으면 1450(1차) 사용.

실행: python -m scripts.backfill_tv_concentration
출력: data/tv_concentration.csv + 콘솔 요약
"""

import csv
import re
import sys
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
OUT_CSV = Path(__file__).resolve().parent.parent / "data" / "tv_concentration.csv"

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})\.html$")


def _parse_report(path: Path):
    html = path.read_text(encoding="utf-8")
    mk = re.search(r"코스피 ([\d,]+)억", html)
    md = re.search(r"코스닥 ([\d,]+)억", html)
    if not mk or not md:
        return None
    total = int(mk.group(1).replace(",", "")) + int(md.group(1).replace(",", ""))
    idx = html.find("💰 거래대금 Top20")
    if idx < 0:
        idx = html.find("거래대금 Top20</")  # 구버전 섹션 타이틀 fallback
    if idx < 0:
        return None
    tbl_end = html.find("</table>", idx)
    seg = html[idx:tbl_end]
    rows = re.findall(r"<tr>(.*?)</tr>", seg, re.S)
    stocks = []
    for r in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(cells) < 7:
            continue
        name = re.sub(r"<[^>]+>", "", cells[1]).strip()
        sector = re.sub(r"<[^>]+>", "", cells[2]).strip()
        market = re.sub(r"<[^>]+>", "", cells[4]).strip()
        mtv = re.search(r"([\d,]+)억", cells[5])
        if not mtv:
            continue
        stocks.append({"name": name, "sector": sector, "market": market,
                       "tv_eok": int(mtv.group(1).replace(",", ""))})
    if len(stocks) < 5:
        return None
    top5 = stocks[:5]
    top5_sum = sum(s["tv_eok"] for s in top5)
    return {
        "total_tv_eok": total,
        "top5_tv_eok": top5_sum,
        "top5_ratio_pct": round(top5_sum / total * 100, 2),
        "top5_names": "|".join(s["name"] for s in top5),
        "top5_sectors": "|".join(s["sector"] or "?" for s in top5),
    }


def main():
    # 날짜별 최적 스냅샷 선택 (1750 우선)
    by_date = {}
    for f in sorted(REPORTS_DIR.glob("*.html")):
        m = DATE_RE.match(f.name)
        if not m:
            continue
        date, snap = m.group(1), m.group(2)
        cur = by_date.get(date)
        if cur is None or (snap == "1750") or (cur[1] not in ("1750",) and snap > cur[1]):
            if cur is not None and cur[1] == "1750":
                continue
            by_date[date] = (f, snap)

    out_rows = []
    for date in sorted(by_date):
        f, snap = by_date[date]
        try:
            r = _parse_report(f)
        except Exception as e:
            print(f"{date} parse error: {e}", file=sys.stderr)
            continue
        if r is None:
            continue
        r["date"] = date
        r["snapshot"] = snap
        out_rows.append(r)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "snapshot", "total_tv_eok",
                                           "top5_tv_eok", "top5_ratio_pct",
                                           "top5_names", "top5_sectors"])
        w.writeheader()
        w.writerows(out_rows)

    print(f"saved {len(out_rows)} days -> {OUT_CSV}")
    print(f"{'date':<12}{'snap':<6}{'top5%':>7}  top5")
    for r in out_rows:
        print(f"{r['date']:<12}{r['snapshot']:<6}{r['top5_ratio_pct']:>7}  {r['top5_names']}")


if __name__ == "__main__":
    main()
