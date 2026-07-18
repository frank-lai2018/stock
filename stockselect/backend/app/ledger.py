"""交易帳 FIFO 損益引擎：由單次買/賣紀錄，配對算出已實現損益與未平倉部位。

單位：shares=股數、price=每股價、fee/tax=該筆總額（元）。
買進手續費攤入該批成本，賣出時依配對比例實現；賣出手續費/證交稅於該賣單實現。
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def build(txns):
    """txns：同一檔的交易 list[dict]（含 id/action/trade_date/shares/price/fee/tax）。
    回傳：未平倉彙總 + 已實現明細（closed round-trips）。"""
    ordered = sorted(txns, key=lambda t: (t["trade_date"], 0 if t["action"] == "buy" else 1, t["id"]))
    fifo = []          # 未配對買批：[shares, price, date, fee_per_share]
    realized = 0.0
    closed = []
    buy_shares = sell_shares = 0.0
    for t in ordered:
        shares = _f(t["shares"]); price = _f(t["price"])
        fee = _f(t.get("fee")); tax = _f(t.get("tax"))
        if shares <= 0:
            continue
        if t["action"] == "buy":
            buy_shares += shares
            fifo.append([shares, price, t["trade_date"], fee / shares])
        else:  # sell（FIFO 配對最早的買批）
            sell_shares += shares
            remain = shares
            sell_cost_per = (fee + tax) / shares     # 賣出成本攤每股
            while remain > 1e-9 and fifo:
                lot = fifo[0]
                take = min(remain, lot[0])
                buy_price, buy_fee_per = lot[1], lot[3]
                pnl = (price - buy_price) * take - (sell_cost_per + buy_fee_per) * take
                realized += pnl
                days = (t["trade_date"] - lot[2]).days
                closed.append({
                    "shares": round(take, 3), "buy_date": lot[2].isoformat(), "buy_price": buy_price,
                    "sell_date": t["trade_date"].isoformat(), "sell_price": price,
                    "pnl": round(pnl), "ret_pct": (price / buy_price - 1) if buy_price else None,
                    "days": days,
                })
                lot[0] -= take; remain -= take
                if lot[0] <= 1e-9:
                    fifo.pop(0)
            # remain>0 代表賣超（無對應買批）→ 忽略多賣部分

    net_shares = sum(l[0] for l in fifo)
    cost_remaining = sum(l[0] * l[1] for l in fifo)          # 每股 × 股數
    avg_cost = (cost_remaining / net_shares) if net_shares > 1e-9 else None
    return {
        "net_shares": round(net_shares, 3),
        "avg_cost": round(avg_cost, 4) if avg_cost is not None else None,
        "realized_pnl": round(realized),
        "closed": closed,
        "buy_shares": round(buy_shares, 3), "sell_shares": round(sell_shares, 3),
    }
