# scripts/review_parser.py
"""키움 HTS '기간별 주문체결상세(통합)' CSV 파싱 + 매매별 P&L 계산 (FIFO)"""

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Order:
    order_no:   str
    date:       str
    time:       str
    stock_code: str
    stock_name: str
    side:       str    # '매수' / '매도'
    total_qty:  int
    avg_price:  float
    order_type: str    # 주문유형구분 원문


@dataclass
class Trade:
    stock_name:  str
    stock_code:  str
    buy_date:    Optional[str]
    buy_time:    Optional[str]
    buy_price:   Optional[float]
    sell_date:   Optional[str]
    sell_time:   Optional[str]
    sell_price:  Optional[float]
    qty:         int
    pnl:         Optional[float]      # 원 (수수료 미반영)
    pnl_pct:     Optional[float]      # %
    note:        str = ""             # '이전기간매수' / '미청산' / ''


def parse_orders(csv_path: str) -> list[Order]:
    """
    CSV → 주문번호별 집계 → Order 목록 반환.
    분할체결(같은 주문번호 여러 행)은 수량 합산 + 가중평균 단가로 통합.
    미체결(체결수량=0)은 제외.
    """
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    data = rows[2:]  # 헤더 2행 제거

    agg: dict = defaultdict(lambda: {
        "qty": 0, "value": 0.0,
        "side": "", "code": "", "name": "", "date": "", "time": "", "order_type": "",
    })

    for i in range(0, len(data) - 1, 2):
        row_a = data[i]
        row_b = data[i + 1]

        order_no   = row_a[1]
        order_type = row_a[5]
        code       = row_a[3]
        side       = "매도" if "매도" in order_type else "매수"

        qty_str   = row_b[4].replace(",", "").strip()
        price_str = row_b[5].replace(",", "").strip()
        qty   = int(qty_str)   if qty_str   else 0
        price = float(price_str) if price_str else 0.0

        o = agg[order_no]
        o["qty"]       += qty
        o["value"]     += qty * price
        o["side"]       = side
        o["code"]       = code
        o["name"]       = row_b[1]
        o["order_type"] = order_type
        if not o["date"]:
            o["date"] = row_b[0]
            o["time"] = row_b[9]

    orders = []
    for no, o in agg.items():
        if o["qty"] == 0:
            continue
        avg = o["value"] / o["qty"]
        orders.append(Order(
            order_no   = no,
            date       = o["date"],
            time       = o["time"],
            stock_code = o["code"],
            stock_name = o["name"],
            side       = o["side"],
            total_qty  = o["qty"],
            avg_price  = round(avg, 2),
            order_type = o["order_type"],
        ))

    return orders


def calc_trades(orders: list[Order]) -> list[Trade]:
    """
    FIFO 방식으로 매수→매도 매칭 → Trade 목록 반환.
    - 매칭 가능: P&L 계산
    - 매수 없이 매도만 있음: note='이전기간매수' (P&L=None)
    - 매도 없이 매수만 있음: note='미청산' (P&L=None)
    """
    by_stock: dict[str, list[Order]] = defaultdict(list)
    for o in orders:
        by_stock[o.stock_code].append(o)

    trades: list[Trade] = []

    for code, stock_orders in by_stock.items():
        stock_orders.sort(key=lambda o: (o.date, o.time))
        stock_name = stock_orders[0].stock_name

        buy_queue: list[dict] = []

        for o in stock_orders:
            if o.side == "매수":
                buy_queue.append({
                    "price": o.avg_price, "qty": o.total_qty,
                    "date": o.date, "time": o.time,
                })
            else:
                remaining = o.total_qty

                while remaining > 0:
                    if not buy_queue:
                        trades.append(Trade(
                            stock_name=stock_name, stock_code=code,
                            buy_date=None, buy_time=None, buy_price=None,
                            sell_date=o.date, sell_time=o.time, sell_price=o.avg_price,
                            qty=remaining, pnl=None, pnl_pct=None,
                            note="이전기간매수",
                        ))
                        remaining = 0
                        break

                    bq      = buy_queue[0]
                    matched = min(bq["qty"], remaining)
                    pnl     = (o.avg_price - bq["price"]) * matched
                    pnl_pct = (o.avg_price / bq["price"] - 1) * 100 if bq["price"] > 0 else None

                    trades.append(Trade(
                        stock_name=stock_name, stock_code=code,
                        buy_date=bq["date"], buy_time=bq["time"], buy_price=bq["price"],
                        sell_date=o.date,    sell_time=o.time,    sell_price=o.avg_price,
                        qty=matched,
                        pnl=round(pnl, 0),
                        pnl_pct=round(pnl_pct, 2) if pnl_pct is not None else None,
                        note="",
                    ))

                    bq["qty"]  -= matched
                    remaining  -= matched
                    if bq["qty"] == 0:
                        buy_queue.pop(0)

        for bq in buy_queue:
            if bq["qty"] > 0:
                trades.append(Trade(
                    stock_name=stock_name, stock_code=code,
                    buy_date=bq["date"], buy_time=bq["time"], buy_price=bq["price"],
                    sell_date=None, sell_time=None, sell_price=None,
                    qty=bq["qty"], pnl=None, pnl_pct=None,
                    note="미청산",
                ))

    trades.sort(key=lambda t: (t.sell_date or t.buy_date or "", t.sell_time or t.buy_time or ""))
    return trades


def summarize(trades: list[Trade]) -> dict:
    """완결 거래(P&L 있는 것)만 통계 산출"""
    closed = [t for t in trades if t.pnl is not None]
    if not closed:
        return {}

    wins   = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    evens  = [t for t in closed if t.pnl == 0]

    total_pnl  = sum(t.pnl for t in closed)
    win_rate   = len(wins) / len(closed) * 100 if closed else 0
    avg_win    = sum(t.pnl for t in wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(t.pnl for t in losses) / len(losses) if losses else 0
    best       = max(closed, key=lambda t: t.pnl)
    worst      = min(closed, key=lambda t: t.pnl)

    # 종목별 P&L
    by_stock: dict[str, float] = defaultdict(float)
    for t in closed:
        by_stock[t.stock_name] += t.pnl
    stock_pnl = sorted(by_stock.items(), key=lambda x: -x[1])

    return {
        "total_pnl":   total_pnl,
        "trade_count": len(closed),
        "win_count":   len(wins),
        "loss_count":  len(losses),
        "even_count":  len(evens),
        "win_rate":    round(win_rate, 1),
        "avg_win":     round(avg_win, 0),
        "avg_loss":    round(avg_loss, 0),
        "best_trade":  best,
        "worst_trade": worst,
        "stock_pnl":   stock_pnl,
    }
