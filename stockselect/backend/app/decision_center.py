"""今日決策中心：合併突破與裸 K、追蹤分數成效、產生部位與組合篩選。

設計原則：
- 規則分數只負責排序，不把型態平均績效冒充成「此分數的勝率」。
- pattern_backtest 是立即可用的型態先驗；decision_signal_log 則從每天實際候選累積
  分數區間的前瞻結果，兩者在 API 中分開呈現。
- 同一批 K 棒只查一次，同時跑突破與裸 K，避免兩個既有端點各掃一次全市場。

兩種模式（2026-10 依 2024-07～2026-09 回測改版，結果見 動能分析設計.md §9）：
- momentum（預設）：可執行訊號＋趨勢模板成立＋加權指數站上 60 日線才開新倉，依 RS 評等排序；
  出場一律進場價下 8% 停損、不設目標、第 20 個交易日收盤到期。裸 K 只在通過動能篩選的股票上當進場時機。
- classic：原始規則（共識 → 決策分排序，出場用各策略自己的停損／目標），保留對照。
兩種模式的追蹤紀錄以 model_version 分開，互不混算。
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import date

from . import db, ledger, price_action, swings
from .breakout_rank import score_breakout


MODEL_VERSION = "decision-center-v1"            # 原始規則（classic）
MOMENTUM_VERSION = "decision-center-v2-momentum"
MODES = {"classic": MODEL_VERSION, "momentum": MOMENTUM_VERSION}
DEFAULT_MODE = "momentum"
HORIZON = 20
ROUND_TRIP_COST = 0.006
MOMENTUM_STOP_PCT = 0.08       # 動能模式固定停損：進場價下 8%（回測 −8% 與 −10% 幾乎相同，取部位較大的 8%）
MARKET_INDEX = "TWSE"          # 加權股價指數；market_index 的 TAIEX 是報酬指數，2026-07 後已停更
MARKET_MA_DAYS = 60

_ensured = False


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def score_bucket(score):
    """以 10 分為一組；100 分仍歸在 90-100，避免單獨一格。"""
    s = max(0, min(100, int(_f(score, 0) or 0)))
    lo = min((s // 10) * 10, 90)
    return f"{lo}-{100 if lo == 90 else lo + 9}"


def normalize_mode(mode):
    return mode if mode in MODES else DEFAULT_MODE


def _wilson(wins, n, z=1.96):
    if not n:
        return None, None
    p = wins / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return round(max(0, mid - half) * 100, 1), round(min(1, mid + half) * 100, 1)


def ensure_tables():
    global _ensured
    if _ensured:
        return
    db.execute(
        "CREATE TABLE IF NOT EXISTS decision_signal_log ("
        " strategy VARCHAR(24) NOT NULL, stock_id VARCHAR(16) NOT NULL,"
        " observed_date DATE NOT NULL, model_version VARCHAR(40) NOT NULL,"
        " decision_status VARCHAR(16), score NUMERIC, score_bucket VARCHAR(12),"
        " pattern VARCHAR(40), entry NUMERIC, stop NUMERIC, target NUMERIC,"
        " horizon INT NOT NULL DEFAULT 20, cost_pct NUMERIC NOT NULL DEFAULT 0.006,"
        " outcome_status VARCHAR(12) NOT NULL DEFAULT 'pending',"
        " outcome_date DATE, outcome_r NUMERIC, outcome_days INT,"
        " created_at TIMESTAMP DEFAULT now(), updated_at TIMESTAMP DEFAULT now(),"
        " PRIMARY KEY(strategy,stock_id,observed_date,model_version))")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_signal_pending "
        "ON decision_signal_log(outcome_status,observed_date)")
    # 舊版只有策略校準所需欄位；以下欄位保存當時的決策畫面，供歷史追蹤回看。
    db.execute(
        "ALTER TABLE decision_signal_log "
        "ADD COLUMN IF NOT EXISTS candidate_state VARCHAR(16),"
        "ADD COLUMN IF NOT EXISTS decision_score NUMERIC,"
        "ADD COLUMN IF NOT EXISTS consensus_count INT,"
        "ADD COLUMN IF NOT EXISTS lead_strategy VARCHAR(24),"
        "ADD COLUMN IF NOT EXISTS is_selected BOOLEAN,"
        "ADD COLUMN IF NOT EXISTS selection_reason TEXT,"
        "ADD COLUMN IF NOT EXISTS suggested_shares NUMERIC,"
        "ADD COLUMN IF NOT EXISTS position_value NUMERIC,"
        "ADD COLUMN IF NOT EXISTS snapshot JSONB")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_signal_observed "
        "ON decision_signal_log(observed_date DESC,stock_id)")
    _ensured = True


def _pattern_backtests():
    exists = db.query("SELECT to_regclass('public.pattern_backtest') AS t")[0]["t"]
    if not exists:
        return {}
    rows = db.query(
        "SELECT pattern,n,win_rate,avg_ret,median_ret,avg_excess,computed_at "
        "FROM pattern_backtest WHERE horizon=20")
    return {r["pattern"]: dict(r) for r in rows}


def market_regime(as_of=None):
    """加權指數與 60 日均線的相對位置；動能模式只在指數站上 60 日線時開新倉。

    回測期間跌破 60 日線時開新倉的突破訊號，20 日超額為 0、平均 −0.87R；站上時 +2.30%。
    資料不足時 above=None（不擋），由呼叫端顯示「大盤資料不足」。
    """
    params = {"id": MARKET_INDEX, "n": MARKET_MA_DAYS}
    cond = ""
    if as_of:
        cond = " AND trade_date <= %(d)s"
        params["d"] = as_of
    rows = db.query("SELECT trade_date, close FROM market_index WHERE index_id=%(id)s" + cond +
                    " ORDER BY trade_date DESC LIMIT %(n)s", params)
    base = {"index": MARKET_INDEX, "name": "加權指數", "ma_days": MARKET_MA_DAYS}
    if not rows:
        return {**base, "date": None, "close": None, "ma": None, "above": None, "gap_pct": None}
    close = float(rows[0]["close"])
    if len(rows) < MARKET_MA_DAYS:
        return {**base, "date": _iso(rows[0]["trade_date"]), "close": round(close, 2),
                "ma": None, "above": None, "gap_pct": None}
    ma = sum(float(r["close"]) for r in rows) / len(rows)
    return {**base, "date": _iso(rows[0]["trade_date"]), "close": round(close, 2), "ma": round(ma, 2),
            "above": close > ma, "gap_pct": round((close / ma - 1) * 100, 2)}


def _atr14(bars):
    if len(bars) < 15:
        return None
    values = []
    for prev, cur in zip(bars[-15:-1], bars[-14:]):
        pc = float(prev["close"])
        hi, lo = float(cur["high"]), float(cur["low"])
        values.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return round(sum(values) / len(values), 4) if values else None


def _breakout_strategy(snapshot, bars, recent, backtests):
    for key in swings.BULL_KEYS:
        breakout = swings.ALL[key](bars, recent=recent)
        if not breakout or breakout.get("dir") == "bear":
            continue
        breakout["atr14"] = _atr14(bars)
        breakout["current_adj_close"] = round(float(bars[-1]["close"]), 4)
        row = {**snapshot, "breakout": breakout, "pattern": key}
        decision = score_breakout(row, backtests.get(key))
        if decision["status"] == "skip":
            return None
        prior = backtests.get(key)
        pattern_prior = None
        if prior:
            pattern_prior = {
                "source": "pattern_20d",
                "n": int(prior.get("n") or 0),
                "positive_rate": _f(prior.get("win_rate")),
                "avg_ret_pct": _f(prior.get("avg_ret")),
                "median_ret_pct": _f(prior.get("median_ret")),
                "avg_excess_pct": _f(prior.get("avg_excess")),
                "computed_at": _iso(prior.get("computed_at")),
            }
        return {
            "key": "breakout", "label": "型態突破", "score": decision["score"],
            "status": decision["status"], "signal_state": "triggered",
            "pattern": key, "pattern_name": swings.PATTERN_NAMES[key],
            "entry": decision["entry"], "stop": decision["stop"],
            "target": decision["target"], "risk_pct": decision["risk_pct"],
            "rr": decision["rr"], "blockers": decision["blockers"],
            "notes": decision["notes"], "parts": decision["parts"],
            "signal_date": breakout.get("breakout_date"), "pattern_prior": pattern_prior,
        }
    return None


def _price_action_strategy(bars, lookback, expiry):
    decision = price_action.analyze(bars, lookback, expiry)
    if not decision or decision.get("direction") != "bull" or decision.get("conclusion") == "skip":
        return None
    return {
        "key": "price_action", "label": "裸 K", "score": decision["score"],
        "status": decision["conclusion"], "signal_state": decision["status"],
        "pattern": decision["pattern"], "pattern_name": decision["pattern_name"],
        "entry": decision["entry"], "stop": decision["stop"],
        "target": decision["target"], "risk_pct": decision["risk_pct"],
        "rr": decision["rr"], "blockers": decision["blockers"],
        "notes": decision["notes"], "parts": decision["parts"],
        "signal_date": decision["signal_date"], "trigger_date": decision["trigger_date"],
        "structure_name": decision["structure_name"], "locations": decision["locations"],
        "pattern_prior": None,
    }


def _candidate(snapshot, strategies, current_close=None):
    status_rank = {"priority": 4, "waiting": 3, "watch": 2, "skip": 0}
    lead = max(strategies, key=lambda x: (status_rank.get(x["status"], 0), x["score"]))
    ready = any(x["status"] == "priority" for x in strategies)
    waiting = not ready and any(x["status"] == "waiting" for x in strategies)
    state = "ready" if ready else "waiting" if waiting else "watch"
    consensus_bonus = 6 if len(strategies) > 1 else 0
    return {
        "stock_id": snapshot["stock_id"], "name": snapshot.get("name"),
        "industry": snapshot.get("industry") or "其他",
        # 停損／目標都由還原 K 線產生，進場基準必須使用同一價格座標。
        "close": _f(current_close, _f(snapshot.get("close"))),
        "amt20": _f(snapshot.get("amt20")), "rs_rating": _f(snapshot.get("rs_rating")),
        "eps": _f(snapshot.get("eps")), "eps_ttm": _f(snapshot.get("eps_ttm")),
        "eps_yoy": _f(snapshot.get("eps_yoy")), "rev_yoy": _f(snapshot.get("rev_yoy")),
        "gross_margin": _f(snapshot.get("gross_margin")),
        "gross_margin_chg": _f(snapshot.get("gross_margin_chg")),
        "trend_template": bool(snapshot.get("trend_template")),
        "state": state, "consensus_count": len(strategies),
        "base_score": max(x["score"] for x in strategies),
        "consensus_bonus": consensus_bonus, "calibration_adjustment": 0,
        "decision_score": round(min(100, max(x["score"] for x in strategies) + consensus_bonus), 1),
        "lead_strategy": lead["key"], "strategies": strategies,
    }


def scan_market(min_amt=20_000_000, recent=3, lookback=5, expiry=5,
                eps_min=None, revenue_yoy_min=None, gross_margin_chg_min=None):
    """共享一次 150 根 K 棒，合併多方突破與多方裸 K 候選。"""
    cond = ["in_universe", "security_type='stock'", "amt20 >= %(amt)s"]
    params = {"amt": max(0, int(min_amt))}
    if eps_min is not None:
        cond.append("eps >= %(eps)s"); params["eps"] = float(eps_min)
    if revenue_yoy_min is not None:
        cond.append("rev_yoy >= %(rev)s"); params["rev"] = float(revenue_yoy_min)
    if gross_margin_chg_min is not None:
        cond.append("gross_margin_chg >= %(gm)s"); params["gm"] = float(gross_margin_chg_min)
    rows = db.query(f"SELECT * FROM mv_stock_snapshot WHERE {' AND '.join(cond)}", params)
    snapshots = {r["stock_id"]: dict(r) for r in rows}
    if not snapshots:
        return {"as_of": None, "items": [], "scanned": 0}
    bars_rows = db.query(
        "SELECT stock_id,trade_date,adj_open AS open,adj_high AS high,adj_low AS low,"
        " adj_close AS close,volume FROM ("
        " SELECT stock_id,trade_date,adj_open,adj_high,adj_low,adj_close,volume,"
        " row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) rn"
        " FROM price_daily WHERE stock_id=ANY(%(ids)s)) z"
        " WHERE rn<=150 ORDER BY stock_id,trade_date", {"ids": list(snapshots)})
    grouped = {}
    for row in bars_rows:
        grouped.setdefault(row["stock_id"], []).append(row)
    backtests = _pattern_backtests()
    out = []
    for sid, bars in grouped.items():
        if len(bars) < 30:
            continue
        snap = snapshots[sid]
        strategies = []
        breakout = _breakout_strategy(snap, bars, max(1, min(int(recent), 25)), backtests)
        if breakout:
            strategies.append(breakout)
        pa = _price_action_strategy(bars, max(1, min(int(lookback), 10)),
                                    max(1, min(int(expiry), 10)))
        if pa:
            strategies.append(pa)
        if strategies:
            out.append(_candidate(snap, strategies, bars[-1].get("close")))
    out.sort(key=lambda x: (x["state"] == "ready", x["consensus_count"],
                            x["decision_score"], x.get("amt20") or 0), reverse=True)
    as_of = next((r.get("as_of_date") for r in rows if r.get("as_of_date")), None)
    return {"as_of": _iso(as_of), "items": out, "scanned": len(snapshots)}


def settle_pending(as_of=None):
    """用觀察日後最多 20 根 K 棒結算；同根同碰目標與停損時保守算停損。

    兩種模式一起結算（每筆有自己的 entry／stop／target／horizon）；target 為 NULL（動能模式）
    時沒有「先達目標」，只會停損或到期。
    """
    ensure_tables()
    pending = db.query(
        "SELECT strategy,stock_id,observed_date,model_version,entry,stop,target,horizon,cost_pct "
        "FROM decision_signal_log WHERE outcome_status='pending'")
    if not pending:
        return 0
    cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    ids = sorted({r["stock_id"] for r in pending})
    since = min(r["observed_date"] for r in pending)
    bars = db.query(
        "SELECT stock_id,trade_date,adj_high AS high,adj_low AS low,adj_close AS close "
        "FROM price_daily WHERE stock_id=ANY(%(ids)s) AND trade_date>%(since)s "
        "ORDER BY stock_id,trade_date", {"ids": ids, "since": since})
    by = {}
    for bar in bars:
        if cutoff and bar["trade_date"] > cutoff:
            continue
        by.setdefault(bar["stock_id"], []).append(bar)
    updates = []
    for signal in pending:
        future = [b for b in by.get(signal["stock_id"], []) if b["trade_date"] > signal["observed_date"]]
        horizon = int(signal["horizon"] or HORIZON)
        entry, stop = float(signal["entry"]), float(signal["stop"])
        target = _f(signal["target"])
        unit = entry - stop
        if unit <= 0:
            continue
        outcome = None
        for i, bar in enumerate(future[:horizon], 1):
            # 同一根無法知道先後，固定採較保守的停損。
            if float(bar["low"]) <= stop:
                outcome = ("loss", bar["trade_date"], stop, i); break
            if target is not None and float(bar["high"]) >= target:
                outcome = ("win", bar["trade_date"], target, i); break
        if outcome is None and len(future) >= horizon:
            bar = future[horizon - 1]
            outcome = ("timeout", bar["trade_date"], float(bar["close"]), horizon)
        if outcome is None:
            continue
        status, out_date, exit_price, days = outcome
        cost = float(signal.get("cost_pct") or ROUND_TRIP_COST)
        net_return = exit_price / entry - 1 - cost
        risk_pct = unit / entry
        outcome_r = net_return / risk_pct
        updates.append((status, out_date, round(outcome_r, 4), days,
                        signal["strategy"], signal["stock_id"], signal["observed_date"], signal["model_version"]))
    if updates:
        db.execute_many(
            "UPDATE decision_signal_log AS d SET outcome_status=v.status,outcome_date=v.outcome_date,"
            " outcome_r=v.outcome_r,outcome_days=v.outcome_days,updated_at=now() FROM (VALUES %s) "
            "AS v(status,outcome_date,outcome_r,outcome_days,strategy,stock_id,observed_date,model_version) "
            "WHERE d.strategy=v.strategy AND d.stock_id=v.stock_id AND d.observed_date=v.observed_date "
            "AND d.model_version=v.model_version",
            updates,
            template="(%s,%s::date,%s::numeric,%s::int,%s,%s,%s::date,%s)")
    return len(updates)


def record_candidates(scan, response=None, mode=DEFAULT_MODE):
    """保存今天已觸發的觀察與決策快照；同日重整不覆寫第一份快照。

    scan 保留全市場候選，用於策略校準；response 是套用資金、持股與產業限制後
    的畫面結果，用於保存「當時為何入選／未入選」及建議部位。舊呼叫端只傳
    scan 仍可正常累積訊號，只是沒有完整的畫面快照。
    mode=momentum 時每筆一律記成「進場價下 8% 停損、不設目標、20 日到期」，與畫面上的交易計畫一致。
    """
    ensure_tables()
    observed = scan.get("as_of")
    if not observed:
        return 0
    mode = normalize_mode(mode)
    version = MODES[mode]
    rendered = {item["stock_id"]: item for item in (response or {}).get("items", [])}
    values = []
    for item in scan.get("items", []):
        current = _f(item.get("close"))
        shown = rendered.get(item["stock_id"], item)
        plan = shown.get("position_plan") or {}
        snapshot = json.dumps(shown, ensure_ascii=False, default=_iso)
        for strategy in item.get("strategies", []):
            if strategy.get("signal_state") != "triggered" or strategy.get("status") not in ("priority", "watch"):
                continue
            if mode == "momentum":
                stop = round(current * (1 - MOMENTUM_STOP_PCT), 4) if current else None
                target = None
                if not current or not stop or not (0 < stop < current):
                    continue
            else:
                stop, target = _f(strategy.get("stop")), _f(strategy.get("target"))
                if not current or not stop or not target or not (0 < stop < current < target):
                    continue
            values.append((strategy["key"], item["stock_id"], observed, version,
                           strategy["status"], strategy["score"], score_bucket(strategy["score"]),
                           strategy.get("pattern"), current, stop, target, HORIZON, ROUND_TRIP_COST,
                           shown.get("state"), shown.get("decision_score"),
                           shown.get("consensus_count"), shown.get("lead_strategy"),
                           shown.get("selected"), shown.get("selection_reason"),
                           plan.get("suggested_shares"), plan.get("position_value"), snapshot))
    if not values:
        return 0
    before = db.query(
        "SELECT count(*)::int AS n FROM decision_signal_log "
        "WHERE observed_date=%(d)s AND model_version=%(v)s",
        {"d": observed, "v": version})[0]["n"]
    db.execute_many(
        "INSERT INTO decision_signal_log "
        "(strategy,stock_id,observed_date,model_version,decision_status,score,score_bucket,pattern,"
        " entry,stop,target,horizon,cost_pct,candidate_state,decision_score,consensus_count,"
        " lead_strategy,is_selected,selection_reason,suggested_shares,position_value,snapshot) "
        "VALUES %s ON CONFLICT (strategy,stock_id,observed_date,model_version) DO UPDATE SET "
        " candidate_state=COALESCE(decision_signal_log.candidate_state,EXCLUDED.candidate_state),"
        " decision_score=COALESCE(decision_signal_log.decision_score,EXCLUDED.decision_score),"
        " consensus_count=COALESCE(decision_signal_log.consensus_count,EXCLUDED.consensus_count),"
        " lead_strategy=COALESCE(decision_signal_log.lead_strategy,EXCLUDED.lead_strategy),"
        " is_selected=COALESCE(decision_signal_log.is_selected,EXCLUDED.is_selected),"
        " selection_reason=COALESCE(decision_signal_log.selection_reason,EXCLUDED.selection_reason),"
        " suggested_shares=COALESCE(decision_signal_log.suggested_shares,EXCLUDED.suggested_shares),"
        " position_value=COALESCE(decision_signal_log.position_value,EXCLUDED.position_value),"
        " snapshot=COALESCE(decision_signal_log.snapshot,EXCLUDED.snapshot),updated_at=now()",
        values,
        template="(%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                 "%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)")
    after = db.query(
        "SELECT count(*)::int AS n FROM decision_signal_log "
        "WHERE observed_date=%(d)s AND model_version=%(v)s",
        {"d": observed, "v": version})[0]["n"]
    return max(0, int(after) - int(before))


def decision_history(stock_id=None, strategy=None, outcome_status=None,
                     date_from=None, date_to=None, selected_only=False, limit=500, mode=DEFAULT_MODE):
    """讀取決策歷史，並用最新收盤估算尚未結算訊號的目前 R。"""
    ensure_tables()
    mode = normalize_mode(mode)
    version = MODES[mode]
    latest = db.query("SELECT max(trade_date) AS d FROM price_daily")[0]["d"]
    settle_pending(latest)

    cond = ["d.model_version=%(version)s"]
    params = {"version": version, "limit": max(1, min(int(limit), 1000))}
    if stock_id:
        cond.append("d.stock_id=%(stock_id)s")
        params["stock_id"] = str(stock_id).strip()
    if strategy in ("breakout", "price_action"):
        cond.append("d.strategy=%(strategy)s")
        params["strategy"] = strategy
    if outcome_status in ("pending", "win", "loss", "timeout"):
        cond.append("d.outcome_status=%(outcome)s")
        params["outcome"] = outcome_status
    if date_from:
        cond.append("d.observed_date >= %(date_from)s::date")
        params["date_from"] = _iso(date_from)
    if date_to:
        cond.append("d.observed_date <= %(date_to)s::date")
        params["date_to"] = _iso(date_to)
    if selected_only:
        cond.append("d.is_selected IS TRUE")
    where = " AND ".join(cond)

    summary = db.query(
        "SELECT count(*)::int AS total,"
        " count(*) FILTER (WHERE outcome_status='pending')::int AS pending,"
        " count(*) FILTER (WHERE outcome_status='win')::int AS win,"
        " count(*) FILTER (WHERE outcome_status='loss')::int AS loss,"
        " count(*) FILTER (WHERE outcome_status='timeout')::int AS timeout,"
        " count(*) FILTER (WHERE is_selected IS TRUE)::int AS selected,"
        " avg(outcome_r) FILTER (WHERE outcome_status<>'pending') AS avg_r "
        f"FROM decision_signal_log d WHERE {where}", params)[0]
    rows = db.query(
        "SELECT d.strategy,d.stock_id,d.observed_date,d.model_version,d.decision_status,"
        "d.score,d.score_bucket,d.pattern,d.entry,d.stop,d.target,d.horizon,d.cost_pct,"
        "d.outcome_status,d.outcome_date,d.outcome_r,d.outcome_days,d.candidate_state,"
        "d.decision_score,d.consensus_count,d.lead_strategy,d.is_selected,d.selection_reason,"
        "d.suggested_shares,d.position_value,d.snapshot,d.created_at "
        f"FROM decision_signal_log d WHERE {where} "
        "ORDER BY d.observed_date DESC,d.is_selected DESC NULLS LAST,d.decision_score DESC NULLS LAST,"
        "d.score DESC,d.stock_id,d.strategy LIMIT %(limit)s", params)

    ids = sorted({row["stock_id"] for row in rows})
    latest_by_stock = {}
    stock_meta = {}
    bars_by_stock = {}
    if ids:
        for row in db.query(
                "SELECT DISTINCT ON (stock_id) stock_id,trade_date,adj_close AS close "
                "FROM price_daily WHERE stock_id=ANY(%(ids)s) "
                "ORDER BY stock_id,trade_date DESC", {"ids": ids}):
            latest_by_stock[row["stock_id"]] = row
        for row in db.query(
                "SELECT stock_id,name,industry FROM mv_stock_snapshot "
                "WHERE stock_id=ANY(%(ids)s)", {"ids": ids}):
            stock_meta[row["stock_id"]] = row
        if rows:
            since = min(row["observed_date"] for row in rows)
            for bar in db.query(
                    "SELECT stock_id,trade_date,adj_close AS close FROM price_daily "
                    "WHERE stock_id=ANY(%(ids)s) AND trade_date>%(since)s "
                    "ORDER BY stock_id,trade_date", {"ids": ids, "since": since}):
                bars_by_stock.setdefault(bar["stock_id"], []).append(bar)

    items = []
    for raw in rows:
        row = dict(raw)
        snap = row.pop("snapshot", None) or {}
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except json.JSONDecodeError:
                snap = {}
        meta = stock_meta.get(row["stock_id"], {})
        strategy_snap = next(
            (x for x in snap.get("strategies", []) if x.get("key") == row["strategy"]), {})
        latest_row = latest_by_stock.get(row["stock_id"], {})
        latest_close = _f(latest_row.get("close"))
        entry, stop = _f(row.get("entry")), _f(row.get("stop"))
        unit = entry - stop if entry is not None and stop is not None else None
        current_r = None
        if latest_close is not None and entry and unit and unit > 0:
            net_return = latest_close / entry - 1 - _f(row.get("cost_pct"), ROUND_TRIP_COST)
            current_r = round(net_return / (unit / entry), 2)
        progress_days = sum(
            1 for bar in bars_by_stock.get(row["stock_id"], [])
            if bar["trade_date"] > row["observed_date"])
        items.append({
            **{key: _iso(value) for key, value in row.items()},
            "name": snap.get("name") or meta.get("name") or row["stock_id"],
            "industry": snap.get("industry") or meta.get("industry") or "其他",
            "pattern_name": strategy_snap.get("pattern_name") or row.get("pattern"),
            "candidate_state": row.get("candidate_state") or snap.get("state"),
            "decision_score": _f(row.get("decision_score"), _f(snap.get("decision_score"))),
            "consensus_count": row.get("consensus_count") or snap.get("consensus_count"),
            "is_selected": row.get("is_selected") if row.get("is_selected") is not None else snap.get("selected"),
            "selection_reason": row.get("selection_reason") or snap.get("selection_reason"),
            "suggested_shares": _f(row.get("suggested_shares"), _f((snap.get("position_plan") or {}).get("suggested_shares"))),
            "position_value": _f(row.get("position_value"), _f((snap.get("position_plan") or {}).get("position_value"))),
            "latest_date": _iso(latest_row.get("trade_date")),
            "latest_close": latest_close,
            "current_r": current_r,
            "progress_days": min(progress_days, int(row.get("horizon") or HORIZON)),
        })

    dates = db.query(
        "SELECT observed_date,count(*)::int AS signals,count(DISTINCT stock_id)::int AS stocks "
        "FROM decision_signal_log WHERE model_version=%(version)s "
        "GROUP BY observed_date ORDER BY observed_date DESC LIMIT 120",
        {"version": version})
    return {
        "as_of": _iso(latest),
        "count": int(summary.get("total") or 0),
        "summary": {
            "total": int(summary.get("total") or 0),
            "pending": int(summary.get("pending") or 0),
            "win": int(summary.get("win") or 0),
            "loss": int(summary.get("loss") or 0),
            "timeout": int(summary.get("timeout") or 0),
            "selected": int(summary.get("selected") or 0),
            "avg_r": round(_f(summary.get("avg_r"), 0), 2) if summary.get("avg_r") is not None else None,
        },
        "dates": [{**dict(row), "observed_date": _iso(row["observed_date"])} for row in dates],
        "items": items,
        "model_version": version,
        "mode": mode,
    }


def calibration_rows(mode=DEFAULT_MODE):
    ensure_tables()
    version = MODES[normalize_mode(mode)]
    rows = db.query(
        "SELECT strategy,score_bucket,count(*)::int AS n,"
        " sum((outcome_status='win')::int)::int AS target_hits,"
        " sum((outcome_r>0)::int)::int AS positive_n,"
        " avg(outcome_r) AS avg_r,avg(outcome_days) AS avg_days,"
        " min(observed_date) AS first_date,max(observed_date) AS last_date "
        "FROM decision_signal_log WHERE model_version=%(v)s AND outcome_status<>'pending' "
        "GROUP BY strategy,score_bucket ORDER BY strategy,score_bucket",
        {"v": version})
    out = []
    for row in rows:
        n, hits, positive = int(row["n"]), int(row["target_hits"]), int(row["positive_n"])
        lo, hi = _wilson(hits, n)
        out.append({
            "strategy": row["strategy"], "score_bucket": row["score_bucket"], "n": n,
            "target_hit_rate": round(hits / n * 100, 1),
            "target_hit_ci_low": lo, "target_hit_ci_high": hi,
            "positive_rate": round(positive / n * 100, 1),
            "avg_r": round(float(row["avg_r"]), 2),
            "avg_days": round(float(row["avg_days"]), 1),
            "confidence": "high" if n >= 100 else "medium" if n >= 30 else "low",
            "first_date": _iso(row["first_date"]), "last_date": _iso(row["last_date"]),
            "model_version": version,
        })
    return out


def current_holdings():
    exists = db.query("SELECT to_regclass('public.trade_log') AS t")[0]["t"]
    if not exists:
        return {"items": [], "stock_ids": set(), "industry_counts": {}}
    transactions = db.query(
        "SELECT id,stock_id,action,trade_date,shares,price,fee,tax FROM trade_log ORDER BY trade_date,id")
    by = {}
    for txn in transactions:
        by.setdefault(txn["stock_id"], []).append(txn)
    open_positions = {sid: ledger.build(txns) for sid, txns in by.items()}
    open_positions = {sid: pos for sid, pos in open_positions.items() if pos["net_shares"] > 1e-9}
    if not open_positions:
        return {"items": [], "stock_ids": set(), "industry_counts": {}}
    snaps = {r["stock_id"]: r for r in db.query(
        "SELECT stock_id,name,industry,close FROM mv_stock_snapshot WHERE stock_id=ANY(%(ids)s)",
        {"ids": list(open_positions)})}
    items, counts = [], {}
    for sid, pos in open_positions.items():
        snap = snaps.get(sid, {})
        industry = snap.get("industry") or "其他"
        counts[industry] = counts.get(industry, 0) + 1
        close = _f(snap.get("close"))
        items.append({
            "stock_id": sid, "name": snap.get("name") or sid, "industry": industry,
            "shares": pos["net_shares"], "close": close,
            "market_value": close * pos["net_shares"] if close is not None else None,
        })
    return {"items": items, "stock_ids": set(open_positions), "industry_counts": counts}


def _position_plan(entry, stop, target, capital, risk_per_trade_pct, max_position_pct,
                   lot_size=1000, available_capital=None, exit_rule=None):
    """以進場到停損的價差反推部位。target=None 表示不設目標（動能模式以到期出場）。"""
    entry, stop, target = _f(entry), _f(stop), _f(target)
    if not entry or not stop or not (0 < stop < entry) or (target is not None and target <= entry):
        return {"valid": False, "reason": "進場、停損或目標價無法形成有效多方計畫"}
    unit_risk = entry - stop
    risk_budget = capital * risk_per_trade_pct / 100
    cap_limit = min(capital * max_position_pct / 100,
                    available_capital if available_capital is not None else capital)
    by_risk = math.floor(risk_budget / unit_risk)
    by_capital = math.floor(cap_limit / entry)
    odd_lot = max(0, min(by_risk, by_capital))
    size = max(1, int(lot_size))
    suggested = (odd_lot // size) * size
    if suggested <= 0 and odd_lot > 0 and size > 1:
        suggested = odd_lot
        mode = "零股"
    else:
        mode = "整張" if size >= 1000 else "零股"
    value = suggested * entry
    risk_amount = suggested * unit_risk
    return {
        "valid": suggested > 0, "reason": None if suggested > 0 else "資金或單筆風險額度不足",
        "entry": round(entry, 2), "stop": round(stop, 2),
        "target": round(target, 2) if target is not None else None,
        "rr": round((target - entry) / unit_risk, 2) if target is not None else None,
        "exit_rule": exit_rule,
        "unit_risk": round(unit_risk, 2), "risk_budget": round(risk_budget),
        "suggested_shares": suggested, "board_lot_shares": (odd_lot // 1000) * 1000,
        "odd_lot_capacity": odd_lot, "order_mode": mode,
        "position_value": round(value), "capital_pct": round(value / capital * 100, 1),
        "risk_amount": round(risk_amount), "actual_risk_pct": round(risk_amount / capital * 100, 2),
    }


METHODS = {
    "momentum": (f"動能模式：可執行訊號＋趨勢模板成立＋加權指數站上 {MARKET_MA_DAYS} 日線才開新倉，依 RS 評等排序；"
                 f"停損進場價下 {int(MOMENTUM_STOP_PCT * 100)}%、不設目標、第 {HORIZON} 個交易日收盤出場"),
    "classic": "原始規則：多策略共識 + 可執行狀態 + 現有持股產業上限 + 固定風險部位；分數校準滿 30 筆才影響名次",
}


def build_decision_response(scan, calibrations, holdings, capital=1_000_000,
                            risk_per_trade_pct=0.75, max_new_positions=3,
                            max_industry_positions=2, max_position_pct=25,
                            lot_size=1000, limit=200, mode=DEFAULT_MODE, market=None):
    """套用模式規則、持股產業上限與資金限制，挑出本次新倉。

    momentum：ready 且趨勢模板成立者才可入選（裸 K 因此只在動能股上當進場時機），加權指數跌破 60 日線時
    不開新倉，依 RS → 決策分 → 成交額排序；出場為進場價下 8% 停損、不設目標、20 日到期。
    classic：依共識 → 決策分（含校準）→ 成交額排序，出場用領頭策略自己的停損／目標。
    market 由呼叫端傳入（market_regime(as_of)），回測時可帶入當日的大盤狀態。
    """
    mode = normalize_mode(mode)
    momentum = mode == "momentum"
    items = deepcopy(scan.get("items", []))
    cmap = {(r["strategy"], r["score_bucket"]): r for r in calibrations}
    for item in items:
        best_live = None
        for strategy in item["strategies"]:
            live = cmap.get((strategy["key"], score_bucket(strategy["score"])))
            strategy["score_calibration"] = live
            if live and (best_live is None or live["n"] > best_live["n"]):
                best_live = live
        # 原始規則：至少 30 筆才讓校準結果小幅影響排序。動能模式以 RS 排序，決策分只是同 RS 時的次序。
        adjustment = (max(-5, min(5, best_live["avg_r"] * 4))
                      if not momentum and best_live and best_live["n"] >= 30 else 0)
        item["calibration_adjustment"] = round(adjustment, 1)
        item["decision_score"] = round(min(100, item["base_score"] + item["consensus_bonus"] + adjustment), 1)
        item["calibration"] = best_live
        prior = next((x.get("pattern_prior") for x in item["strategies"] if x.get("pattern_prior")), None)
        item["historical_reference"] = best_live or prior
        item["momentum_ok"] = bool(item.get("trend_template"))

    if momentum:
        items.sort(key=lambda x: (x["state"] == "ready" and x["momentum_ok"], x["state"] == "ready",
                                  x.get("rs_rating") or 0, x["decision_score"], x.get("amt20") or 0),
                   reverse=True)
    else:
        items.sort(key=lambda x: (x["state"] == "ready", x["consensus_count"],
                                  x["decision_score"], x.get("amt20") or 0), reverse=True)
    market_blocked = momentum and bool(market) and market.get("above") is False
    exit_rule = f"第 {HORIZON} 個交易日收盤出場" if momentum else None
    held_ids = set(holdings.get("stock_ids", set()))
    industry_counts = dict(holdings.get("industry_counts", {}))
    selected = 0
    remaining = max(0, float(capital))
    for item in items:
        lead = next(x for x in item["strategies"] if x["key"] == item["lead_strategy"])
        entry = _f(item.get("close")) or _f(lead.get("entry"))
        if momentum:
            stop = entry * (1 - MOMENTUM_STOP_PCT) if entry else None
            target = None
        else:
            stop, target = lead.get("stop"), lead.get("target")
        item["position_plan"] = _position_plan(
            entry, stop, target, max(1, float(capital)), max(0.01, float(risk_per_trade_pct)),
            max(1, min(100, float(max_position_pct))), lot_size, remaining, exit_rule)
        item["held"] = item["stock_id"] in held_ids
        item["selected"] = False
        if item["held"]:
            item["selection_reason"] = "目前已持有，列為持股管理而非新倉"
        elif item["state"] != "ready":
            item["selection_reason"] = "訊號仍在等待／觀察，尚未列入可執行名單"
        elif momentum and not item["momentum_ok"]:
            item["selection_reason"] = "趨勢模板未成立：動能模式只在多頭排列、RS ≥ 70 的股票上找進場點"
        elif market_blocked:
            item["selection_reason"] = f"加權指數在 {MARKET_MA_DAYS} 日線下，動能模式暫停開新倉"
        elif industry_counts.get(item["industry"], 0) >= max(1, int(max_industry_positions)):
            item["selection_reason"] = f"{item['industry']}持股已達產業上限"
        elif selected >= max(1, int(max_new_positions)):
            item["selection_reason"] = "已達本次新增部位上限"
        elif not item["position_plan"].get("valid"):
            item["selection_reason"] = item["position_plan"].get("reason")
        else:
            item["selected"] = True
            item["selection_reason"] = "通過訊號、產業與部位風險限制"
            selected += 1
            industry_counts[item["industry"]] = industry_counts.get(item["industry"], 0) + 1
            remaining = max(0, remaining - item["position_plan"]["position_value"])
    items = items[:max(1, min(int(limit), 500))]
    summary = {
        "selected": sum(1 for x in items if x["selected"]),
        "ready": sum(1 for x in items if x["state"] == "ready"),
        "ready_momentum": sum(1 for x in items if x["state"] == "ready" and x["momentum_ok"]),
        "waiting": sum(1 for x in items if x["state"] == "waiting"),
        "watch": sum(1 for x in items if x["state"] == "watch"),
        "consensus": sum(1 for x in items if x["consensus_count"] > 1),
        "held": len(holdings.get("items", [])),
        "remaining_capital": round(remaining),
    }
    return {
        "as_of": scan.get("as_of"), "scanned": scan.get("scanned", 0),
        "count": len(items), "summary": summary, "items": items,
        "mode": mode, "market": market, "market_blocked": market_blocked,
        "holdings": {"items": holdings.get("items", []),
                     "industry_counts": holdings.get("industry_counts", {})},
        "calibration": calibrations,
        "settings": {
            "capital": float(capital), "risk_per_trade_pct": float(risk_per_trade_pct),
            "max_new_positions": int(max_new_positions),
            "max_industry_positions": int(max_industry_positions),
            "max_position_pct": float(max_position_pct), "lot_size": int(lot_size),
            "model_version": MODES[mode], "horizon": HORIZON,
            "round_trip_cost_pct": ROUND_TRIP_COST * 100,
            "stop_pct": MOMENTUM_STOP_PCT * 100 if momentum else None,
            "market_ma_days": MARKET_MA_DAYS if momentum else None,
        },
        "method": METHODS[mode],
    }
