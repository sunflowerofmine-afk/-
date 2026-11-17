# scripts/fetch_morning_nxt.py
"""
아침 NXT 프리장 가격 수집 — 매일 08:52 KST 실행(cron-job.org workflow_dispatch).

★ 2026-11-17 개편(사용자 요청 "고쳐줘"):
   대상 = ① 텔레그램에 입력한 보유 종목(trades_store.open_positions)
          ② 투탑(삼성전자·SK하이닉스) — 항상 NXT 대상이라 수집 성공 여부의 기준이 된다
          ③ 전일 2차 신호 종목(있으면 덧붙임, 없어도 실패 아님)
   전에는 ③만 대상이었고, 신호 종목이 전부 NXT 미대상이면 exit 1로 끝나
   파일이 안 남았다(10월 중순부터 11-17까지 실패 다수).
   08:52 한 번 조회로 프리장 시가·고가·저가·현재가를 함께 저장한다
   (`overMarketPriceInfo`의 open/high/low는 진행 중 세션 값, 2026-11-17 저녁장에서 관측).
   보유 종목은 매수가·손절가 대비 %와 "프리장 저가가 손절가를 건드렸는지"를 적는다.
   파일 이름은 이제 **수집한 날짜** 기준: nxt_morning_{오늘}.json (전에는 신호 날짜였다).
   TRADES_DIR  텔레그램 입력 저장소 (워크플로에서 백업 레포 data/trades)

(아래는 옛 설명 — 신호 종목 수집 시절 기록)

⚠ 2026-09-09까지 이 스크립트는 **한 번도 파일을 만든 적이 없다.**
   `data/signals/`를 봇 레포에서 읽었는데 거기엔 신호 CSV가 커밋되지 않는다
   (백업 레포로만 간다). 매 실행이 "어제 신호 파일 없음 → skip"을 찍고
   exit 0으로 끝나서 워크플로는 3개월간 초록불이었다.
   → 입출력 경로를 환경변수로 받고, 신호를 못 찾으면 **exit 1로 죽인다.**

   SIGNALS_DIR      신호 CSV가 있는 디렉터리 (기본: 이 레포의 data/signals)
   NXT_MORNING_DIR  결과 저장 위치 (기본: 이 레포의 data/nxt_morning)

{
  "date": "2026-06-02",
  "fetched_at": "08:52",
  "prices": {
    "005930": {"name": "삼성전자", "nxt_price": 360500, "signal_price": 350000, "pct": 3.0},
    ...
  }
}

⚠ 2026-09-10 두 번째 발견: 파일은 생겼는데 **내용이 비어 있었다.**
   `closePriceRaw`는 KRX 정규장 전용 값이라 NXT 세션 내내 15:30에 멈춰 있다.
   그래서 08:52 실행이 전일 종가를 NXT 장전가로 적었다 — 9/10 3종목 전부
   pct 0.00, 전일 종가와 완전 일치(우리기술 14,320 / 가온전선 265,500 /
   SK이노베이션 153,500). 실제 NXT 가격은 `overMarketPriceInfo.overPrice`.

   `overMarketPriceInfo`가 null이면 그 종목은 NXT 미대상이다.
   `integratedPriceInfo.openPrice`는 KRX 시가와 다르다(9/10 NXT 대상 4종목
   전부 +900부터 +7,000까지 상회) — 일봉 시가는 KRX 09:00 값이라는 뜻.
"""
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_DELAY

logger = logging.getLogger(__name__)

_POLL_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
_ROOT     = Path(__file__).parent.parent
_OUT_DIR     = Path(os.environ.get("NXT_MORNING_DIR") or (_ROOT / "data" / "nxt_morning"))
_SIGNALS_DIR = Path(os.environ.get("SIGNALS_DIR")     or (_ROOT / "data" / "signals"))


def _to_int(v) -> int | None:
    """'154,500' 또는 154500 → 154500."""
    if v in (None, "", "-"):
        return None
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fetch_quote(code: str) -> dict:
    """naver polling API 1회 조회.

    ⚠ 2026-09-10 발견: 기존 구현은 `closePriceRaw`만 읽었는데 이 값은
       **KRX 정규장 전용**이다. NXT 세션 중에도 15:30에 멈춘 채 움직이지 않아,
       08:52 실행이 매번 '전일 종가'를 NXT 장전가라고 기록했다
       (9/10 3종목 전부 pct 0.00 — 전일 종가와 완전 일치).
       실제 NXT 가격은 `overMarketPriceInfo`에 있다. 이 블록이 없으면
       그 종목은 **NXT 미대상**이다.
    """
    out = {"nxt_price": None, "krx_close": None, "nxt_eligible": False,
           "session": None, "integrated_open": None}
    try:
        r = requests.get(_POLL_URL.format(code=code), headers=HEADERS, timeout=10)
        if not r.ok:
            return out
        data = r.json().get("datas", [])
        if not data:
            return out
        d = data[0]
        out["krx_close"] = _to_int(d.get("closePriceRaw"))
        ip = d.get("integratedPriceInfo") or {}
        out["integrated_open"] = _to_int(ip.get("openPrice"))
        om = d.get("overMarketPriceInfo")
        if om:
            out["nxt_eligible"] = (om.get("tradeStopType") or {}).get("name", "TRADING") == "TRADING"
            out["nxt_price"] = _to_int(om.get("overPrice"))
            out["session"] = f'{om.get("tradingSessionType")}/{om.get("overMarketStatus")}'
    except Exception as e:
        logger.warning(f"[{code}] 조회 실패: {e}")
    return out


def _find_yesterday_signals() -> tuple[str | None, dict[str, str]]:
    """가장 최근 2차(17:50) 신호 CSV 로드. {code: name} 반환."""
    import pandas as pd
    from scripts.storage import find_signal_file
    for days_back in range(1, 5):
        d_str = (date.today() - timedelta(days=days_back)).isoformat()
        path = find_signal_file(d_str, kind="2차", signals_dir=_SIGNALS_DIR)
        if path is None:
            continue
        try:
            df = pd.read_csv(path, dtype={"종목코드": str}, encoding="utf-8-sig")
            if df.empty:
                continue
            code_col = "종목코드"
            name_col = "종목명"
            stocks = {
                str(row[code_col]).zfill(6): str(row[name_col])
                for _, row in df.iterrows()
                if pd.notna(row.get(code_col))
            }
            logger.info(f"신호 파일 로드: {path} ({len(stocks)}개 종목)")
            return d_str, stocks
        except Exception as e:
            logger.warning(f"로드 실패 {path}: {e}")
    return None, {}


def _load_signal_prices(sig_date: str) -> dict[str, float]:
    """signals CSV에서 entry_reference_price 또는 signal_price 추출."""
    import pandas as pd
    from scripts.storage import find_signal_file
    _p = find_signal_file(sig_date, kind="2차", signals_dir=_SIGNALS_DIR)
    matches = [_p] if _p else []
    if not matches:
        return {}
    try:
        df = pd.read_csv(matches[0], dtype={"종목코드": str}, encoding="utf-8-sig")
        result = {}
        for _, row in df.iterrows():
            code = str(row.get("종목코드", "")).zfill(6)
            price = row.get("entry_reference_price") or row.get("signal_price") or 0
            try:
                result[code] = float(price)
            except (TypeError, ValueError):
                pass
        return result
    except Exception:
        return {}


TWO_TOP = {"005930": "삼성전자", "000660": "SK하이닉스"}


def _session_ohlc(code: str) -> dict:
    """진행 중인 NXT 세션의 시가·고가·저가·현재가. 블록이 없으면 전부 None."""
    out = {"open": None, "high": None, "low": None, "last": None, "session": None}
    try:
        r = requests.get(_POLL_URL.format(code=code), headers=HEADERS, timeout=10)
        d = (r.json().get("datas") or [{}])[0] if r.ok else {}
        om = d.get("overMarketPriceInfo") or {}
        if om:
            out.update(open=_to_int(om.get("openPrice")), high=_to_int(om.get("highPrice")),
                       low=_to_int(om.get("lowPrice")), last=_to_int(om.get("overPrice")),
                       session=f'{om.get("tradingSessionType")}/{om.get("overMarketStatus")}')
    except Exception as e:
        logger.warning(f"[{code}] 세션 시세 실패: {e}")
    return out


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.market_calendar import is_trading_day, get_now_kst
    from scripts import trades_store as ts
    now = get_now_kst()
    today = now.date()
    if not is_trading_day(today):
        logger.info("비거래일 — 수집 생략")
        return 0
    out_path = _OUT_DIR / f"nxt_morning_{today.isoformat()}.json"
    if out_path.exists():
        logger.info(f"이미 존재: {out_path} → skip")
        return 0

    # ① 보유 종목 — 이름으로 입력되므로 코드를 찾는다
    try:
        pos = ts.open_positions(days=15)
    except Exception as e:   # 입력 파일이 깨져도 투탑·신호는 남긴다
        logger.error(f"보유 입력 읽기 실패: {e}")
        pos = []
    logger.info(f"보유 입력 {len(pos)}건 (TRADES_DIR={ts.trades_dir()})")
    codes = {}
    if pos:
        from scripts.morning_briefing import _code_by_name
        codes = _code_by_name([p["name"] for p in pos])

    targets: dict[str, dict] = {}
    for p in pos:
        code = codes.get(p["name"])
        if not code:
            logger.warning(f"보유 '{p['name']}' 종목코드를 못 찾음 — 건너뜀")
            continue
        targets[code] = {"name": p["name"], "role": "보유", "buy_price": p.get("price"),
                         "stop": p.get("깨는가"), "qty": p.get("qty"), "input_time": p.get("time")}
    for code, name in TWO_TOP.items():
        targets.setdefault(code, {"name": name, "role": "투탑"})
    sig_date, sigs = _find_yesterday_signals()
    sig_prices = _load_signal_prices(sig_date) if sig_date else {}
    for code, name in sigs.items():
        targets.setdefault(code, {"name": name, "role": "신호", "signal_price": sig_prices.get(code)})

    prices: dict[str, dict] = {}
    for code, t in targets.items():
        q = _session_ohlc(code)
        row = {**t, **{f"nxt_{k}": v for k, v in q.items() if k != "session"}, "session": q["session"]}
        base = t.get("buy_price") or t.get("signal_price")
        if base:
            row["low_pct"] = _pct(q["low"], base)
            row["last_pct"] = _pct(q["last"], base)
        if t.get("stop") and q["low"]:
            row["stop_touched"] = q["low"] <= t["stop"]
        prices[code] = row
        logger.info(f"  [{t['role']}] {t['name']}({code}) 시{q['open']} 고{q['high']} 저{q['low']} 현{q['last']} {q['session']}")
        time.sleep(REQUEST_DELAY)

    result = {"date": today.isoformat(), "fetched_at": now.strftime("%H:%M"),
              "signal_date": sig_date, "prices": prices}
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"저장: {out_path}")

    # 투탑은 항상 NXT 대상 — 둘 다 값이 없으면 조회 자체가 망가진 것
    if not any(prices.get(c, {}).get("nxt_last") for c in TWO_TOP):
        logger.error("투탑 NXT 가격을 하나도 못 받았다 — overMarketPriceInfo 구조나 조회를 확인할 것")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
