r"""drill.py — 看圖練習器：隨機抽一段被截斷的 K 線，先判斷、再揭曉，並累積統計。

為什麼要這個：學裸K最大的問題是「事後諸葛」——看歷史圖時後面早就畫出來了，
怎麼看都覺得理所當然。這裡強制的流程是 **先出手 → 才揭曉 → 記錄對錯**，
累積幾百次之後，你會有自己的判斷勝率，而不是「讀完書覺得自己懂了」。

防作弊設計（重要，否則練假的）：
  1. /new 回傳的 bars **不含未來**，未來只在 /reveal 時另外撈。
  2. 不回傳 stock_id / name，**日期也換成合成時間軸**——看到「2330」或
     「2020-03-19」等於直接看到答案。真實身分存在 DB，揭曉時才給。
  3. **必須先 /answer 才能 /reveal**，避免手滑先看答案再說自己猜對了。
  4. 已揭曉的題目不能改答案（answered_at / revealed_at 都會鎖）。

判定：以 T 日收盤為基準，看 horizon 個交易日後的報酬。
  多→漲為對、空→跌為對、中性→波動在 neutral_band 內為對。
"""
import random
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(prefix="/api/drill", tags=["drill"])

BEFORE_DEFAULT = 120        # 題目給幾根歷史（約半年）
HORIZON_DEFAULT = 20        # 揭曉幾根未來（約一個月）
NEUTRAL_BAND = 0.03         # 中性判定：|報酬| < 3% 算盤整
SYNTH_START = 946684800     # 2000-01-01 UTC；合成時間軸起點（純粹給圖表用）
SYNTH_STEP = 86400

_ensured = False


def _ensure():
    global _ensured
    if _ensured:
        return
    db.execute(
        "CREATE TABLE IF NOT EXISTS drill_attempt ("
        " id SERIAL PRIMARY KEY,"
        " stock_id VARCHAR(16) NOT NULL,"      # 不加 stock 外鍵：frank 角色無 REFERENCES 權限
        " as_of DATE NOT NULL,"                # 題目截斷日 T
        " bars_before INT, horizon INT,"
        " created_at TIMESTAMP DEFAULT now(),"
        # ---- 作答 ----
        " answered_at TIMESTAMP,"
        " ans_dir VARCHAR(8),"                 # bull / bear / neutral
        " ans_support NUMERIC, ans_resistance NUMERIC,"
        " ans_entry BOOLEAN, ans_stop NUMERIC,"
        " ans_confidence SMALLINT,"            # 1~5
        " ans_note VARCHAR(300),"
        # ---- 揭曉 ----
        " revealed_at TIMESTAMP,"
        " base_close NUMERIC,"                 # T 日收盤（還原）
        " fwd_ret NUMERIC, fwd_high_pct NUMERIC, fwd_low_pct NUMERIC,"
        " correct BOOLEAN)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_drill_answered ON drill_attempt(answered_at)")
    _ensured = True


def _synth(bars, offset=0):
    """把真實日線換成合成時間軸（隱藏年代），只保留 OHLCV。"""
    out = []
    for i, b in enumerate(bars):
        out.append({
            "timestamp": (SYNTH_START + (offset + i) * SYNTH_STEP) * 1000,   # klinecharts 用毫秒
            "open": float(b["open"]), "high": float(b["high"]),
            "low": float(b["low"]), "close": float(b["close"]),
            "volume": int(b["volume"] or 0),
        })
    return out


def _bars(sid, start, end):
    return db.query(
        "SELECT trade_date, adj_open AS open, adj_high AS high, adj_low AS low,"
        "       adj_close AS close, volume FROM price_daily "
        "WHERE stock_id=%(id)s AND adj_close IS NOT NULL"
        "  AND trade_date > %(s)s::date AND trade_date <= %(e)s::date ORDER BY trade_date",
        {"id": sid, "s": start, "e": end})


@router.post("/new")
def new_drill(bars: int = BEFORE_DEFAULT, horizon: int = HORIZON_DEFAULT):
    """隨機抽一題：回傳截斷到 T 的 K 線（無身分、無真實日期）。"""
    _ensure()
    bars = max(40, min(int(bars), 400))
    horizon = max(1, min(int(horizon), 120))

    for _ in range(12):                       # 少數個股歷史不足，重抽幾次
        row = db.query("SELECT stock_id FROM mv_stock_snapshot WHERE in_universe "
                       "ORDER BY random() LIMIT 1")
        if not row:
            raise HTTPException(500, "母體是空的（mv_stock_snapshot 沒資料？）")
        sid = row[0]["stock_id"]
        pick = db.query(
            "SELECT trade_date FROM ("
            "  SELECT trade_date, row_number() OVER (ORDER BY trade_date) rn,"
            "         count(*) OVER () total FROM price_daily"
            "  WHERE stock_id=%(id)s AND adj_close IS NOT NULL) z "
            "WHERE rn >= %(b)s AND rn <= total - %(h)s ORDER BY random() LIMIT 1",
            {"id": sid, "b": bars, "h": horizon})
        if pick:
            break
    else:
        raise HTTPException(500, "抽不到符合長度的樣本，請調小 bars / horizon")

    t = pick[0]["trade_date"]
    hist = db.query(
        "SELECT trade_date, adj_open AS open, adj_high AS high, adj_low AS low,"
        "       adj_close AS close, volume FROM ("
        "  SELECT *, row_number() OVER (ORDER BY trade_date DESC) rn FROM price_daily"
        "  WHERE stock_id=%(id)s AND adj_close IS NOT NULL AND trade_date <= %(t)s) z "
        "WHERE rn <= %(n)s ORDER BY trade_date", {"id": sid, "t": t, "n": bars})
    if len(hist) < 40:
        raise HTTPException(500, "抽到的樣本歷史不足，請再抽一次")

    rid = db.execute("INSERT INTO drill_attempt (stock_id, as_of, bars_before, horizon) "
                     "VALUES (%(s)s, %(t)s, %(b)s, %(h)s) RETURNING id",
                     {"s": sid, "t": t, "b": len(hist), "h": horizon}, returning=True)[0]["id"]
    return {"id": rid, "horizon": horizon, "bars": _synth(hist),
            "last_close": round(float(hist[-1]["close"]), 2)}


class Answer(BaseModel):
    dir: str                                   # bull / bear / neutral
    support: float | None = None
    resistance: float | None = None
    entry: bool = False
    stop: float | None = None
    confidence: int = 3                        # 1~5
    note: str | None = None


@router.post("/{rid}/answer")
def answer(rid: int, a: Answer):
    """記錄判斷。已作答或已揭曉的題目不可再改（不然統計就沒意義了）。"""
    _ensure()
    rows = db.query("SELECT answered_at, revealed_at FROM drill_attempt WHERE id=%(i)s", {"i": rid})
    if not rows:
        raise HTTPException(404, "找不到這題")
    if rows[0]["answered_at"]:
        raise HTTPException(400, "這題已經作答過了，不能改答案")
    if a.dir not in ("bull", "bear", "neutral"):
        raise HTTPException(400, "dir 需為 bull / bear / neutral")
    db.execute(
        "UPDATE drill_attempt SET answered_at=now(), ans_dir=%(d)s, ans_support=%(sp)s,"
        " ans_resistance=%(rs)s, ans_entry=%(e)s, ans_stop=%(st)s, ans_confidence=%(c)s,"
        " ans_note=%(n)s WHERE id=%(i)s",
        {"i": rid, "d": a.dir, "sp": a.support, "rs": a.resistance, "e": a.entry,
         "st": a.stop, "c": max(1, min(int(a.confidence), 5)), "n": (a.note or "")[:300]})
    return {"ok": True}


@router.post("/{rid}/reveal")
def reveal(rid: int):
    """揭曉未來走勢 + 真實身分，並算對錯。必須先作答。"""
    _ensure()
    rows = db.query("SELECT * FROM drill_attempt WHERE id=%(i)s", {"i": rid})
    if not rows:
        raise HTTPException(404, "找不到這題")
    r = rows[0]
    if not r["answered_at"]:
        raise HTTPException(400, "請先作答再揭曉——先看答案就練不到東西了")

    fut = db.query(
        "SELECT trade_date, adj_open AS open, adj_high AS high, adj_low AS low,"
        "       adj_close AS close, volume FROM ("
        "  SELECT *, row_number() OVER (ORDER BY trade_date) rn FROM price_daily"
        "  WHERE stock_id=%(id)s AND adj_close IS NOT NULL AND trade_date > %(t)s) z "
        "WHERE rn <= %(n)s ORDER BY trade_date",
        {"id": r["stock_id"], "t": r["as_of"], "n": r["horizon"]})
    if not fut:
        raise HTTPException(500, "這題沒有未來資料（資料被刪過？）")

    base = db.query("SELECT adj_close c FROM price_daily WHERE stock_id=%(id)s AND trade_date=%(t)s",
                    {"id": r["stock_id"], "t": r["as_of"]})[0]["c"]
    base = float(base)
    ret = float(fut[-1]["close"]) / base - 1
    hi = max(float(b["high"]) for b in fut) / base - 1
    lo = min(float(b["low"]) for b in fut) / base - 1
    d = r["ans_dir"]
    correct = (ret > 0) if d == "bull" else (ret < 0) if d == "bear" else abs(ret) < NEUTRAL_BAND

    if not r["revealed_at"]:                   # 只在第一次揭曉時寫入，重看不會蓋掉
        db.execute(
            "UPDATE drill_attempt SET revealed_at=now(), base_close=%(b)s, fwd_ret=%(r)s,"
            " fwd_high_pct=%(h)s, fwd_low_pct=%(l)s, correct=%(c)s WHERE id=%(i)s",
            {"i": rid, "b": round(base, 4), "r": round(ret, 6), "h": round(hi, 6),
             "l": round(lo, 6), "c": correct})

    name = db.query("SELECT name, industry FROM stock WHERE stock_id=%(i)s", {"i": r["stock_id"]})
    return {
        "id": rid, "correct": correct,
        "stock_id": r["stock_id"], "name": (name[0]["name"] if name else None),
        "industry": (name[0]["industry"] if name else None),
        "as_of": r["as_of"].isoformat(),
        "future": _synth(fut, offset=r["bars_before"]),      # 接在歷史後面的合成時間軸
        "future_dates": [b["trade_date"].isoformat() for b in fut],
        "base_close": round(base, 2),
        "fwd_ret": round(ret, 4), "fwd_high_pct": round(hi, 4), "fwd_low_pct": round(lo, 4),
        "ans_dir": d, "ans_confidence": r["ans_confidence"],
    }


@router.get("/stats")
def stats():
    """累積戰績：整體正確率、依方向、依信心分層。信心分層最有價值——
    如果高信心的正確率沒有比低信心高，代表你的『自信』沒有資訊量。"""
    _ensure()
    base = "FROM drill_attempt WHERE revealed_at IS NOT NULL"
    tot = db.query(f"SELECT count(*) n, count(*) FILTER (WHERE correct) ok,"
                   f" avg(fwd_ret) avg_ret {base}")[0]
    by_dir = db.query(f"SELECT ans_dir, count(*) n, count(*) FILTER (WHERE correct) ok,"
                      f" avg(fwd_ret) avg_ret {base} GROUP BY ans_dir ORDER BY ans_dir")
    by_conf = db.query(f"SELECT ans_confidence c, count(*) n, count(*) FILTER (WHERE correct) ok "
                       f"{base} GROUP BY ans_confidence ORDER BY ans_confidence")

    def rate(n, ok):
        return round(ok / n, 4) if n else None

    return {
        "n": tot["n"], "correct": tot["ok"], "accuracy": rate(tot["n"], tot["ok"]),
        "avg_ret": round(float(tot["avg_ret"]), 4) if tot["avg_ret"] is not None else None,
        "by_dir": [{"dir": r["ans_dir"], "n": r["n"], "accuracy": rate(r["n"], r["ok"]),
                    "avg_ret": round(float(r["avg_ret"]), 4) if r["avg_ret"] is not None else None}
                   for r in by_dir],
        "by_confidence": [{"confidence": r["c"], "n": r["n"], "accuracy": rate(r["n"], r["ok"])}
                          for r in by_conf],
    }


@router.get("/history")
def history(limit: int = 50):
    """近期作答紀錄（已揭曉），供回顧自己哪種盤看錯。"""
    _ensure()
    rows = db.query(
        "SELECT d.id, d.stock_id, s.name, d.as_of, d.ans_dir, d.ans_confidence, d.ans_entry,"
        "       d.ans_note, d.correct, d.fwd_ret, d.fwd_high_pct, d.fwd_low_pct, d.revealed_at "
        "FROM drill_attempt d LEFT JOIN stock s ON s.stock_id = d.stock_id "
        "WHERE d.revealed_at IS NOT NULL ORDER BY d.revealed_at DESC LIMIT %(n)s",
        {"n": max(1, min(int(limit), 300))})
    for r in rows:
        r["as_of"] = r["as_of"].isoformat()
        r["revealed_at"] = r["revealed_at"].isoformat()
        for k in ("fwd_ret", "fwd_high_pct", "fwd_low_pct"):
            if r[k] is not None:
                r[k] = float(r[k])
    return rows
