"""장중 흐름 모양 확인 (네이버 fchart 1분봉, 정규장 09:00-15:30).

"투매·속도감·장대 음봉·공포" 같은 장중 조건은 일봉으로 판정할 수 없다.
하루 하락폭이 커도 대부분 시가 갭일 수 있다(2026-11-25 SK하이닉스: 갭 약 -6% + 장중 흘러내림 약 -5%).
그런 말을 쓰기 전에 이 스크립트로 갭 비중·최대 낙폭·거래량 분포를 먼저 본다.

사용: python scripts/intraday_shape.py 000660 [YYYYMMDD]
  날짜 생략 시 1분봉 자료의 마지막 거래일.
  fchart 1분봉은 최근 약 10거래일만 준다(지난 날짜는 그 안에서만).
  전일 종가 = 전 거래일 15:30 분봉(동시호가 체결가). 거래량은 누적값이라 차분한다.
"""
import re
import sys
import urllib.request
from collections import defaultdict


def fetch(code: str):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=minute&count=4000&requestType=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    txt = urllib.request.urlopen(req, timeout=15).read().decode("euc-kr", "ignore")
    name = re.search(r'name="([^"]*)"', txt)
    rows = []
    for d in re.findall(r'data="(\d{12}\|[^"]*)"', txt):
        f = d.split("|")
        if f[4] in ("null", "") or f[5] in ("null", ""):
            continue
        rows.append((f[0][:8], f[0][8:], int(f[4]), int(f[5])))
    return (name.group(1) if name else code), rows


def main():
    code = sys.argv[1]
    name, rows = fetch(code)
    days = sorted({r[0] for r in rows})
    day = sys.argv[2] if len(sys.argv) > 2 else days[-1]
    if day not in days:
        print(f"{day} 분봉 없음. 가능한 날짜: {days[0]}부터 {days[-1]}")
        return 1
    prev_days = [x for x in days if x < day]
    d = [r for r in rows if r[0] == day and "0900" <= r[1] <= "1530"]
    prev_close = None
    if prev_days:
        p = [r for r in rows if r[0] == prev_days[-1] and r[1] <= "1530"]
        prev_close = p[-1][2] if p else None
    vol = [d[0][3]] + [d[i][3] - d[i - 1][3] for i in range(1, len(d))]
    tot = sum(vol) or 1
    o, c = d[0][2], d[-1][2]
    hi = max(d, key=lambda r: r[2]); lo = min(d, key=lambda r: r[2])
    print(f"{name}({code}) {day} 정규장 1분봉 {len(d)}개")
    if prev_close:
        print(f"전일 종가 {prev_close:,} → 시가 {o:,}  갭 {(o/prev_close-1)*100:+.2f}%")
        print(f"하루 등락 {(c/prev_close-1)*100:+.2f}% = 갭 {(o/prev_close-1)*100:+.2f}% + 장중(시가→종가) {(c/o-1)*100:+.2f}%")
    print(f"고가 {hi[2]:,}({hi[1]})  저가 {lo[2]:,}({lo[1]})  종가 {c:,}")
    one = sorted(((d[i][1], d[i][2]/d[i-1][2]-1, vol[i]) for i in range(1, len(d)) if d[i][1] != "1530"), key=lambda x: x[1])
    print("1분 최대 낙폭: " + ", ".join(f"{t} {r*100:+.2f}%(거래량 {v:,})" for t, r, v in one[:3]))
    w = sorted(((d[i][1], d[i+10][2]/d[i][2]-1) for i in range(len(d) - 11)), key=lambda x: x[1])
    print("10분 최대 낙폭: " + ", ".join(f"{t}부터 {r*100:+.2f}%" for t, r in w[:3]))
    h = defaultdict(int)
    for r, v in zip(d, vol):
        h[r[1][:2]] += v
    print("시간대별 거래량 비중(15시는 15:30 동시호가 포함): " + " / ".join(f"{k}시 {v/tot*100:.0f}%" for k, v in sorted(h.items())))
    avg = tot / len(d)
    spikes = [x for x in one if x[1] <= -0.01 and x[2] >= 3 * avg]
    print(f"거래량 3배 이상 동반 1분 -1% 이하 봉: {len(spikes)}개" + (" " + ", ".join(f"{t} {r*100:+.2f}%" for t, r, _ in spikes[:5]) if spikes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
