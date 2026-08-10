"""K 線手繪圖形 API：使用者自己在個股 K 線上畫的趨勢線／水平線／通道／費波。

一筆 = 圖上一個 klinecharts overlay：
  tool   overlay 名稱（rayLine=趨勢線、segment=線段、horizontalStraightLine=水平線、
         priceChannelLine=平行通道、fibonacciLine=費波南希…）
  points [{timestamp, value}, ...]，時間用毫秒（前端 klinecharts 原生格式）

同一檔股票在「日/週/月」與「還原/未還原」下價位與時間軸都不同，
故以 (stock_id, period, adj) 分組存取，切換週期只會看到該組畫過的線。
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(prefix="/api/drawings", tags=["drawings"])

# 允許的畫線工具（klinecharts 內建 overlay 名稱），擋掉亂塞的字串
TOOLS = {
    "rayLine", "segment", "straightLine", "horizontalStraightLine", "horizontalRayLine",
    "horizontalSegment", "verticalStraightLine", "priceChannelLine", "parallelStraightLine",
    "fibonacciLine", "priceLine", "rect", "circle", "simpleAnnotation",
}
PERIODS = {"D", "W", "M"}

_ensured = False


def _ensure():
    global _ensured
    if _ensured:
        return
    db.execute(
        "CREATE TABLE IF NOT EXISTS chart_drawing ("
        " id SERIAL PRIMARY KEY,"
        " stock_id VARCHAR(16) NOT NULL,"
        " period VARCHAR(2) NOT NULL DEFAULT 'D',"    # D/W/M：不同週期各自一組
        " adj BOOLEAN NOT NULL DEFAULT true,"         # 還原價與否，價位不同故分開存
        " tool VARCHAR(40) NOT NULL,"                 # overlay 名稱
        " points JSONB NOT NULL,"                     # [{timestamp, value}, ...]
        " styles JSONB,"                              # 顏色/線寬（null=用預設）
        " note VARCHAR(200),"
        " created_at TIMESTAMP DEFAULT now(),"
        " updated_at TIMESTAMP DEFAULT now())")
    db.execute("CREATE INDEX IF NOT EXISTS idx_chart_drawing_key "
               "ON chart_drawing(stock_id, period, adj)")
    _ensured = True


class DrawingIn(BaseModel):
    stock_id: str
    period: str = "D"
    adj: bool = True
    tool: str
    points: list[dict]
    styles: dict | None = None
    note: str | None = None


class DrawingPatch(BaseModel):
    points: list[dict] | None = None
    styles: dict | None = None
    note: str | None = None


def _clean_points(points):
    """只留 timestamp/value 兩個數值欄（dataIndex 依資料而變，不存）。"""
    out = []
    for p in points or []:
        ts, v = p.get("timestamp"), p.get("value")
        if ts is None or v is None:
            continue
        out.append({"timestamp": int(ts), "value": float(v)})
    if not out:
        raise HTTPException(400, "points 至少要有一個含 timestamp/value 的點")
    return out


@router.get("")
def list_drawings(stock_id: str, period: str = "D", adj: bool = True):
    """取某檔股票在該週期／還原設定下畫過的所有線。"""
    _ensure()
    rows = db.query(
        "SELECT id, tool, points, styles, note FROM chart_drawing "
        "WHERE stock_id = %(s)s AND period = %(p)s AND adj = %(a)s ORDER BY id",
        {"s": stock_id, "p": period if period in PERIODS else "D", "a": adj})
    return {"count": len(rows), "items": rows}


@router.post("")
def add_drawing(d: DrawingIn):
    _ensure()
    sid = (d.stock_id or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 stock_id")
    if d.tool not in TOOLS:
        raise HTTPException(400, f"不支援的畫線工具：{d.tool}")
    rows = db.execute(
        "INSERT INTO chart_drawing (stock_id, period, adj, tool, points, styles, note) "
        "VALUES (%(s)s, %(p)s, %(a)s, %(t)s, %(pt)s::jsonb, %(st)s::jsonb, %(n)s) RETURNING id",
        {"s": sid, "p": d.period if d.period in PERIODS else "D", "a": d.adj, "t": d.tool,
         "pt": json.dumps(_clean_points(d.points)),
         "st": json.dumps(d.styles) if d.styles else None, "n": d.note}, returning=True)
    return {"ok": True, "id": rows[0]["id"]}


@router.put("/{did}")
def update_drawing(did: int, d: DrawingPatch):
    """拖動端點後回存新座標（也可只改樣式/備註）。"""
    _ensure()
    sets, params = [], {"id": did}
    if d.points is not None:
        sets.append("points = %(pt)s::jsonb")
        params["pt"] = json.dumps(_clean_points(d.points))
    if d.styles is not None:
        sets.append("styles = %(st)s::jsonb")
        params["st"] = json.dumps(d.styles)
    if d.note is not None:
        sets.append("note = %(n)s")
        params["n"] = d.note
    if not sets:
        return {"ok": True, "updated": 0}
    sets.append("updated_at = now()")
    n = db.execute(f"UPDATE chart_drawing SET {', '.join(sets)} WHERE id = %(id)s", params)
    if not n:
        raise HTTPException(404, "查無此手繪")
    return {"ok": True, "updated": n}


@router.delete("/{did}")
def del_drawing(did: int):
    _ensure()
    return {"ok": True, "deleted": db.execute("DELETE FROM chart_drawing WHERE id = %(id)s", {"id": did})}


@router.delete("")
def clear_drawings(stock_id: str, period: str = "D", adj: bool = True):
    """清空該股在此週期／還原設定下的所有手繪。"""
    _ensure()
    n = db.execute(
        "DELETE FROM chart_drawing WHERE stock_id = %(s)s AND period = %(p)s AND adj = %(a)s",
        {"s": stock_id, "p": period if period in PERIODS else "D", "a": adj})
    return {"ok": True, "deleted": n}
