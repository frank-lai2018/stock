"""型態突破＋趨勢持有 60 日的 LINE 日報；沿用選股引擎，僅增加通知資料。

新倉股數是 100 萬淨值／100 萬現金的規則試算，已持股納入限制。
交易帳沒有買進策略標籤；出場檢查是將本規則套用到未平倉買批的參考。
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from . import decision_center as dc, db, execution, ledger, swings
from .line_notify import NotificationError, fit_text

TAIPEI = timezone(timedelta(hours=8))
VERSION = "line-digest-v1"
PROFILE = "trend_hold/breakout/rs/lot1"


def today():
    return datetime.now(TAIPEI).date()


def build_response(scan, holdings, market):
    # RS 排序不採分數校準修正，[] 避免獨立預覽建立／改寫歷史表。
    return dc.build_decision_response(scan, [], holdings, mode="trend_hold", gate="breakout",
                                      ranking="rs", lot_size=1, limit=500, market=market)


def _f(value):
    return execution.number(value)


def holding_checks(transactions, histories, calendar, as_of, names=None):
    """逐批檢查，不用平均成本取代各批成交價；除權息以還原因子對齊。

市場交易日從實際買進日（含）起算，第 59 日提醒次交易日收盤到期。
行情不完整時只報資料不足，不推導「無出場提醒」。
"""
    cutoff = execution.day(as_of)
    calendar = sorted({execution.day(d) for d in calendar if execution.day(d) <= cutoff})
    grouped = {}
    for txn in transactions:
        if execution.day(txn["trade_date"]) <= cutoff:
            grouped.setdefault(txn["stock_id"], []).append(txn)
    checks = []
    for sid, txns in grouped.items():
        position = ledger.build(txns)
        if position["net_shares"] <= 0:
            continue
        bars = sorted((b for b in histories.get(sid, []) if execution.day(b["trade_date"]) <= cutoff),
                      key=lambda b: execution.day(b["trade_date"]))
        by_date = {execution.day(b["trade_date"]): b for b in bars}
        current = by_date.get(cutoff)
        ma_dates = calendar[-50:]
        ma_bars = [by_date.get(d) for d in ma_dates]
        ma50 = (sum(_f(b.get("close")) for b in ma_bars) / 50
                if len(ma_bars) == 50 and all(b and _f(b.get("close")) for b in ma_bars) else None)
        state = execution.locked(current, bars[-2] if len(bars) > 1 else None) if current else "missing"
        for lot in position["open_lots"]:
            buy_date = execution.day(lot["date"])
            count = sum(d >= buy_date for d in calendar) if buy_date in calendar and calendar[-1] == cutoff else None
            check = {"stock_id": sid, "name": (names or {}).get(sid, sid), "buy_date": lot["date"],
                     "shares": lot["shares"], "buy_price": lot["price"], "holding_days": count,
                     "alerts": [], "incomplete": False, "stop": None}
            buy_bar = by_date.get(buy_date)
            buy_factor = execution.factor(buy_bar) if buy_bar else None
            current_factor = execution.factor(current) if current else None
            if not buy_factor or not current_factor or state == "missing" or buy_date not in calendar:
                check["incomplete"] = True
                check["alerts"].append("買進日或當日行情不足，出場條件無法完整檢查")
            else:
                stop_adj = lot["price"] * buy_factor * 0.92
                check["stop"] = round(stop_adj / current_factor, 2)
                if _f(current.get("low")) <= stop_adj:
                    check["alerts"].append(f"今日盤中已觸及8%停損參考 {check['stop']:.2f}；請核對實際委託")
                if ma50 is None:
                    check["incomplete"] = True
                    check["alerts"].append("50日均線資料不足，無法完整檢查趨勢出場")
                elif _f(current.get("close")) < ma50:
                    check["alerts"].append("收盤已低於50日均線；套用本規則為次交易日開盤出場")
                if state == "down":
                    check["alerts"].append("日K呈一價跌停，可能無法成交，需確認委託")
            if count is not None and count >= 60:
                check["alerts"].append(f"已達第{count}個交易日；本規則第60日收盤到期，請核對未平倉紀錄")
            elif count == 59:
                check["alerts"].append("次交易日為第60個交易日；本規則於該日收盤到期")
            elif count is None:
                check["incomplete"] = True
                check["alerts"].append("市場交易日資料不足，無法確認60日期限")
            checks.append(check)
    return checks


def load_holding_checks(as_of):
    if not db.query("SELECT to_regclass('public.trade_log') AS t")[0]["t"]:
        return []
    txns = db.query("SELECT id,stock_id,action,trade_date,shares,price,fee,tax FROM trade_log "
                    "WHERE trade_date<=%(d)s ORDER BY trade_date,id", {"d": as_of})
    by_id = {}
    for txn in txns:
        by_id.setdefault(txn["stock_id"], []).append(txn)
    positions = {sid: ledger.build(rows) for sid, rows in by_id.items()}
    positions = {sid: pos for sid, pos in positions.items() if pos["net_shares"] > 0}
    if not positions:
        return []
    since = min(execution.day(as_of) - timedelta(days=150),
                min(execution.day(pos["first_open_date"]) for pos in positions.values()))
    rows = db.query(
        "SELECT stock_id,trade_date,adj_open AS open,adj_high AS high,adj_low AS low,adj_close AS close,"
        " open AS raw_open,high AS raw_high,low AS raw_low,close AS raw_close,volume,amount "
        "FROM price_daily WHERE stock_id=ANY(%(ids)s) AND trade_date BETWEEN %(since)s AND %(d)s "
        "ORDER BY stock_id,trade_date", {"ids": list(positions), "since": since, "d": as_of})
    histories = {}
    for row in rows:
        histories.setdefault(row["stock_id"], []).append(row)
    calendar = [r["trade_date"] for r in db.query(
        "SELECT trade_date FROM market_index WHERE index_id='TWSE' "
        "AND trade_date BETWEEN %(since)s AND %(d)s ORDER BY trade_date", {"since": since, "d": as_of})]
    names = {r["stock_id"]: r["name"] for r in db.query(
        "SELECT stock_id,name FROM stock WHERE stock_id=ANY(%(ids)s)", {"ids": list(positions)})}
    return holding_checks(txns, histories, calendar, as_of, names)


def enrich_selected(response):
    """取原型態的突破量比，不改核心研究指紋或選股門檻。"""
    selected = [x for x in response["items"] if x.get("selected")]
    if not selected:
        return
    ids = [x["stock_id"] for x in selected]
    histories = {}
    for row in db.query(
        "SELECT * FROM (SELECT stock_id,trade_date,adj_open AS open,adj_high AS high,adj_low AS low,"
        "adj_close AS close,volume,amount,row_number() OVER(PARTITION BY stock_id ORDER BY trade_date DESC) rn "
        "FROM price_daily WHERE stock_id=ANY(%(ids)s) AND trade_date<=%(d)s) x "
        "WHERE rn<=150 ORDER BY stock_id,trade_date", {"ids": ids, "d": response["as_of"]}):
        histories.setdefault(row["stock_id"], []).append(row)
    for item in selected:
        strategy = next(s for s in item["strategies"] if s["key"] == "breakout")
        pattern = swings.ALL[strategy["pattern"]](histories.get(item["stock_id"], []), recent=3) or {}
        item["notification_vol_ratio"] = _f(pattern.get("vol_ratio"))
    tables = db.query("SELECT to_regclass('public.theme') AS t, to_regclass('public.stock_theme') AS st")[0]
    if not tables["t"] or not tables["st"]:
        return
    for row in db.query(
        "SELECT DISTINCT st.stock_id,t.name FROM stock_theme st JOIN theme t USING(theme_id) "
        "WHERE st.stock_id=ANY(%(ids)s) AND st.valid_to IS NULL AND t.is_active "
        "AND ((t.layer=2 AND st.status='confirmed') OR (t.layer=3 AND st.status IN ('confirmed','seed'))) "
        "ORDER BY st.stock_id,t.name", {"ids": ids}):
        item = next(x for x in selected if x["stock_id"] == row["stock_id"])
        item.setdefault("notification_themes", []).append(row["name"])


def make_snapshot(response, requested_date):
    if response.get("mode") != "trend_hold" or response.get("gate") != "breakout":
        raise NotificationError("通知策略設定不符")
    enrich_selected(response)
    return {"version": VERSION, "profile": PROFILE, "requested_date": str(requested_date),
            "created_at": datetime.now(TAIPEI).isoformat(), "response": response,
            "holding_checks": load_holding_checks(response["as_of"]) if response.get("as_of") else []}


def render_digest(snapshot, warning_jobs=()):
    response = snapshot["response"]
    selected = [x for x in response.get("items", []) if x.get("selected")]
    market = response.get("market") or {}
    regime = ("站上60日線，允許新倉" if market.get("above") is True else
              "低於或等於60日線，暫停新倉" if market.get("above") is False else "資料不足，暫停新倉")
    lines = ["📊 型態突破｜趨勢持有60日（實驗）", f"行情日期：{response.get('as_of') or '—'}",
             "✅ 決策資料完成", f"大盤：{regime}", "設定：RS排序、全部族群、均額2千萬+、近3日突破", "",
             f"【次交易日新買候選：{len(selected)}檔】"]
    for item in selected:
        strategy = next(s for s in item["strategies"] if s["key"] == "breakout")
        close = _f(item.get("raw_close")) or _f(item.get("close"))
        ratio = item.get("notification_vol_ratio")
        themes = "／".join(item.get("notification_themes", [])[:3]) or item.get("industry") or "—"
        lines += [f"{item['stock_id']} {item.get('name') or ''}｜{themes}",
                  f"型態：{strategy.get('pattern_name') or '型態突破'}｜訊號日：{strategy.get('signal_date') or '—'}",
                  f"RS：{item.get('rs_rating', '—')}｜突破分：{strategy.get('score', '—')}"
                  + (f"｜量比：{ratio:.2f}" if ratio is not None else ""),
                  f"收盤參考：{close:.2f}元；次交易日開盤進場",
                  f"試算：{item['position_plan']['suggested_shares']:,}股；成交後重算股數與8%停損",
                  "原因：型態突破達可執行，通過大盤、持股與資金限制", ""]
    if not selected:
        lines.append("今日無入選新倉。")
        if response.get("market_blocked"):
            lines.append("原因：大盤條件未通過。")
        else:
            reasons = Counter(x.get("selection_reason") or "未符合條件" for x in response.get("items", []) if not x.get("held"))
            lines += [f"• {reason}（{n}檔）" for reason, n in reasons.most_common(3)]
            if not reasons:
                lines.append("原因：沒有符合本策略的候選訊號。")
    checks = snapshot.get("holding_checks", [])
    alerts = [c for c in checks if c["alerts"]]
    lines += ["", f"【交易帳出場檢查：{len(alerts)}筆提醒】"]
    for check in alerts[:10]:
        lines += [f"{check['stock_id']} {check['name']}｜{check['buy_date']}買進、{check['shares']:g}股",
                  *["• " + alert for alert in check["alerts"]]]
    if len(alerts) > 10:
        lines.append(f"另有{len(alerts) - 10}筆，請至交易帳查看。")
    if not checks:
        lines.append("交易帳無未平倉持股。")
    elif not alerts:
        lines.append("目前未觸及本策略出場條件。")
    if warning_jobs:
        lines += ["", "⚠️ 其他更新工作未完成：" + "、".join(warning_jobs)]
    lines += ["", "出場：8%停損；收盤低於50日線後次日開盤出；第60個交易日收盤到期，不設固定停利。",
              "股數採淨值100萬／現金100萬試算，已持股納入限制；未連券商。",
              "出場檢查將本規則套用交易帳各買批；交易帳未標記買進策略。",
              "停損為盤後檢查，無盤中即時監控；鎖跌停可能延後成交。"]
    return fit_text("\n".join(lines))


def failure_text(requested_date, reasons):
    return fit_text("⚠️ 台股選股通知：更新未完成\n"
                    f"排程日期：{requested_date}\n策略：型態突破＋趨勢持有60日\n"
                    + "\n".join("• " + reason for reason in reasons)
                    + "\n本次不提供新買點，請查看電腦排程紀錄；修復並重跑後可補送日報。")
