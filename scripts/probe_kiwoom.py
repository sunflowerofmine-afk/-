# scripts/probe_kiwoom.py
"""키움 REST API 확인용 프로브 — 실제 붙이기 전 가능 여부만 검증한다.

확인 목표 (3가지):
  1) 토큰 발급이 되는가 (서버에서, PC 없이)
  2) 종목별 투자자별 수급(ka10059/ka10064)이 조회되는가
  3) ★NXT/KRX를 구분해서 주는가 (stex_tp 파라미터 / 응답 필드)

주의:
  - API 키는 코드에 넣지 말 것. 환경변수에서만 읽는다.
      KIWOOM_APPKEY / KIWOOM_SECRETKEY  (필수)
      KIWOOM_MOCK=1                      (모의투자 서버로 테스트하려면)
  - 키 값은 로그에 절대 찍지 않는다.
  - 이 스크립트는 조회만 한다. 주문/매매 기능 없음.

실행: python -m scripts.probe_kiwoom
"""
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

REAL_HOST = "https://api.kiwoom.com"
MOCK_HOST = "https://mockapi.kiwoom.com"

# ka10059 종목별투자자기관별요청 / ka10064 장중투자자별매매차트요청
# 엔드포인트 경로가 공개문서에 명확치 않아 후보를 순회하며 찾는다.
_ENDPOINT_CANDIDATES = [
    "/api/dostk/stkinfo",
    "/api/dostk/mrkcond",
    "/api/dostk/chart",
    "/api/dostk/rkinfo",
]

TEST_CODE = "005930"   # 삼성전자


def _host() -> str:
    return MOCK_HOST if os.getenv("KIWOOM_MOCK") == "1" else REAL_HOST


def get_token(appkey: str, secretkey: str) -> str | None:
    """접근토큰 발급 (au10001)."""
    url = f"{_host()}/oauth2/token"
    body = {"grant_type": "client_credentials", "appkey": appkey, "secretkey": secretkey}
    try:
        r = requests.post(url, json=body, timeout=10,
                          headers={"Content-Type": "application/json;charset=UTF-8"})
        print(f"[토큰] {url} → HTTP {r.status_code}")
        data = r.json()
        # 키움은 'token' 또는 'access_token' 으로 반환 (버전차)
        tok = data.get("token") or data.get("access_token")
        if not tok:
            print("[토큰] 실패 응답:", json.dumps(data, ensure_ascii=False)[:400])
            return None
        print(f"[토큰] 발급 성공 (만료: {data.get('expires_dt') or data.get('expires_in')})")
        return tok
    except Exception as e:
        print(f"[토큰] 예외: {e}")
        return None


def call_tr(token: str, api_id: str, body: dict) -> tuple[str, dict] | None:
    """후보 엔드포인트를 순회하며 TR 호출. 성공한 (경로, 응답) 반환."""
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "api-id": api_id,
        "cont-yn": "N",
        "next-key": "",
    }
    for path in _ENDPOINT_CANDIDATES:
        url = f"{_host()}{path}"
        try:
            r = requests.post(url, headers=headers, json=body, timeout=10)
            data = r.json() if r.content else {}
            rc = str(data.get("return_code", ""))
            msg = data.get("return_msg", "")
            if r.status_code == 200 and rc in ("0", "00", ""):
                print(f"  ✅ {api_id} @ {path} → HTTP 200 (return_code={rc} {msg})")
                return path, data
            print(f"  ✗ {api_id} @ {path} → HTTP {r.status_code} rc={rc} {msg[:60]}")
        except Exception as e:
            print(f"  ✗ {api_id} @ {path} → 예외 {e}")
    return None


def _summarize(data: dict, label: str) -> None:
    """응답 구조 요약 — NXT 구분 필드가 있는지 본다."""
    print(f"\n--- {label} 응답 구조 ---")
    print("최상위 키:", list(data.keys()))
    for k, v in data.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"  '{k}' 리스트 {len(v)}행 · 필드: {list(v[0].keys())}")
            print(f"    첫 행 샘플: {json.dumps(v[0], ensure_ascii=False)[:300]}")
    blob = json.dumps(data, ensure_ascii=False)
    for kw in ["nxt", "NXT", "넥스트", "stex", "거래소", "krx", "KRX"]:
        if kw in blob:
            print(f"  🔎 '{kw}' 문자열이 응답에 존재 → 거래소 구분 가능성")


def main() -> None:
    appkey    = os.getenv("KIWOOM_APPKEY")
    secretkey = os.getenv("KIWOOM_SECRETKEY")
    if not appkey or not secretkey:
        print("환경변수 KIWOOM_APPKEY / KIWOOM_SECRETKEY 가 필요합니다.")
        print("  (키 값을 채팅이나 코드에 붙여넣지 마세요 — .env 또는 셸 환경변수로만)")
        return

    print(f"서버: {_host()}  ({'모의투자' if os.getenv('KIWOOM_MOCK') == '1' else '실전'})\n")
    token = get_token(appkey, secretkey)
    if not token:
        print("\n→ 토큰 발급 실패. 앱키/시크릿 또는 API 사용 신청 상태를 확인하세요.")
        return

    # 1) ka10059 종목별투자자기관별요청
    print("\n[1] ka10059 종목별투자자기관별요청 (삼성전자)")
    r1 = call_tr(token, "ka10059", {
        "dt": "", "stk_cd": TEST_CODE,
        "amt_qty_tp": "1",   # 1:금액, 2:수량
        "trde_tp": "0",      # 0:순매수
        "unit_tp": "1000",   # 단위
    })
    if r1:
        _summarize(r1[1], "ka10059")

    # 2) ka10064 장중투자자별매매차트요청 (장중 수급 — 네이버로는 불가능한 데이터)
    print("\n[2] ka10064 장중투자자별매매차트요청 (삼성전자)")
    r2 = call_tr(token, "ka10064", {
        "mrkt_tp": "000", "amt_qty_tp": "1", "trde_tp": "0", "stk_cd": TEST_CODE,
    })
    if r2:
        _summarize(r2[1], "ka10064")

    # 3) ★거래소 구분(stex_tp) 먹히는지 — KRX vs NXT 응답이 다른가
    print("\n[3] 거래소 구분 테스트 (stex_tp: 1=KRX, 2=NXT)")
    if r1:
        path = r1[0]
        for tp, name in [("1", "KRX"), ("2", "NXT")]:
            body = {"dt": "", "stk_cd": TEST_CODE, "amt_qty_tp": "1",
                    "trde_tp": "0", "unit_tp": "1000", "stex_tp": tp}
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}", "api-id": "ka10059",
                "cont-yn": "N", "next-key": "",
            }
            try:
                rr = requests.post(f"{_host()}{path}", headers=headers, json=body, timeout=10)
                d = rr.json() if rr.content else {}
                rc = str(d.get("return_code", ""))
                lists = [v for v in d.values() if isinstance(v, list)]
                n = len(lists[0]) if lists else 0
                head = json.dumps(lists[0][0], ensure_ascii=False)[:160] if n else "(빈 응답)"
                print(f"  stex_tp={tp}({name}): rc={rc} 행수={n} | {head}")
            except Exception as e:
                print(f"  stex_tp={tp}({name}): 예외 {e}")
        print("\n→ KRX와 NXT의 행수/값이 다르면 거래소별 수급 조회 가능.")
        print("  동일하면 stex_tp가 무시된 것 = NXT 투자자별 수급은 제공 안 됨.")


if __name__ == "__main__":
    main()
