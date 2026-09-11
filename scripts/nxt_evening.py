# scripts/nxt_evening.py
"""19:30 KST — NXT 막판 시황 (2026-09-11부터 `market_brief`의 얇은 진입점).

예전엔 투탑 NXT 가격 + "오늘 후보"의 NXT 흐름 + 선행 지표를 보냈다. 후보 종목 알림을
중단하면서 이 시각도 같은 시황 알림(지수·거래대금·NXT 거래대금 상위 10·미선물·거시·테마 맵)을
보낸다. 돌팬티의 실제 진입 시점(NXT 막판 19:50부터 20:00) 직전이라는 자리는 그대로다.

※ 용어 규약: 옆장 · 애프터마켓 · 대체거래소 · 넥스트레이드 = 모두 **NXT**로 통일 표기.
★ NXT 투자자별(기관/외인) 수급은 여기 없다 — 무료 소스(네이버)가 가격/거래량/거래대금만 준다.
  증권사 HTS 화면 차분으로는 존재하나 자동화 경로가 없다. [[reference_broker_api_findings]]
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.market_brief import run as _run_brief


def run() -> None:
    _run_brief()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
