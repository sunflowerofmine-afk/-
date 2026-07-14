# scripts/nxt_evening.py
"""3차 알림 — NXT 저녁 흐름 (19:30 KST).

※ 용어 규약: 옆장 · 애프터마켓 · 대체거래소 · 넥스트레이드 = 모두 **NXT**로 통일 표기.
   (돌팬티는 일지에서 "대체거래소"라 쓰지만 NXT와 동일)

목적: 돌팬티의 실제 진입 시점(NXT 막판 19:50~20:00) 직전에, 그가 실제로 보는 것을 준다.
  "NXT에서 삼성전자까지 강한 흐름이 확인되어 20억 → 40억으로 비중 확대" (7/6)
  "NXT에서 빼는 흐름과 속도를 보니" (7/14)
  "오후 8시까지의 흐름과 미 선물, 유가 추이를 종합 확인한 후 최종 매수 여부 결정" (7/1)

2차(17:50)는 NXT 장(15:40~20:00)의 절반만 본 시점이다. 3차는 KRX 종가 대비
NXT 현재가가 살았는지 죽었는지를 보여준다.

★ NXT 투자자별(기관/외인) 수급은 이 알림에 없다 — 다만 "데이터가 없어서"가 아니다.
  증권사 HTS는 [정규장] vs [정규장+NXT] 투자자별을 따로 집계하므로, 차분하면 NXT 수급을
  구할 수 있다(2026-07-14 확인). 못 넣는 이유는 무료 소스(네이버)가 가격/거래량/거래대금만
  주고, 증권사 API 경로는 인프라 제약(키움=고정IP 필수) 때문. [[reference_broker_api_findings]]

매수 신호가 아니라 진입 직전 확인용 정보.
"""
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import TWOTOP_CODES, SIGNALS_DIR
from scripts import notifier as ntf
from scripts.fetch_nxt_data import fetch_nxt_quant
from scripts.fetch_stock_data import fetch_daily_history
from scripts.storage import find_signal_file

logger = logging.getLogger(__name__)


def _krx_close(code: str) -> float | None:
    """오늘 KRX 정규장 종가."""
    try:
        df = fetch_daily_history(code, pages=1)
        if df.empty:
            return None
        return float(df.iloc[0]["close"])   # iloc0 = 최신
    except Exception as e:
        logger.warning(f"[{code}] KRX 종가 조회 실패: {e}")
        return None


def _fmt_tv(won: float) -> str:
    """거래대금 읽기 쉽게 — 1조 이상은 조 단위."""
    eok = (won or 0) / 1e8
    return f"{eok/10000:.1f}조" if eok >= 10000 else f"{eok:,.0f}억"


def _line(name: str, krx: float | None, nxt: dict | None, nxt_top: set) -> str | None:
    """'삼성전자 263,000 → 266,500 (+1.33%) · NXT 1,240억' 한 줄."""
    if not krx or not nxt:
        return None
    px = nxt.get("nxt_price") or 0
    if px <= 0:
        return None
    chg = (px - krx) / krx * 100
    arrow = "🔴" if chg > 0 else ("🔵" if chg < 0 else "⚪")
    tag = " 🔵NXT대장" if nxt.get("_code") in nxt_top else ""
    return (f"{arrow} <b>{name}</b> {krx:,.0f} → {px:,.0f} "
            f"(<b>{chg:+.2f}%</b>) · NXT {_fmt_tv(nxt.get('nxt_tv'))}{tag}")


def _today_candidates() -> list[tuple[str, str]]:
    """오늘 2차 신호 종목 [(code, name)]. 없으면 빈 리스트."""
    p = find_signal_file(date.today().isoformat(), kind="2차", signals_dir=Path(SIGNALS_DIR))
    if p is None:
        return []
    try:
        df = pd.read_csv(p, dtype={"종목코드": str}, encoding="utf-8-sig")
        return [(str(r["종목코드"]).zfill(6), str(r["종목명"])) for _, r in df.iterrows()]
    except Exception as e:
        logger.warning(f"신호 CSV 로드 실패: {e}")
        return []


def build_message() -> str | None:
    nxt = fetch_nxt_quant()
    if not nxt:
        logger.warning("NXT 데이터 없음 → 발송 생략")
        return None
    # NXT 거래대금 상위 5 = 대장
    nxt_top = {c for c, _ in sorted(nxt.items(), key=lambda x: x[1].get("nxt_tv", 0), reverse=True)[:5]}

    lines = ["🌙 <b>3차 · NXT 저녁 흐름</b> (19:30 KST)",
             "※ KRX 종가 → NXT 현재가. 진입 직전 확인용 · <b>매수 신호 아님</b>", ""]

    # 투탑
    top_lines = []
    for code, name in TWOTOP_CODES.items():
        d = nxt.get(code)
        if d:
            d = {**d, "_code": code}
        ln = _line(name, _krx_close(code), d, nxt_top)
        if ln:
            top_lines.append(ln)
    if top_lines:
        lines.append("<b>■ 투탑</b>")
        lines += top_lines
        lines.append("")

    # 오늘 후보
    cands = _today_candidates()
    cand_lines = []
    for code, name in cands:
        d = nxt.get(code)
        if d:
            d = {**d, "_code": code}
        ln = _line(name, _krx_close(code), d, nxt_top)
        if ln:
            cand_lines.append(ln)
    lines.append("<b>■ 오늘 후보</b> (2차 알림 종목)")
    if cand_lines:
        lines += cand_lines
    elif cands:
        lines.append("후보 종목이 NXT 거래상위에 없음 (KRX 전용 가능성)")
    else:
        lines.append("오늘 후보 없음")
    lines.append("")

    # 선행 지표
    fwd = []
    try:
        from scripts.fetch_futures import fetch_futures
        fu = fetch_futures()
        nq = (fu.get("나스닥선물") or {}).get("chg_pct")
        vix = (fu.get("VIX") or {}).get("value")
        if nq is not None:
            fwd.append(f"나스닥선물 {nq:+.2f}%")
        if vix is not None:
            fwd.append(f"VIX {vix:.1f}")
        if fu.get("risk_appetite"):
            fwd.append(f"위험자산 선호 <b>{fu['risk_appetite']}</b>")
    except Exception as e:
        logger.warning(f"미선물 조회 실패: {e}")
    try:
        from scripts.fetch_macro import fetch_macro
        mc = fetch_macro()
        if mc.get("usdkrw"):
            fwd.append(f"달러원 {mc['usdkrw']:,.0f}")
        if mc.get("wti"):
            fwd.append(f"WTI {mc['wti']:.1f}")
    except Exception as e:
        logger.warning(f"거시 조회 실패: {e}")
    if fwd:
        lines.append("<b>■ 선행 지표</b>")
        lines.append(" · ".join(fwd))
        lines.append("")

    lines.append("───────────────────")
    lines.append("ℹ 이 알림은 NXT <b>가격·거래대금</b>만 표시 (무료 소스 한계). "
                 "NXT 투자자별 수급은 증권사 HTS에서 [정규장] vs [정규장+NXT] 차분으로 확인 가능")
    return "\n".join(lines)


def run() -> None:
    msg = build_message()
    if not msg:
        return
    ntf.send_message(msg)
    logger.info("3차 NXT 저녁 알림 전송 완료")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
