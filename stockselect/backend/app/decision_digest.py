"""型態突破／強哥圖卡研究版＋趨勢持有 60 日的 LINE 日報。

新倉股數是 100 萬淨值／100 萬現金的規則試算，已持股納入限制。
交易帳沒有買進策略標籤；出場檢查是將本規則套用到未平倉買批的參考。
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from . import decision_center as dc, decision_themes, db, entry_conditions, execution, ledger, lin_bojin, swings
from .line_notify import NotificationError, fit_text

TAIPEI = timezone(timedelta(hours=8))
VERSION = "line-digest-v3"
PROFILE = "trend_hold/breakout/rs/lot1"
LIN_PROFILE = f"trend_hold/lin_bojin/{lin_bojin.VERSION}/rs/lot1"
STRATEGIES = {"breakout": "日線型態突破", lin_bojin.KEY: lin_bojin.LABEL}


def profile_for(gate):
    if gate not in STRATEGIES:
        raise NotificationError("通知策略設定不符")
    return LIN_PROFILE if gate == lin_bojin.KEY else PROFILE


def today():
    return datetime.now(TAIPEI).date()


def build_response(scan, holdings, market, gate="breakout"):
    # RS 排序不採分數校準修正，[] 避免獨立預覽建立／改寫歷史表。
    profile_for(gate)
    # eps 是行情日當時已公告的最新單季 EPS；先過濾再配置，避免不合格者占用名額。
    # 保留原始掃描筆數／健康狀態及完整持股限制，零候選仍是正常日報。
    eligible_scan = {**scan, "items": [item for item in scan.get("items", []) if positive_eps(item)]}
    response = dc.build_decision_response(eligible_scan, [], holdings, mode="trend_hold", gate=gate,
                                         ranking="rs", lot_size=1, limit=500, market=market)
    response["settings"]["latest_quarter_eps_gt"] = 0
    if gate == lin_bojin.KEY:
        entry_conditions.attach(response)
        lin_bojin.attach(response, eligible_scan)
    return response


def _f(value):
    return execution.number(value)


def positive_eps(item):
    eps = _f(item.get("eps"))
    return eps is not None and eps > 0


def validate_eps_filter(response):
    # 快照／輸出也檢查，防止舊快照或其他呼叫端繞過通知門檻。
    if (response.get("settings", {}).get("latest_quarter_eps_gt") != 0
            or any(not positive_eps(item) for item in response.get("items", []))):
        raise NotificationError("通知候選必須符合最新單季 EPS > 0；請重新產生通知快照")


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
    if response.get("gate") == lin_bojin.KEY:
        # 風險額度不足仍通知通過買點的股票；族群標籤沿用今日決策中心。
        decision_themes.attach_display_themes({"items": [x for x in response["items"] if x.get("state") == "ready"]
                                              + lin_bojin.observation_items(response)})
        return
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


def make_snapshot(response, requested_date, holding_checks=None):
    profile = profile_for(response.get("gate"))
    if response.get("mode") != "trend_hold" or response.get("settings", {}).get("ranking") != "rs":
        raise NotificationError("通知策略設定不符")
    validate_eps_filter(response)
    enrich_selected(response)
    return {"version": VERSION, "profile": profile, "requested_date": str(requested_date),
            "created_at": datetime.now(TAIPEI).isoformat(), "response": response,
            "holding_checks": holding_checks if holding_checks is not None else
                load_holding_checks(response["as_of"]) if response.get("as_of") else []}


def render_digest(snapshot, warning_jobs=()):
    response = snapshot["response"]
    validate_eps_filter(response)
    if response.get("gate") == lin_bojin.KEY:
        return render_lin_digest(snapshot, warning_jobs)
    selected = [x for x in response.get("items", []) if x.get("selected")]
    market = response.get("market") or {}
    regime = ("站上60日線，允許新倉" if market.get("above") is True else
              "低於或等於60日線，暫停新倉" if market.get("above") is False else "資料不足，暫停新倉")
    lines = ["📊 型態突破｜趨勢持有60日（實驗）", f"行情日期：{response.get('as_of') or '—'}",
             "✅ 決策資料完成", f"大盤：{regime}", "設定：RS排序、全部族群、均額2千萬+、近3日突破、最新單季EPS>0", "",
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
                  f"最新單季 EPS：{_f(item.get('eps')):g}元",
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
                lines.append("原因：沒有同時符合本策略與最新單季EPS>0的候選訊號。")
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


def render_lin_digest(snapshot, warning_jobs=()):
    response = snapshot["response"]
    ready = [x for x in response.get("items", []) if x.get("state") == "ready"]
    selected = [x for x in ready if x.get("selected")]
    observations = lin_bojin.observation_items(response)
    above = (response.get("market") or {}).get("above")
    regime = "站上60日線，允許新倉" if above is True else "低於或等於60日線，暫停新倉" if above is False else "資料不足，暫停新倉"
    lines = ["📊 強哥選股條件｜趨勢持有60日", "依圖卡整理的研究版，非本人官方公式；績效尚待驗證。",
             f"行情日期：{response.get('as_of') or '—'}", "✅ 決策資料完成", f"大盤：{regime}",
             "設定：RS排序、全部族群、均額2千萬+、最新單季EPS>0；未加籌碼或營收交集", "",
             f"【通過買點條件：{len(ready)}檔｜資金配置入選：{len(selected)}檔】"]
    # 已配置者優先，其他通過買點者仍依原 RS 排序列出，避免零部位被誤解為零買點。
    shown = selected + [x for x in ready if not x.get("selected")]
    for item in shown[:10]:
        info, plan = item.get("lin_bojin") or {}, item.get("position_plan") or {}
        themes = "／".join(t["name"] for t in item.get("display_themes", [])[:3]) or "族群待補"
        close = _f(item.get("raw_close")) or _f(item.get("close"))
        lines += [f"{item['stock_id']} {item.get('name') or ''}｜{themes}",
                  f"買點：{info.get('setup_label') or '—'}・{info.get('pattern_name') or '—'}",
                  f"RS：{item.get('rs_rating', '—')}｜今日量比：{info.get('volume_ratio', '—')}"
                  f"｜距頸線：{info.get('extension_pct', '—')}%",
                  f"最新單季 EPS：{_f(item.get('eps')):g}元",
                  f"固定頸線：{info.get('neckline', '—')}｜突破日期：{info.get('breakout_date') or '—'}",
                  f"收盤參考：{close:.2f}｜含滑價追價上限：{info.get('max_entry', '—')}",
                  (f"入選試算：{plan.get('suggested_shares', 0):,}股；成交後重算股數與8%停損"
                   if item.get("selected") else "未配置部位：" + (item.get("selection_reason") or "未通過帳戶限制")), ""]
    if len(shown) > 10:
        lines.append(f"另有 {len(shown) - 10} 檔，請到今日決策中心查看。")
    if not ready:
        lines.append("今日沒有同時通過買點條件與最新單季EPS>0的股票；接近頸線、尚未回測轉強或追價過遠均不算。")
    if not selected:
        lines.append("今日無配置入選新倉。")
        if ready:
            reasons = Counter(x.get("selection_reason") or "未通過帳戶限制" for x in ready)
            lines += [f"• {reason}（{n}檔）" for reason, n in reasons.most_common(3)]
    lines += ["", f"【提前觀察：{len(observations)}檔｜尚未確認買點】",
              "僅追蹤週線壓力下方0～8%；不配置部位，不是買進訊號。"]
    for item in observations[:5]:
        observed = item["lin_bojin"]["observation"]
        band = observed["pressure_band"]
        close = _f(item.get("raw_close")) or _f(item.get("close"))
        themes = "／".join(t["name"] for t in item.get("display_themes", [])[:2]) or "族群待補"
        lines += [f"{item['stock_id']} {item.get('name') or ''}｜{themes}｜{observed['label']}",
                  f"週壓力：{band['low']:g}～{band['high']:g}｜測試{band['touch_count']}次｜距上緣{observed['extension_pct']:+g}%",
                  f"收盤：{close:g}｜RS：{item.get('rs_rating', '—')}｜最新單季 EPS：{_f(item.get('eps')):g}元｜量比：{observed['volume_ratio']:g}"]
    if len(observations) > 5:
        lines.append(f"另有 {len(observations) - 5} 檔，請至今日決策中心的「提前觀察」查看。")
    if not observations:
        lines.append("今日無符合提前觀察條件的股票。")
    alerts = [c for c in snapshot.get("holding_checks", []) if c["alerts"]]
    lines += ["", f"【交易帳出場檢查：{len(alerts)}筆提醒】"]
    for check in alerts[:5]:
        lines += [f"{check['stock_id']} {check['name']}｜{check['buy_date']}買進、{check['shares']:g}股",
                  *["• " + alert for alert in check["alerts"]]]
    if len(alerts) > 5:
        lines.append(f"另有 {len(alerts) - 5} 筆，請至交易帳查看。")
    if not alerts:
        lines.append("交易帳無出場提醒；行情不足會另列提醒。")
    if warning_jobs:
        lines += ["", "⚠️ 其他更新工作未完成：" + "、".join(warning_jobs)]
    lines += ["", "日線型態或週線前高壓力帶：首次突破／回測再轉強／跳空突破擇一。",
              "只供次交易日開盤參考；含滑價成交價超過頸線5%就放棄。",
              "出場：成交價下8%停損；收盤低於50日線後次日開盤出；第60個交易日收盤到期。",
              "股數採淨值100萬／現金100萬試算，既有持股納入限制；未連券商。",
              "本策略獨立試算，請勿與另一份日報的股數直接相加。",
              "持股檢查套用交易帳各買批，未標記買進策略；盤後檢查，無盤中即時監控。",
              f"規則版本：{lin_bojin.VERSION}"]
    return fit_text("\n".join(lines))


def failure_text(requested_date, reasons, gate="breakout"):
    profile_for(gate)
    return fit_text("⚠️ 台股選股通知：更新未完成\n"
                    f"排程日期：{requested_date}\n策略：{STRATEGIES[gate]}＋趨勢持有60日\n"
                    + "\n".join("• " + reason for reason in reasons)
                    + "\n本次不提供新買點，請查看電腦排程紀錄；修復並重跑後可補送日報。")
