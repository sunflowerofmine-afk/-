"""
send_telegram.py
필터링된 종목 결과를 텔레그램으로 전송
"""

import sys
import logging
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def send_message(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 미설정")
        return False

    resp = requests.post(
        TELEGRAM_API,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    if resp.status_code == 200:
        logger.info("텔레그램 전송 완료")
        return True
    else:
        logger.error(f"텔레그램 전송 실패: {resp.status_code} {resp.text}")
        return False


def format_message(df: pd.DataFrame, market_name: str, run_time: str) -> str:
    """DataFrame → 텔레그램 메시지 포맷"""
    if df.empty:
        return f"<b>[{market_name}]</b> {run_time}\n조건에 맞는 종목 없음"

    lines = [f"<b>[{market_name} 종가베팅 후보]</b> {run_time}\n"]
    for i, row in df.iterrows():
        change = f"+{row['등락률']:.2f}%" if row["등락률"] >= 0 else f"{row['등락률']:.2f}%"
        amount_억 = row["거래대금"] / 1_000_000_00
        lines.append(
            f"{i+1}. {row['종목명']}  {change}  "
            f"현재가 {int(row['현재가']):,}  거래대금 {amount_억:.0f}억"
        )

    return "\n".join(lines)


def run(filtered: dict) -> None:
    """filtered: {market_name: DataFrame}"""
    # TODO: run_pipeline.py에서 호출
    pass
