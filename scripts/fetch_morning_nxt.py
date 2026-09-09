# scripts/fetch_morning_nxt.py
"""
D+1 NXT 장전 단일가 수집 — 매일 08:52 KST 실행.

GitHub Actions cron: "52 23 * * 0-4"  (23:52 UTC = 08:52 KST 월~금)

어제 신호 종목들의 08:50 장전 단일가(또는 형성 중인 가격)를 수집해
nxt_morning_{YYYY-MM-DD}.json 으로 저장.

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

주의: naver polling API는 08:00~08:50 장전 세션 중 현재 형성가를 반환.
      09:00 정규장 시작 이후에는 정규장 가격을 반환하므로
      반드시 08:52~08:59 사이에 실행할 것.
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


def _fetch_price(code: str) -> int | None:
    """naver polling API에서 현재가 조회 (장전 세션 중이면 장전 단일가 형성가)."""
    url = _POLL_URL.format(code=code)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if not r.ok:
            return None
        data = r.json().get("datas", [])
        if not data:
            return None
        raw = data[0].get("closePriceRaw")
        return int(raw) if raw else None
    except Exception as e:
        logger.warning(f"[{code}] 가격 조회 실패: {e}")
        return None


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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger.info(f"신호 경로: {_SIGNALS_DIR}  (존재={_SIGNALS_DIR.exists()})")
    logger.info(f"저장 경로: {_OUT_DIR}")

    from scripts.market_calendar import is_trading_day, get_now_kst
    today = get_now_kst().date()
    if not is_trading_day(today):
        logger.info("비거래일 — 수집 생략")
        return 0

    sig_date, stocks = _find_yesterday_signals()
    if not stocks:
        # 조용히 넘어가면 안 된다. 이 스크립트는 이 분기에서 3개월간 아무것도
        # 만들지 않으면서 워크플로는 초록불이었다(2026-09-09 발견).
        logger.error(
            f"신호 파일을 못 찾았다 — {_SIGNALS_DIR} 안에 최근 4일치 2차 CSV가 없다. "
            "SIGNALS_DIR가 백업 레포를 가리키는지 확인할 것."
        )
        return 1

    out_path = _OUT_DIR / f"nxt_morning_{sig_date}.json"
    if out_path.exists():
        logger.info(f"이미 존재: {out_path} → skip")
        return 0

    signal_prices = _load_signal_prices(sig_date)
    now_str = __import__("datetime").datetime.now().strftime("%H:%M")

    prices: dict[str, dict] = {}
    for code, name in stocks.items():
        nxt_price = _fetch_price(code)
        sig_price = signal_prices.get(code)
        pct = None
        if nxt_price and sig_price and sig_price > 0:
            pct = round((nxt_price - sig_price) / sig_price * 100, 2)
        prices[code] = {
            "name":         name,
            "nxt_price":    nxt_price,
            "signal_price": sig_price,
            "pct":          pct,
        }
        logger.info(f"  [{code}] {name}: {nxt_price} ({'+' if pct and pct > 0 else ''}{pct:.2f}% 기준대비)" if pct is not None else f"  [{code}] {name}: {nxt_price}")
        time.sleep(REQUEST_DELAY)

    result = {
        "date":       sig_date,
        "fetched_at": now_str,
        "prices":     prices,
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"저장: {out_path}")

    # 간단한 요약 출력
    valid = [(c, v) for c, v in prices.items() if v["pct"] is not None]
    if valid:
        avg = sum(v["pct"] for _, v in valid) / len(valid)
        win = sum(1 for _, v in valid if v["pct"] > 0)
        logger.info(f"장전 단가 요약: {len(valid)}개 / 평균 {avg:+.2f}% / 양봉 {win}개")

    # 가격을 하나도 못 받았으면 파일만 남고 쓸모가 없다. 이것도 드러내야 한다.
    if not any(v["nxt_price"] for v in prices.values()):
        logger.error(f"{len(prices)}개 종목 전부 가격 조회 실패 — 저장은 했으나 내용이 비었다")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
