"""K 線手繪圖形 API：使用者自己在個股 K 線上畫的趨勢線／水平線／通道／費波。

一筆 = 圖上一個 klinecharts overlay：
  tool   overlay 名稱（rayLine=趨勢線、segment=線段、horizontalStraightLine=水平線、
         priceChannelLine=平行通道、fibonacciLine=費波南希…）
  points [{timestamp, value}, ...]，時間用毫秒（前端 klinecharts 原生格式）

同一檔股票在「日/週/月」與「還原/未還原」下價位與時間軸都不同，
故以 (stock_id, period, adj) 分組存取，切換週期只會看到該組畫過的線。
"""
import json
from datetime import datetime, timezone

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


TOOL_NAME = {"rayLine": "趨勢線", "segment": "線段", "straightLine": "直線",
             "horizontalStraightLine": "水平線", "horizontalRayLine": "水平射線",
             "horizontalSegment": "水平線段", "priceChannelLine": "平行通道",
             "parallelStraightLine": "平行線", "fibonacciLine": "費波南希", "priceLine": "價格線"}
FLAT_TOOLS = {"horizontalStraightLine", "horizontalRayLine", "horizontalSegment", "priceLine"}


def _ms(d):
    """交易日 → 毫秒 timestamp（與前端 klinecharts 存的 points 同基準：UTC 當日零時）。"""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _bars(stock_id, period, adj, n=2):
    """該股在此週期／還原設定下最近 n 根 K 棒的 (日期, 收盤)，由舊到新。"""
    col = "adj_close" if adj else "close"
    if period == "D":
        rows = db.query(
            f"SELECT * FROM (SELECT trade_date, {col} AS close FROM price_daily WHERE stock_id=%(id)s "
            "ORDER BY trade_date DESC LIMIT %(n)s) z ORDER BY trade_date", {"id": stock_id, "n": n})
    else:
        unit = {"W": "week", "M": "month"}.get(period, "week")
        rows = db.query(
            "SELECT * FROM (SELECT date_trunc(%(u)s, trade_date)::date AS trade_date, "
            f"  (array_agg({col} ORDER BY trade_date DESC))[1] AS close "
            "  FROM price_daily WHERE stock_id=%(id)s GROUP BY 1 ORDER BY 1 DESC LIMIT %(n)s) z "
            "ORDER BY trade_date", {"id": stock_id, "u": unit, "n": n})
    return [(r["trade_date"], float(r["close"])) for r in rows if r["close"] is not None]


def _line_at(pts, tool, ts):
    """線在時間 ts 的價位；工具的有效範圍外回 None（線段畫完就結束、射線不往回延伸）。"""
    ps = [(p.get("timestamp"), p.get("value")) for p in (pts or [])
          if p.get("timestamp") is not None and p.get("value") is not None]
    if not ps:
        return None
    if tool in FLAT_TOOLS:
        return float(ps[0][1])
    if len(ps) < 2:
        return None
    (t0, v0), (t1, v1) = ps[0], ps[1]
    if t1 == t0:
        return None
    if tool == "segment" and not (min(t0, t1) <= ts <= max(t0, t1)):
        return None                                   # 線段只在兩點之間有效
    if tool == "rayLine" and ts < min(t0, t1):
        return None                                   # 射線不往起點左邊延伸
    return float(v0) + (float(v1) - float(v0)) / (t1 - t0) * (ts - t0)


def _values_at(d, ts):
    """回傳 [(子線名, 價位)]；平行通道有主軌+平行軌，其餘一條。"""
    tool, pts = d["tool"], d.get("points") or []
    base = _line_at(pts, tool, ts)
    if base is None:
        return []
    if tool in ("priceChannelLine", "parallelStraightLine") and len(pts) >= 3:
        anchor = _line_at(pts, tool, pts[2].get("timestamp"))
        if anchor is not None and pts[2].get("value") is not None:
            return [("主軌", base), ("平行軌", base + float(pts[2]["value"]) - anchor)]
    return [("", base)]


@router.get("/alerts")
def alerts(band: float = 0.02):
    """手繪線警報：把每條線延伸到最新一根 K 棒，比對收盤價。

    穿越判定用「前一根 vs 這一根」：由上而下穿過＝跌破、由下而上穿過＝站上；
    沒穿越但距線在 band 內（預設 2%）回報「接近」。
    上升線（斜率為正）是支撐、下降線是壓力，所以同樣是跌破，意義不同。
    費波南希多層次、雜訊高，不納入警報。
    """
    _ensure()
    rows = db.query("SELECT id, stock_id, period, adj, tool, points, note FROM chart_drawing "
                    "WHERE tool <> 'fibonacciLine' ORDER BY stock_id")
    if not rows:
        return {"count": 0, "as_of": None, "items": []}
    names = {r["stock_id"]: r["name"] for r in
             db.query("SELECT stock_id, name FROM stock WHERE stock_id = ANY(%(ids)s)",
                      {"ids": sorted({r["stock_id"] for r in rows})})}
    cache, out, as_of = {}, [], None
    for d in rows:
        key = (d["stock_id"], d["period"], d["adj"])
        if key not in cache:
            cache[key] = _bars(*key)
        bars = cache[key]
        if len(bars) < 2:
            continue
        (pd_, pc), (cd, cc) = bars[-2], bars[-1]
        if d["period"] == "D" and (as_of is None or cd.isoformat() > as_of):
            as_of = cd.isoformat()
        cur = _values_at(d, _ms(cd))
        prev = dict(_values_at(d, _ms(pd_)))
        for label, line in cur:
            pline = prev.get(label)
            sig = None
            if pline is not None:
                if pc >= pline and cc < line:
                    sig = "break_down"
                elif pc <= pline and cc > line:
                    sig = "break_up"
            gap = cc / line - 1 if line else None
            if sig is None and gap is not None and abs(gap) <= band:
                sig = "near"
            if not sig:
                continue
            slope = (line - pline) if pline is not None else 0
            kind = ("水平線" if d["tool"] in FLAT_TOOLS else
                    "上升趨勢線" if slope > 0 else "下降趨勢線" if slope < 0 else "水平趨勢線")
            out.append({
                "id": d["id"], "stock_id": d["stock_id"], "name": names.get(d["stock_id"], d["stock_id"]),
                "period": d["period"], "adj": d["adj"], "tool": d["tool"],
                "tool_name": TOOL_NAME.get(d["tool"], d["tool"]) + (f"·{label}" if label else ""),
                "kind": kind, "note": d.get("note"),
                "date": cd.isoformat(), "close": round(cc, 2), "line": round(line, 2),
                "gap": round(gap, 4) if gap is not None else None,
                "signal": sig,
                "text": _alert_text(sig, kind, gap),
            })
    order = {"break_down": 0, "break_up": 1, "near": 2}
    out.sort(key=lambda x: (order[x["signal"]], abs(x["gap"] or 0)))
    return {"count": len(out), "as_of": as_of, "items": out}


def _alert_text(sig, kind, gap):
    if sig == "break_down":
        return f"跌破{kind}" + ("（支撐失守）" if kind == "上升趨勢線" else "")
    if sig == "break_up":
        return f"站上{kind}" + ("（壓力突破）" if kind == "下降趨勢線" else "")
    side = "上方" if (gap or 0) >= 0 else "下方"
    return f"逼近{kind}（在線{side} {abs(gap or 0) * 100:.1f}%）"


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
