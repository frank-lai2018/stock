"""連續組合模擬；現金、跨日持股／產業／風險限制，不重複買同股票。

持股價值採還原價的股利再投資口徑。未來出場路徑只在實際到達該日／時點
才釋放現金，未來報酬不參與選股、部位或現金判斷。
"""
import math

from . import decision_center as dc, execution


def run(calendar, by_date, bars_by_stock, markets, mode, gate, capital=1_000_000,
        lot_size=1, max_total_positions=10, max_total_risk_pct=4):
    calendar = sorted(calendar)
    if not by_date:
        return {"curve": [], "trades": [], "skipped": [], "open_positions": 0}
    first = min(by_date)
    bar_lookup = {sid: {execution.day(b["trade_date"]): b for b in bars}
                  for sid, bars in bars_by_stock.items()}
    cash, held, pending, trades, curve, skipped = float(capital), {}, [], [], [], []

    def value(pos, d, field="close"):
        rows = bar_lookup[pos["stock_id"]]
        current = rows.get(d)
        if current:
            pos["mark"] = float(current[field]) / pos["anchor"]
        return pos["notional"] * pos["mark"] / pos["fill"]["actual_entry"]

    def close(pos):
        nonlocal cash
        fill = pos["fill"]
        # simulate 已扣全程成本；買進已付一半，所以出場補回同一口徑。
        cash += pos["notional"] * (1 + fill["net_return"] + dc.ROUND_TRIP_COST / 2)
        trades.append({"stock_id": pos["stock_id"], "industry": pos["industry"],
                       "observed_date": pos["observed_date"], "shares": pos["shares"],
                       "notional": pos["notional"], **fill})
        del held[pos["stock_id"]]

    for d in calendar:
        if d < first:
            continue
        # 開盤出場的資金可以用於當日開盤新倉；盤中／收盤出場則不能。
        for pos in list(held.values()):
            if pos["fill"]["exit_date"] == d and pos["fill"].get("exit_timing") == "open":
                close(pos)
        for item, observed in pending:
            sid = item["stock_id"]
            if sid in held:
                continue
            strategy = next(s for s in item["strategies"] if s["key"] == item["lead_strategy"])
            spec = dc.execution_spec(item, strategy, mode)
            spec["observed_date"] = observed
            fill = execution.simulate(bars_by_stock.get(sid, []), spec, calendar)
            if fill["entry_date"] != d:
                skipped.append({"stock_id": sid, "observed_date": observed, "reason": fill.get("reason")})
                continue
            equity = cash + sum(value(p, d, "open") for p in held.values())
            risk_used = sum(p["risk"] for p in held.values())
            budget = max(0, min(equity * 0.0075, equity * max_total_risk_pct / 100 - risk_used))
            raw = fill["actual_entry_raw"]
            risk_pct = (fill["actual_entry"] - fill["actual_stop"]) / fill["actual_entry"]
            shares = math.floor(min(budget / (raw * risk_pct), equity * 0.25 / raw,
                                    cash / (raw * (1 + dc.ROUND_TRIP_COST / 2))))
            shares = shares // lot_size * lot_size
            industry = item["industry"]
            if (shares <= 0 or len(held) >= max_total_positions or
                    sum(p["industry"] == industry for p in held.values()) >= 2):
                skipped.append({"stock_id": sid, "observed_date": observed, "reason": "成交時資金、產業或總風險不足"})
                continue
            notional = shares * raw
            cash -= notional * (1 + dc.ROUND_TRIP_COST / 2)
            anchor = execution.factor(bar_lookup[sid][observed])
            held[sid] = {"stock_id": sid, "industry": industry, "observed_date": observed,
                         "shares": shares, "notional": notional, "risk": notional * risk_pct,
                         "fill": fill, "anchor": anchor, "mark": fill["actual_entry"]}
        pending = []
        for pos in list(held.values()):
            if pos["fill"]["exit_date"] == d:
                close(pos)
        market_value = sum(value(pos, d) for pos in held.values())
        equity = cash + market_value
        curve.append({"date": d, "equity": equity, "cash": cash, "positions": len(held),
                      "exposure": market_value / equity if equity else 0,
                      "risk_pct": sum(p["risk"] for p in held.values()) / equity * 100 if equity else 0})
        if d in by_date:
            counts = {}
            for pos in held.values():
                counts[pos["industry"]] = counts.get(pos["industry"], 0) + 1
            holdings = {"items": list(held.values()), "stock_ids": set(held), "industry_counts": counts,
                        "risk_amount": sum(p["risk"] for p in held.values())}
            response = dc.build_decision_response(
                {"as_of": d.isoformat(), "items": by_date[d], "scanned": len(by_date[d])}, [], holdings,
                capital=equity, available_capital=cash, lot_size=lot_size, mode=mode, gate=gate,
                market=markets.get(d), limit=500, max_total_positions=max_total_positions,
                max_total_risk_pct=max_total_risk_pct)
            pending = [(item, d) for item in response["items"] if item["selected"]]
    return {"curve": curve, "trades": trades, "skipped": skipped, "open_positions": len(held),
            "accounting": "adjusted-total-return-dividend-reinvestment", "lot_size": lot_size}
