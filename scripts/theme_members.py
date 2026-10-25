# scripts/theme_members.py
"""테마 구성 종목 하루 한 번 저장 — 14:20 테마 히트맵이 읽는다.

구성 종목 조회는 네이버가 10개씩 끊어 줘서(2026-10-16 cursor 방식) 테마 40개만 받아도 1분 30초가
걸렸다(10-25 실측). 14:20 알림에서 그 자리에서 받으면 알림이 그만큼 늦는다. 구성은 하루 안에
거의 안 바뀌므로 07:40 아침 실행이 전부 받아 data/theme_members.json 한 파일에 덮어쓴다.
14:20은 이 파일의 종목코드만 쓰고 등락률·거래대금은 그 시각 전 종목 시세에서 읽는다.
"""
from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.naver_api import fetch_group_list, fetch_group_stocks, to_float

logger = logging.getLogger(__name__)

PATH = Path("data") / "theme_members.json"
WORKERS = 6


def refresh(path: Path = PATH) -> Path | None:
    """테마 전체의 구성 종목코드를 받아 저장. 실패한 테마는 빼고 저장한다(전부 실패면 기존 파일 유지)."""
    themes = [t for t in fetch_group_list("theme") if t.get("no") is not None]

    def one(t):
        try:
            return t, [str(m.get("itemcode")) for m in fetch_group_stocks("theme", t["no"]) if m.get("itemcode")]
        except Exception as e:
            logger.warning(f"[{t.get('name')}] 구성 종목 실패: {e}")
            return t, None

    with ThreadPoolExecutor(WORKERS) as ex:
        got = list(ex.map(one, themes))
    # 받기 실패한 테마는 전날 구성을 그대로 쓴다 — 일부 실패로 테마가 지도에서 빠지지 않게(GPT 검토 10-25)
    prev = {str(t["no"]): t["codes"] for t in (load(path) or {}).get("themes", [])}
    rows, kept = [], 0
    for t, codes in got:
        if not codes and prev.get(str(t["no"])):
            codes, kept = prev[str(t["no"])], kept + 1
        if codes:
            rows.append({"no": t["no"], "name": str(t.get("name") or ""), "codes": codes})
    if len(rows) == kept:
        logger.warning("테마 구성 새로 받은 것 0건 — 기존 파일 유지")
        return None
    if kept:
        logger.warning(f"테마 {kept}개는 받기 실패 — 전날 구성 유지")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "themes": rows},
                               ensure_ascii=False, indent=0), encoding="utf-8")
    logger.info(f"테마 구성 저장: {len(rows)}/{len(themes)}개 테마 → {path}")
    return path


def load(path: Path = PATH) -> dict | None:
    """{"saved_at", "themes": [{no, name, codes}]}. 없거나 깨졌으면 None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data.get("themes") else None
    except Exception:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    refresh()
