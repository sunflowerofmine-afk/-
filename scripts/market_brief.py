# scripts/market_brief.py
"""시황 알림 — 봇이 사용자에게 보내는 유일한 알림 (2026-09-11 결정).

봇은 종목을 고르지 않는다. 후보·게이트·패턴은 계속 계산해 저장하되(백테스트용) 알림엔 싣지
않는다. 이유: 봇 선별은 D+1 승률 48.1%·중앙값 0.00%로 예측력이 없고, 고수 셋이 산 대장주를
봇 트랙이 구조적으로 못 고르며, 후보를 보여 주면 사용자 판단이 거기 앵커링된다.
사용자는 이 시황을 보고 MTS에서 직접 판단하고, 매매 근거는 텔레그램 답장으로 남긴다
(`scripts/harvest_trades.py`).

내용 = 판단지 0번("오늘 자리가 있는가"). 숫자만 쓴다. "밀리지 않았다" 같은 해석 문구는 넣지
않는다 — 사용자 요청.
  지수 / 거래대금(전일·20일 평균 대비 %) / 오른 종목 비율·상한가·집중도 /
  미선물·유가·환율 / NXT 거래대금 상위 10 / 다음 거래일까지 밤 수 + 테마 맵 이미지
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import REPORTS_DIR
from scripts import market_history as mh
from scripts import notifier as ntf
from scripts.market_calendar import get_now_kst, get_run_type, is_trading_day
from scripts.naver_api import fetch_stock_default, to_float

logger = logging.getLogger(__name__)

_WD = ["월", "화", "수", "목", "금", "토", "일"]
_SLOT_LABEL = {"1420": "마감 전 잠정", "1535": "종가 확정", "1750": "NXT 반영", "1930": "NXT 막판"}
_SESSION_KO = {"PRE_MARKET": "프리장", "REGULAR_MARKET": "메인", "AFTER_MARKET": "저녁장"}
NXT_TOP_N = 10


def slot_for(now: datetime, run_type: str) -> str:
    hhmm = now.strftime("%H%M")
    if run_type == "1차":
        return "1420"
    if run_type == "2차":
        return "1535" if hhmm < "1700" else "1750"
    if "1900" <= hhmm <= "2000":
        return "1930"
    return hhmm


def _next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(n):
            return n
        n += timedelta(days=1)
    return n


def _pct_txt(v: float | None, none: str = "기록 없음") -> str:
    return none if v is None else f"{v:+.0f}%"


def _tv_txt(eok: float | None) -> str:
    if not eok:
        return "-"
    return f"{eok/10000:.1f}조" if eok >= 10000 else f"{eok:,.0f}억"


def _adl_from(df: pd.DataFrame) -> float | None:
    """오른 종목 비율(%) — 등락률 0 제외. pipeline._calc_market_regime과 같은 정의."""
    try:
        chg = df["등락률"].dropna()
        total = int((chg != 0).sum())
        return round(float((chg > 0).sum()) / total * 100, 1) if total else None
    except Exception:
        return None


def collect(now: datetime, run_type: str, raw_data: dict[str, pd.DataFrame] | None = None,
            merged_df: pd.DataFrame | None = None, index_levels: dict | None = None) -> dict:
    """알림 재료를 모은다. pipeline이 이미 받은 데이터는 인자로 넘겨 다시 받지 않는다.
    단독 실행(19:30)이면 전부 직접 받는다. 어느 항목이 실패해도 나머지로 알림을 만든다."""
    from scripts import fetch_market_data as fmd
    from scripts.ranking import calc_market_total, filter_excluded_stocks

    d: dict = {"now": now, "run_type": run_type, "slot": slot_for(now, run_type)}
    today = now.date()

    # 지수
    if index_levels is None:
        try:
            index_levels = fmd.fetch_index_levels()
        except Exception as e:
            logger.warning(f"지수 수집 실패: {e}")
            index_levels = {}
    d["index"] = index_levels

    # 전 종목 (KRX). 표시용 KRX 가격은 NXT 합산 전 원본에서 읽는다.
    if raw_data is None:
        try:
            raw_data = fmd.run()
        except Exception as e:
            logger.warning(f"전 종목 수집 실패: {e}")
            raw_data = {}
    kospi_df = raw_data.get("KOSPI", pd.DataFrame())
    kosdaq_df = raw_data.get("KOSDAQ", pd.DataFrame())
    all_raw = pd.concat([x for x in (kospi_df, kosdaq_df) if not x.empty], ignore_index=True) \
        if (not kospi_df.empty or not kosdaq_df.empty) else pd.DataFrame()
    totals = calc_market_total(kospi_df, kosdaq_df)
    d["kospi_tv_eok"]  = totals.get("kospi_total_tv_eok") or None
    d["kosdaq_tv_eok"] = totals.get("kosdaq_total_tv_eok") or None
    d["krx_price"] = {}
    if not all_raw.empty:
        d["krx_price"] = dict(zip(all_raw["종목코드"].astype(str), all_raw["현재가"].astype(float)))

    # NXT 전체 (거래대금 상위 10 + 총액). 실패하면 빈 값.
    nxt_rows: list[dict] = []
    try:
        nxt_rows = fetch_stock_default(trade_type="NXT", market_type="ALL", order_type="quantTop")
    except Exception as e:
        logger.warning(f"NXT 수집 실패: {e}")
    nxt = []
    for r in nxt_rows:
        code = str(r.get("itemcode") or "")
        amt = to_float(r.get("tradeAmount"), 0.0) or 0.0
        nxt.append({"code": code, "name": r.get("itemname"), "price": to_float(r.get("nowPrice")),
                    "tv_eok": amt / 1e8, "session": r.get("tradingSessionType"),
                    "status": r.get("marketStatus")})
    nxt.sort(key=lambda x: x["tv_eok"], reverse=True)
    d["nxt_total_eok"] = round(sum(x["tv_eok"] for x in nxt)) if nxt else None
    d["nxt_top"] = nxt[:NXT_TOP_N]
    d["nxt_session"] = _SESSION_KO.get((nxt[0]["session"] if nxt else "") or "", "")
    d["nxt_open"] = bool(nxt and nxt[0]["status"] == "OPEN")

    # 오른 종목 비율 · 상한가 · Top5 집중도 — pipeline과 같은 정의.
    # 집중도 분자는 NXT 합산 후 거래대금이라(pipeline의 top_tv) 단독 실행도 같은 합산을 거친다.
    if (merged_df is None or merged_df.empty) and not all_raw.empty:
        try:
            from scripts.fetch_nxt_data import fetch_nxt_quant, merge_nxt_into_df
            merged_df = merge_nxt_into_df(all_raw.copy(), fetch_nxt_quant())
        except Exception as e:
            logger.warning(f"NXT 합산 실패 — KRX만으로 계산: {e}")
    base_df = merged_df if merged_df is not None and not merged_df.empty else all_raw
    d["adl_pct"] = _adl_from(base_df) if not base_df.empty else None
    d["limit_up"] = None
    d["top5_pct"] = None
    if not base_df.empty:
        try:
            ex = filter_excluded_stocks(base_df)
            d["limit_up"] = int((ex["등락률"] >= 29.5).sum())
            total_eok = (d["kospi_tv_eok"] or 0) + (d["kosdaq_tv_eok"] or 0)
            if total_eok > 0:
                top5_eok = float(ex.nlargest(5, "거래대금")["거래대금"].sum()) / 1e8
                d["top5_pct"] = round(top5_eok / total_eok * 100, 1)
        except Exception as e:
            logger.warning(f"상한가·집중도 계산 실패: {e}")

    # 미선물 · 거시
    try:
        from scripts.fetch_futures import fetch_futures
        d["futures"] = fetch_futures()
    except Exception as e:
        logger.warning(f"미선물 실패: {e}")
        d["futures"] = {}
    try:
        from scripts.fetch_macro import fetch_macro
        d["macro"] = fetch_macro()
    except Exception as e:
        logger.warning(f"거시 실패: {e}")
        d["macro"] = {}

    # 전일·20일 비교 (기록 파일) → 그 뒤에 오늘 행 추가
    d["cmp"] = mh.compare(today.isoformat(), d["slot"], d["kospi_tv_eok"], d["kosdaq_tv_eok"], d["nxt_total_eok"])
    try:
        mh.append({
            "date": today.isoformat(), "slot": d["slot"], "time": now.strftime("%H%M"),
            "kospi_tv_eok": d["kospi_tv_eok"], "kosdaq_tv_eok": d["kosdaq_tv_eok"],
            "nxt_tv_eok": d["nxt_total_eok"], "adl_pct": d["adl_pct"], "limit_up": d["limit_up"],
            "top5_pct": d["top5_pct"], "kospi_chg": index_levels.get("kospi_chg"),
            "kosdaq_chg": index_levels.get("kosdaq_chg"),
        })
    except Exception as e:
        logger.warning(f"시황 기록 저장 실패: {e}")

    nxt_day = _next_trading_day(today)
    d["next_day"] = nxt_day
    d["nights"] = (nxt_day - today).days
    return d


def build_text(d: dict) -> str:
    now: datetime = d["now"]
    slot = d["slot"]
    head_when = f"{now.month:02d}/{now.day:02d} ({_WD[now.weekday()]}) {now.strftime('%H:%M')}"
    label = _SLOT_LABEL.get(slot, "수동")
    L = [f"<b>[종베 시황] {head_when} · {label}</b>"]

    ix = d.get("index") or {}
    def _lv(v):  return f"{v:,.2f}" if v is not None else "-"
    def _ch(v):  return f"<b>{v:+.2f}%</b>" if v is not None else "-"
    L.append(f"코스피 {_lv(ix.get('kospi_level'))} {_ch(ix.get('kospi_chg'))} · 코스닥 {_lv(ix.get('kosdaq_level'))} {_ch(ix.get('kosdaq_chg'))}")

    c = d.get("cmp") or {}
    if slot == "1420":
        L.append(
            f"거래대금 코스피 {_tv_txt(d.get('kospi_tv_eok'))} (전일 같은 시각 {_pct_txt(c.get('kospi_vs_prev'))} · 전일 하루치의 "
            f"{c.get('progress_kospi') if c.get('progress_kospi') is not None else '-'}%) · "
            f"코스닥 {_tv_txt(d.get('kosdaq_tv_eok'))} ({_pct_txt(c.get('kosdaq_vs_prev'))} · "
            f"{c.get('progress_kosdaq') if c.get('progress_kosdaq') is not None else '-'}%)"
        )
    else:
        L.append(
            f"거래대금 코스피 {_tv_txt(d.get('kospi_tv_eok'))} (전일 {_pct_txt(c.get('kospi_vs_prev'))} · 20일 {_pct_txt(c.get('kospi_vs_avg20'))}) · "
            f"코스닥 {_tv_txt(d.get('kosdaq_tv_eok'))} (전일 {_pct_txt(c.get('kosdaq_vs_prev'))} · 20일 {_pct_txt(c.get('kosdaq_vs_avg20'))})"
        )

    adl = d.get("adl_pct"); lu = d.get("limit_up"); t5 = d.get("top5_pct")
    L.append(
        f"오른 종목 {adl:.0f}%" if adl is not None else "오른 종목 -"
        )
    L[-1] += f" · 상한가 {lu if lu is not None else '-'} · Top5 집중 {t5 if t5 is not None else '-'}%"

    fu = d.get("futures") or {}; mc = d.get("macro") or {}
    parts = []
    for name, short in (("나스닥선물", "나스닥선물"), ("S&P선물", "S&P선물"), ("VIX", "VIX")):
        v = fu.get(name)
        if v and v.get("chg_pct") is not None:
            parts.append(f"{short} {v['value']:,.1f} ({v['chg_pct']:+.2f}%)" if name == "VIX" else f"{short} {v['chg_pct']:+.2f}%")
    if mc.get("wti") is not None:
        parts.append(f"WTI {mc['wti']:.1f} ({mc.get('wti_chg', 0):+.2f})")
    if mc.get("usdkrw") is not None:
        parts.append(f"환율 {mc['usdkrw']:,.1f} ({mc.get('usdkrw_chg', 0):+.1f})")
    if parts:
        L.append(" · ".join(parts))

    # NXT
    if d.get("nxt_top"):
        sess = d.get("nxt_session") or "NXT"
        state = "진행" if d.get("nxt_open") else "마감"
        basis = "KRX 종가" if slot != "1420" else "KRX 현재가"
        L.append("")
        L.append(f"<b>NXT {sess} {state} {now.strftime('%H:%M')}</b> · 총 {_tv_txt(d.get('nxt_total_eok'))} (전일 같은 시각 {_pct_txt(c.get('nxt_vs_prev'))}) · {basis} 대비")
        for i, x in enumerate(d["nxt_top"], 1):
            krx = (d.get("krx_price") or {}).get(x["code"])
            px = x.get("price")
            diff = f"{(px / krx - 1) * 100:+.2f}%" if (px and krx) else "-"
            L.append(f" {i:>2} {x['name']} {px:,.0f} {diff} · {_tv_txt(x['tv_eok'])}" if px else f" {i:>2} {x['name']} - · {_tv_txt(x['tv_eok'])}")

    nd = d.get("next_day")
    if nd:
        L.append("")
        L.append(f"다음 거래일 {nd.month:02d}/{nd.day:02d} ({_WD[nd.weekday()]}) · 밤 {d.get('nights')}")
    return "\n".join(L)


def build_map(d: dict) -> Path | None:
    from scripts.theme_map import build
    now: datetime = d["now"]
    title = f"주도 테마 · {now.month:02d}/{now.day:02d} ({_WD[now.weekday()]}) {now.strftime('%H:%M')}"
    out = REPORTS_DIR / "theme_map" / f"{now.strftime('%Y-%m-%d_%H%M')}.png"
    return build(out, title)


def send(d: dict) -> bool:
    text = build_text(d)
    ok = ntf.send_message(text)
    logger.info(f"시황 알림 발송 {'성공' if ok else '실패'} ({d.get('slot')})")
    img = build_map(d)
    if img:
        ok_img = ntf.send_photo(img)
        logger.info(f"테마 맵 발송 {'성공' if ok_img else '실패'}: {img}")
    else:
        logger.warning("테마 맵 없음 — 텍스트만 발송")
    return ok


def run(run_type: str | None = None) -> None:
    """단독 실행 (19:30 NXT 막판 · 수동). 전 종목·NXT·지표를 직접 받는다."""
    now = get_now_kst()
    rt = run_type or get_run_type(now)
    if not is_trading_day(now.date()):
        logger.info("비거래일 — 시황 알림 생략")
        return
    d = collect(now, rt)
    send(d)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="TELEGRAM_CHAT_ID_DEV로만 발송")
    ap.add_argument("--dry", action="store_true", help="발송 없이 텍스트만 출력")
    a = ap.parse_args()
    if a.preview:
        ntf.set_preview_mode(True)
    if a.dry:
        _now = get_now_kst()
        _d = collect(_now, get_run_type(_now))
        print(build_text(_d))
        print(build_map(_d))
    else:
        run()
