"""自選股 API：使用者自建分類 + 各分類成員。

分類（watchlist_category）彼此獨立；成員（watchlist_item）記錄加入當下的型態快照
（snapshot：breakout/pattern），檢視時再抓即時的 mv_stock_snapshot（股價/RS/近3月/產業）
合併，讓每個分類的表格與「型態突破」搜尋結果同構。
另有裸 K 決策、突破決策兩個檢視（/{cid}/price-action、/{cid}/breakout），規則同兩個決策頁的「指定個股分析」。
"""
import bisect
import json
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, target_track
from ..growth import fundamental_trends
from .patterns import screen_price_action
from .screen import _attach_last_pattern, _attach_recent_eps, breakout_ranking


def _bt_map():
    """載入 pattern_backtest → {pattern: {horizon: {avg, wr}}} + 持有期清單。表不存在回 ({}, [])。
    avg 轉成小數（表內存百分比），供自選股「vs 型態歷史」對照。"""
    if not db.query("SELECT to_regclass('public.pattern_backtest') AS t")[0]["t"]:
        return {}, []
    m, hs = {}, set()
    for r in db.query("SELECT pattern, horizon, avg_ret, win_rate FROM pattern_backtest"):
        hs.add(r["horizon"])
        m.setdefault(r["pattern"], {})[r["horizon"]] = {
            "avg": (float(r["avg_ret"]) / 100) if r["avg_ret"] is not None else None,
            "wr": float(r["win_rate"]) if r["win_rate"] is not None else None,
        }
    return m, sorted(hs)


def _pick_horizon(horizons, elapsed):
    """依突破後已經過的交易日，挑對照用的回測持有期：取 ≥elapsed 的最小者；超過最大則用最大並標記。"""
    for h in horizons:
        if h >= elapsed:
            return h, False
    return horizons[-1], True                      # 已過觀察期，仍以最長持有期當參考

def _attach_target_track(items):
    """為有突破快照的列附上「量測滿足價」達成追蹤 row["target_track"]。

    一次撈齊所有股票自最早突破日起的還原價日線，再依各自的突破日切片——
    逐檔發 query 會有幾百次來回。
    """
    todo = [(r, r["breakout"]) for r in items
            if r.get("breakout") and r["breakout"].get("target") and r["breakout"].get("breakout_date")]
    if not todo:
        return
    ids = sorted({r["stock_id"] for r, _ in todo})
    since = min(bk["breakout_date"][:10] for _, bk in todo)
    rows = db.query(
        "SELECT stock_id, trade_date, adj_high AS high, adj_low AS low, adj_close AS close "
        "FROM price_daily WHERE stock_id = ANY(%(ids)s) AND trade_date >= %(d)s::date "
        "  AND adj_close IS NOT NULL ORDER BY stock_id, trade_date",
        {"ids": ids, "d": since})
    by, dates = {}, {}
    for b in rows:
        by.setdefault(b["stock_id"], []).append(b)
    for sid, bs in by.items():
        dates[sid] = [b["trade_date"] for b in bs]
    for r, bk in todo:
        bs = by.get(r["stock_id"])
        if not bs:
            continue
        i = bisect.bisect_left(dates[r["stock_id"]], date.fromisoformat(bk["breakout_date"][:10]))
        t = target_track.track(bk, bs[i:])
        if t:
            if t.get("hit_date"):                     # date → 字串，給 JSON
                t["hit_date"] = t["hit_date"].isoformat()
            r["target_track"] = t


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

_ensured = False


def _ensure():
    global _ensured
    if _ensured:
        return
    db.execute(
        "CREATE TABLE IF NOT EXISTS watchlist_category ("
        " id SERIAL PRIMARY KEY,"
        " name VARCHAR(60) NOT NULL UNIQUE,"
        " sort INT DEFAULT 0,"
        " created_at TIMESTAMP DEFAULT now())")
    db.execute(
        "CREATE TABLE IF NOT EXISTS watchlist_item ("
        " id SERIAL PRIMARY KEY,"
        " category_id INT NOT NULL REFERENCES watchlist_category(id) ON DELETE CASCADE,"
        " stock_id VARCHAR(16) NOT NULL,"
        " snapshot JSONB,"                          # 加入當下的型態 {breakout, pattern, pattern_name}
        " note VARCHAR(200),"
        " entry_price NUMERIC,"                      # 加入當下收盤（進場價，用來追蹤持有報酬）
        " entry_date DATE,"                          # 加入當下的資料日
        " created_at TIMESTAMP DEFAULT now(),"
        " UNIQUE(category_id, stock_id))")
    db.execute("ALTER TABLE watchlist_item ADD COLUMN IF NOT EXISTS entry_price NUMERIC")   # 舊表補欄
    db.execute("ALTER TABLE watchlist_item ADD COLUMN IF NOT EXISTS entry_date DATE")
    db.execute("CREATE INDEX IF NOT EXISTS idx_wl_item_cat ON watchlist_item(category_id)")
    _ensured = True


def _entry_of(ids):
    """取一批股票『目前收盤 + 資料日』作為進場價（優先 mv，缺則退回 price_daily 最新）。"""
    out = {}
    for r in db.query("SELECT stock_id, close, as_of_date FROM mv_stock_snapshot "
                      "WHERE stock_id = ANY(%(ids)s)", {"ids": ids}):
        if r.get("close") is not None:
            out[r["stock_id"]] = (r["close"], r.get("as_of_date"))
    missing = [i for i in ids if i not in out]
    for i in missing:
        r = db.query("SELECT adj_close AS close, trade_date FROM price_daily "
                     "WHERE stock_id = %(s)s ORDER BY trade_date DESC LIMIT 1", {"s": i})
        if r:
            out[i] = (r[0]["close"], r[0]["trade_date"])
    return out


class CategoryIn(BaseModel):
    name: str


class ItemIn(BaseModel):
    category_id: int
    stock_id: str
    snapshot: dict | None = None      # {breakout, pattern, pattern_name}；手動/選股加入可為 None
    note: str | None = None


class BulkRow(BaseModel):
    stock_id: str
    snapshot: dict | None = None


class ItemBulkIn(BaseModel):
    category_id: int
    items: list[BulkRow]


# ---- 分類 ----
@router.get("/categories")
def list_categories():
    _ensure()
    return db.query(
        "SELECT c.id, c.name, c.sort, "
        " (SELECT count(*) FROM watchlist_item i WHERE i.category_id = c.id) AS n "
        "FROM watchlist_category c ORDER BY c.sort, c.id")


@router.post("/categories")
def add_category(c: CategoryIn):
    _ensure()
    name = (c.name or "").strip()
    if not name:
        raise HTTPException(400, "分類名稱不可空白")
    if len(name) > 60:
        raise HTTPException(400, "分類名稱過長（上限 60 字）")
    if db.query("SELECT 1 FROM watchlist_category WHERE name = %(n)s", {"n": name}):
        raise HTTPException(409, f"分類已存在：{name}")
    rows = db.execute("INSERT INTO watchlist_category (name) VALUES (%(n)s) RETURNING id, name",
                      {"n": name}, returning=True)
    return {"ok": True, **rows[0]}


@router.put("/categories/{cid}")
def rename_category(cid: int, c: CategoryIn):
    _ensure()
    name = (c.name or "").strip()
    if not name:
        raise HTTPException(400, "分類名稱不可空白")
    if db.query("SELECT 1 FROM watchlist_category WHERE name = %(n)s AND id <> %(id)s",
                {"n": name, "id": cid}):
        raise HTTPException(409, f"分類已存在：{name}")
    n = db.execute("UPDATE watchlist_category SET name = %(n)s WHERE id = %(id)s",
                   {"n": name, "id": cid})
    if not n:
        raise HTTPException(404, "查無分類")
    return {"ok": True}


@router.delete("/categories/{cid}")
def del_category(cid: int):
    _ensure()
    n = db.execute("DELETE FROM watchlist_category WHERE id = %(id)s", {"id": cid})  # 連帶刪成員（CASCADE）
    return {"ok": True, "deleted": n}


# ---- 成員 ----
@router.get("/{cid}/items")
def category_items(cid: int):
    """分類內成員：即時 mv 快照 + 加入當下的型態，合併成與型態突破同構的列。"""
    _ensure()
    wls = db.query(
        "SELECT id, stock_id, snapshot, note, entry_price, entry_date, created_at FROM watchlist_item "
        "WHERE category_id = %(c)s ORDER BY created_at DESC, id DESC", {"c": cid})
    if not wls:
        return {"count": 0, "as_of": None, "items": []}

    ids = [w["stock_id"] for w in wls]
    snap = {r["stock_id"]: r for r in
            db.query("SELECT * FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s)", {"ids": ids})}
    bt, horizons = _bt_map()                              # 型態回測期望值（對照用）
    cal = [r["trade_date"] for r in                       # 交易日曆（算突破後幾個交易日）
           db.query("SELECT trade_date FROM market_index WHERE index_id='TWSE' ORDER BY trade_date")]
    missing = [i for i in ids if i not in snap]           # mv 沒有的（下市/非母體）補名稱
    names = {}
    if missing:
        for r in db.query("SELECT stock_id, name, industry FROM stock WHERE stock_id = ANY(%(ids)s)",
                          {"ids": missing}):
            names[r["stock_id"]] = r

    items, as_of = [], None
    for w in wls:
        sid = w["stock_id"]
        s = snap.get(sid)
        if s:
            row = dict(s)
            if s.get("as_of_date"):
                as_of = s["as_of_date"].isoformat()
        else:
            nm = names.get(sid, {})
            row = {"stock_id": sid, "name": nm.get("name") or sid, "industry": nm.get("industry"),
                   "security_type": None, "rs_rating": None, "close": None, "ret_3m": None}
        snp = w.get("snapshot") or {}
        if snp.get("breakout"):
            bk = dict(snp["breakout"])                       # 複製，不動到原快照
            cl = row.get("close")                            # 即時收盤（來自 mv 最新）
            since = None
            if cl is not None and bk.get("breakout_close"):
                since = round(float(cl) / float(bk["breakout_close"]) - 1, 4)   # 突破後至今漲跌%（即時）
                bk["since_pct"] = since
            row["breakout"] = bk
            # vs 型態歷史：突破後 N 個交易日的實際（順型態方向）報酬 對比 回測同期期望
            pat = snp.get("pattern")
            bd = bk.get("breakout_date")
            if pat and bd and since is not None and horizons and as_of and cal:
                elapsed = (bisect.bisect_right(cal, date.fromisoformat(as_of))
                           - bisect.bisect_right(cal, date.fromisoformat(bd[:10])))
                elapsed = max(elapsed, 0)
                sign = -1 if bk.get("dir") == "bear" else 1  # 空方型態：跌為順勢
                actual_adj = round(sign * since, 4)
                h, over = _pick_horizon(horizons, elapsed)
                ref = (bt.get(pat) or {}).get(h) or {}
                exp = ref.get("avg")
                row["track"] = {
                    "days": elapsed, "horizon": h, "over": over,
                    "actual": actual_adj,
                    "exp_ret": round(exp, 4) if exp is not None else None,
                    "win_rate": ref.get("wr"),
                    "rel": round(actual_adj - exp, 4) if exp is not None else None,
                }
        row["pattern"] = snp.get("pattern")
        row["pattern_name"] = snp.get("pattern_name")
        row["watchlist_id"] = w["id"]
        row["added_at"] = w["created_at"].isoformat() if w["created_at"] else None
        row["note"] = w.get("note")
        ep = w.get("entry_price")                          # 進場價 + 持有至今報酬（即時）
        row["entry_price"] = float(ep) if ep is not None else None
        row["entry_date"] = w["entry_date"].isoformat() if w.get("entry_date") else None
        cl = row.get("close")
        row["hold_pct"] = (round(float(cl) / float(ep) - 1, 4)
                           if (ep and cl is not None) else None)
        items.append(row)
    _attach_target_track(items)                        # 量測滿足價：是否達標/幾天到/還差多少
    _attach_last_pattern(items)
    _attach_recent_eps(items)                          # 近 4 季 EPS（供前端「每季 EPS >」過濾）
    return {"count": len(items), "as_of": as_of, "items": items,
            "target_summary": target_track.summarize([i.get("target_track") for i in items])}


# ---- 決策檢視（裸 K／突破）：同決策頁的「指定個股分析」，不套母體、流動性、證券類別與基本面門檻 ----
def _members(cid):
    """分類成員，順序同清單檢視（新加入的在前）。"""
    return db.query("SELECT id, stock_id FROM watchlist_item WHERE category_id = %(c)s "
                    "ORDER BY created_at DESC, id DESC", {"c": cid})


def _no_signal_rows(ids, reason, trends=False):
    """沒有訊號的成員也列出（前端排最後、灰字）；mv 沒有的（下市／暫停交易）補名稱。"""
    if not ids:
        return []
    snap = {r["stock_id"]: r for r in db.query(
        "SELECT stock_id, name, industry, security_type, close, amt20, as_of_date, eps, gross_margin,"
        " rs_rating, eps_yoy, rev_yoy, per_pctile FROM mv_stock_snapshot WHERE stock_id = ANY(%(ids)s)",
        {"ids": ids})}
    missing = [i for i in ids if i not in snap]
    names = {r["stock_id"]: r for r in db.query(
        "SELECT stock_id, name, industry FROM stock WHERE stock_id = ANY(%(ids)s)", {"ids": missing})} \
        if missing else {}
    ft = fundamental_trends([i for i in ids if i in snap]) if trends else {}
    rows = []
    for sid in ids:
        if sid in snap:
            row = {**dict(snap[sid]), "reason": reason}
            if trends:
                row["fundamental_trend"] = ft.get(sid, {})
        else:
            nm = names.get(sid, {})
            row = {"stock_id": sid, "name": nm.get("name") or sid, "industry": nm.get("industry"),
                   "reason": "沒有最新行情（可能已下市或暫停交易）"}
        row["decision"] = None
        rows.append(row)
    return rows


def _decision_view(mem, res, empty_reason, trends=False):
    """有訊號的照決策頁排序在前，沒有的依清單順序接在後面；每列附 watchlist_id 供移出。"""
    got = {r["stock_id"] for r in res["items"]}
    items = res["items"] + _no_signal_rows([m["stock_id"] for m in mem if m["stock_id"] not in got],
                                           empty_reason, trends)
    wid = {m["stock_id"]: m["id"] for m in mem}
    for r in items:
        r["watchlist_id"] = wid.get(r["stock_id"])
    as_of = res.get("as_of") or next(
        (r["as_of_date"].isoformat() for r in items if r.get("as_of_date")), None)
    return {"count": len(items), "n_signal": len(res["items"]), "as_of": as_of,
            "method": res.get("method"), "items": items}


@router.get("/{cid}/price-action")
def category_price_action(cid: int, lookback: int = 5, expiry: int = 5):
    """分類成員的裸 K 決策；近 lookback 根沒有訊號的也列。"""
    _ensure()
    mem = _members(cid)
    if not mem:
        return {"count": 0, "n_signal": 0, "as_of": None, "items": []}
    res = screen_price_action(stock_ids=",".join(m["stock_id"] for m in mem),
                              lookback=lookback, expiry=expiry, limit=500)
    lb = max(1, min(int(lookback), 10))
    return _decision_view(mem, res, f"近 {lb} 根 K 棒沒有可評估的裸 K 訊號", trends=True)


@router.get("/{cid}/breakout")
def category_breakout(cid: int, recent: int = 20):
    """分類成員的突破決策；近 recent 日沒有已確認多方突破的也列。預設近 1 月（決策頁是近 3 日）。"""
    _ensure()
    mem = _members(cid)
    if not mem:
        return {"count": 0, "n_signal": 0, "as_of": None, "items": []}
    res = breakout_ranking(stock_ids=",".join(m["stock_id"] for m in mem), recent=recent, limit=500)
    rec = max(1, min(int(recent), 25))
    return _decision_view(mem, res, f"近 {rec} 日沒有已確認的多方型態突破", trends=True)


@router.post("/items")
def add_item(it: ItemIn):
    _ensure()
    sid = (it.stock_id or "").strip()
    if not db.query("SELECT 1 FROM watchlist_category WHERE id = %(id)s", {"id": it.category_id}):
        raise HTTPException(404, "查無分類")
    if not db.query("SELECT 1 FROM stock WHERE stock_id = %(id)s", {"id": sid}):
        raise HTTPException(404, f"查無此股：{sid}")
    snp = json.dumps(it.snapshot) if it.snapshot else None
    px, dt = _entry_of([sid]).get(sid, (None, None))     # 進場價＝加入當下收盤
    rows = db.execute(
        "INSERT INTO watchlist_item (category_id, stock_id, snapshot, note, entry_price, entry_date) "
        "VALUES (%(c)s, %(s)s, %(snap)s::jsonb, %(note)s, %(px)s, %(dt)s) "
        "ON CONFLICT (category_id, stock_id) DO UPDATE SET snapshot = EXCLUDED.snapshot "  # 已存在：保留原進場價
        "RETURNING id",
        {"c": it.category_id, "s": sid, "snap": snp, "note": it.note, "px": px, "dt": dt}, returning=True)
    return {"ok": True, "id": rows[0]["id"]}


@router.post("/items/bulk")
def add_items_bulk(payload: ItemBulkIn):
    """批次加入（各搜尋頁「全選加入自選股」）。已存在者更新型態快照，回傳加入/略過數。"""
    _ensure()
    if not db.query("SELECT 1 FROM watchlist_category WHERE id = %(id)s", {"id": payload.category_id}):
        raise HTTPException(404, "查無分類")
    ids = [(r.stock_id or "").strip() for r in payload.items if (r.stock_id or "").strip()]
    if not ids:
        return {"ok": True, "added": 0, "skipped": 0}
    valid = {r["stock_id"] for r in
             db.query("SELECT stock_id FROM stock WHERE stock_id = ANY(%(ids)s)", {"ids": ids})}
    entry = _entry_of([i for i in ids if i in valid]) if valid else {}
    added = 0
    for r in payload.items:
        sid = (r.stock_id or "").strip()
        if sid not in valid:
            continue
        snp = json.dumps(r.snapshot) if r.snapshot else None
        px, dt = entry.get(sid, (None, None))                # 進場價＝加入當下收盤
        db.execute(
            "INSERT INTO watchlist_item (category_id, stock_id, snapshot, entry_price, entry_date) "
            "VALUES (%(c)s, %(s)s, %(snap)s::jsonb, %(px)s, %(dt)s) "
            "ON CONFLICT (category_id, stock_id) "                      # 已存在：保留原進場價
            "DO UPDATE SET snapshot = COALESCE(EXCLUDED.snapshot, watchlist_item.snapshot)",
            {"c": payload.category_id, "s": sid, "snap": snp, "px": px, "dt": dt})
        added += 1
    return {"ok": True, "added": added, "skipped": len(payload.items) - added}


@router.delete("/items/{item_id}")
def del_item(item_id: int):
    _ensure()
    n = db.execute("DELETE FROM watchlist_item WHERE id = %(id)s", {"id": item_id})
    return {"ok": True, "deleted": n}
