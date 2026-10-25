# scripts/harvest_trades.py
"""텔레그램 답장 수거 — 사용자가 봇 채팅에 남긴 매매 근거를 백업 레포에 저장.

GitHub Actions는 상주 프로세스가 없어 웹훅을 못 받는다. 대신 실행 때마다 getUpdates로
밀린 메시지를 긁는다. 텔레그램은 미수거 업데이트를 24시간만 보관하므로 매일 최소 한 번은
돌아야 한다 — 07:40 아침 브리핑 워크플로가 주말에도 수거한다.

저장 위치는 TRADES_DIR (워크플로: 백업 레포 clone 안). 오프셋도 같은 곳에 둔다.
수거한 건은 봇 채팅에 "기록됨" 한 줄로 되돌려 준다 — 입력이 살았는지 사용자가 알아야 한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from scripts import trades_store as ts
from scripts.notifier import send_private

logger = logging.getLogger(__name__)


def _offset_path(store: Path) -> Path:
    return store / "offset.json"


def _load_offset(store: Path) -> int:
    p = _offset_path(store)
    if p.exists():
        try:
            return int(json.loads(p.read_text(encoding="utf-8")).get("last_update_id", 0))
        except (ValueError, json.JSONDecodeError):
            return 0
    return 0


def _save_offset(store: Path, last: int) -> None:
    store.mkdir(parents=True, exist_ok=True)
    _offset_path(store).write_text(json.dumps({"last_update_id": last}), encoding="utf-8")


def fetch_updates(offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    resp = requests.get(url, params={"offset": offset + 1, "timeout": 0, "limit": 100,
                                     "allowed_updates": json.dumps(["message", "edited_message"])},
                        timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates 실패: {str(data)[:200]}")
    return data.get("result") or []


def _send_reviews(rows: list[dict]) -> None:
    """매수(연습 포함)를 적은 뒤에만 복기 카드를 보낸다 — 판단 전엔 고수 질문을 보이지 않는다(2026-10-25).
    실패해도 수거에는 영향 없다."""
    buys = [r for r in rows if r.get("side") == "매수" and r.get("name") and not r.get("edited")]
    if not buys:
        return
    # 수거는 정기 알림 시각 밖(12:00·23:00 등)에도 돌므로 ✅ 기록됨처럼 소리·진동 없이 보낸다 — 하루 3번 원칙
    try:
        from scripts.check_cards import build_review
        from scripts.morning_briefing import _code_by_name
        from scripts.notifier import send_photo
        codes = _code_by_name([r["name"] for r in buys])
    except Exception as e:
        logger.warning(f"복기 카드 준비 실패: {e}")
        return
    for r in buys:
        try:
            code = codes.get(r["name"])
            if not code:
                logger.info(f"복기 카드 생략 — 종목코드를 못 찾음: {r['name']}")
                continue
            p = build_review(r["name"], code, bool(r.get("practice")), Path("reports") / "review_card" / f"{code}.png")
            if p:
                send_photo(p, silent=True)
        except Exception as e:
            logger.warning(f"[{r.get('name')}] 복기 카드 실패: {e}")


def harvest(store: Path, ack: bool = True) -> list[dict]:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 — 수거 생략")
        return []
    store.mkdir(parents=True, exist_ok=True)   # 0건이어도 폴더는 남긴다 — 워크플로 git add가 여기를 본다
    last = _load_offset(store)
    updates = fetch_updates(last)
    saved: list[dict] = []
    max_id = last
    for up in updates:
        uid = int(up.get("update_id", 0))
        max_id = max(max_id, uid)
        edited = "edited_message" in up
        msg = up.get("edited_message") or up.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text") or msg.get("caption") or ""
        if chat_id != str(TELEGRAM_CHAT_ID) or not text.strip():
            continue
        ts_utc = int(msg.get("edit_date") or msg.get("date") or 0)
        row = ts.record(uid, int(msg.get("message_id", 0)), ts_utc, text, edited=edited, store=store)
        saved.append(row)
        logger.info(f"기록: {ts.format_ack(row)}")
    if max_id > last:
        _save_offset(store, max_id)
    if saved and ack:
        lines = ["✅ 기록됨"] + [f" · {ts.format_ack(r)}" for r in saved]
        send_private("\n".join(lines), silent=True)   # 확인용이라 소리·진동 없이 — 새벽 4시 수거 때 울렸다(사용자 09-22)
        _send_reviews(saved)
    logger.info(f"수거 완료: 업데이트 {len(updates)}건 중 저장 {len(saved)}건 (offset {last} → {max_id})")
    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=None, help="저장 폴더 (기본: TRADES_DIR 또는 data/trades)")
    ap.add_argument("--no-ack", action="store_true", help="기록 확인 메시지를 보내지 않는다")
    a = ap.parse_args()
    harvest(Path(a.store) if a.store else ts.trades_dir(), ack=not a.no_ack)
