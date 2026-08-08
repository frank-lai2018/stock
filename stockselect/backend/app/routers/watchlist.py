"""自選股 API：使用者自建分類 + 各分類成員。

分類（watchlist_category）彼此獨立；成員（watchlist_item）記錄加入當下的型態快照
（snapshot：breakout/pattern），檢視時再抓即時的 mv_stock_snapshot（股價/RS/近3月/產業）
合併，讓每個分類的表格與「型態突破」搜尋結果同構。
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from .screen import _attach_last_pattern

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
            if cl is not None and bk.get("breakout_close"):
                bk["since_pct"] = round(float(cl) / float(bk["breakout_close"]) - 1, 4)   # 突破後至今漲跌%（即時）
            row["breakout"] = bk
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
    _attach_last_pattern(items)
    return {"count": len(items), "as_of": as_of, "items": items}


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
