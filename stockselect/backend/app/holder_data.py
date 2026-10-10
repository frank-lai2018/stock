"""TDCC 週資料：分級彙總、期間比較與查詢時點可得性。

人數只加總 1~15 級；16 是差異，17 是合計，均不可當成持股級距。
舊資料缺合計時標示分級加總。未知發布時間以本機確認取得時間為準，
不把資料日期冒充發布時間；修訂後的值只可用於修訂被觀察到之後。
"""
from datetime import date, datetime
from math import isfinite

from . import db

MAX_GAP_DAYS = 10
MAX_AGE_DAYS = 14
METRICS = ("big1000_pct", "big400_pct", "retail_pct", "holders_total")


def day(value):
    return date.fromisoformat(str(value)[:10])


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize(row):
    row = dict(row)
    for key, value in row.items():
        if isinstance(value, (date, datetime)):
            row[key] = value.isoformat()
    official = row.get("official_shares") is not None
    row["total_shares"] = row.get("official_shares") if official else row.get("graded_shares")
    row["holders_total"] = row.get("official_holders") if row.get("official_holders") is not None else row.get("graded_holders")
    row["total_source"] = "官方合計" if official else "分級加總（缺官方合計）"
    row["total_lots"] = number(row["total_shares"])
    if row["total_lots"] is not None:
        row["total_lots"] /= 1000
    for prefix in ("big400", "big1000"):
        value = number(row.get(prefix + "_shares"))
        row[prefix + "_lots"] = value / 1000 if value is not None else None
    holders = number(row["holders_total"])
    row["avg_lots_per_holder"] = row["total_lots"] / holders if holders and row["total_lots"] is not None else None
    problems = []
    if row.get("level_count") != 15 or row.get("valid_count") != 15:
        problems.append("持股分級缺漏或數值無效")
    if not holders or not row["total_lots"] or row["total_lots"] <= 0:
        problems.append("合計人數／股數無效")
    row["shares_difference"] = None
    if official and row.get("graded_shares") is not None and row.get("adjustment_shares") is not None:
        delta = int(row["graded_shares"]) - int(row["adjustment_shares"]) - int(row["official_shares"])
        row["shares_difference"] = delta
        if delta:
            problems.append("分級股數扣除差異不等於官方合計")
    if row.get("official_holders") is not None and row.get("graded_holders") != row["official_holders"]:
        problems.append("分級人數與官方合計不一致")
    # 官方各級比例已四捨五入；不自行改算成不同分母的比例。
    row["quality_ok"] = not problems
    row["quality_reason"] = "、".join(problems) or ("合計檢查通過" if official and row["shares_difference"] == 0 else "分級完整；歷史缺官方合計／差異，無法核對")
    return row


def series(stock_ids, weeks=104, as_of=None, known_at=None, with_prices=False):
    """單次 SQL 彙總所有股票；known_at 只選當時已確認取得的版本。"""
    ids = list(stock_ids)
    if not ids:
        return {}
    params = {"ids": ids, "n": max(4, min(int(weeks), 520)), "d": as_of or date.today(), "known": known_at}
    price_select = ", p.close, p.trade_date AS price_date" if with_prices else ""
    price_join = (" LEFT JOIN LATERAL (SELECT close, trade_date FROM price_daily p WHERE p.stock_id=z.stock_id "
                  "AND p.trade_date<=z.data_date ORDER BY trade_date DESC LIMIT 1) p ON true") if with_prices else ""
    rows = db.query("""
      WITH grouped AS (
        SELECT h.stock_id, h.data_date,
          count(*) FILTER (WHERE level BETWEEN 1 AND 15) AS level_count,
          count(*) FILTER (WHERE level BETWEEN 1 AND 15 AND holders>=0 AND shares>=0 AND pct BETWEEN 0 AND 100) AS valid_count,
          sum(shares) FILTER (WHERE level BETWEEN 1 AND 15) AS graded_shares,
          sum(holders) FILTER (WHERE level BETWEEN 1 AND 15) AS graded_holders,
          max(shares) FILTER (WHERE level=17) AS official_shares,
          max(holders) FILTER (WHERE level=17) AS official_holders,
          max(shares) FILTER (WHERE level=16) AS adjustment_shares,
          sum(pct) FILTER (WHERE level=15) AS big1000_pct,
          sum(shares) FILTER (WHERE level=15) AS big1000_shares,
          sum(holders) FILTER (WHERE level=15) AS big1000_holders,
          sum(pct) FILTER (WHERE level BETWEEN 12 AND 15) AS big400_pct,
          sum(shares) FILTER (WHERE level BETWEEN 12 AND 15) AS big400_shares,
          sum(holders) FILTER (WHERE level BETWEEN 12 AND 15) AS big400_holders,
          sum(pct) FILTER (WHERE level BETWEEN 4 AND 11) AS mid_pct,
          sum(pct) FILTER (WHERE level BETWEEN 1 AND 3) AS retail_pct,
          max(holders) FILTER (WHERE level=12) AS holders_400_600,
          max(holders) FILTER (WHERE level=13) AS holders_600_800,
          max(holders) FILTER (WHERE level=14) AS holders_800_1000,
          o.first_seen_at, o.values_seen_at, o.published_at,
          greatest(o.first_seen_at, o.values_seen_at, o.published_at) AS available_at
        FROM shareholding_dist h LEFT JOIN shareholding_observation o USING (stock_id, data_date)
        WHERE h.stock_id=ANY(%(ids)s) AND h.data_date<=%(d)s
          AND (%(known)s::timestamptz IS NULL OR
               greatest(o.first_seen_at,o.values_seen_at,o.published_at)<=%(known)s::timestamptz)
        GROUP BY h.stock_id,h.data_date,o.first_seen_at,o.values_seen_at,o.published_at
      ), ranked AS (
        SELECT *, row_number() OVER (PARTITION BY stock_id ORDER BY data_date DESC) AS rn FROM grouped
      ) SELECT z.*
    """ + price_select + " FROM ranked z" + price_join + " WHERE rn<=%(n)s ORDER BY z.stock_id,z.data_date", params)
    result = {sid: [] for sid in ids}
    for row in rows:
        result[row["stock_id"]].append(normalize(row))
    return result


def window(rows, periods):
    if len(rows) < periods + 1:
        return None, f"需至少 {periods + 1} 期完整資料"
    selected = rows[-periods - 1:]
    if any(not r.get("quality_ok") for r in selected):
        return None, "比較期間內分級缺漏或合計檢查未通過"
    if any(not 0 < (day(b["data_date"]) - day(a["data_date"])).days <= MAX_GAP_DAYS
           for a, b in zip(selected, selected[1:])):
        return None, "比較期間缺週資料，暫不跨缺口計算"
    return selected, None


def summary(rows):
    changes = []
    for n in (1, 4, 8):
        selected, reason = window(rows, n)
        values = {}
        for key in METRICS:
            a, b = (number(selected[-1].get(key)), number(selected[0].get(key))) if selected else (None, None)
            values[key] = round(a - b, 4) if a is not None and b is not None else None
        changes.append({"periods": n, "from_date": selected[0]["data_date"] if selected else None,
                        "to_date": rows[-1]["data_date"] if rows else None, "reason": reason, **values})
    up = 0
    for a, b in reversed(list(zip(rows, rows[1:]))):
        pair, _ = window([a, b], 1)
        x, y = number(a.get("big1000_pct")), number(b.get("big1000_pct"))
        if not pair or x is None or y is None or y <= x:
            break
        up += 1
    return {"changes": changes, "big1000_up_periods": up, "count": len(rows)}


def concentration(rows, as_of):
    """近四期千張比例淨增加、400 張以上比例未下降；不是連四期上升。"""
    if not rows:
        return {"passed": False, "reason": "沒有查詢時點已取得的集保資料"}
    latest = rows[-1]
    age = (day(as_of) - day(latest["data_date"])).days
    if age < 0 or age > MAX_AGE_DAYS:
        return {"passed": False, "reason": "集保資料日期超前或距股價基準超過 14 日"}
    selected, reason = window(rows, 4)
    if not selected:
        return {"passed": False, "reason": reason, "data_date": latest["data_date"]}
    delta = {}
    for key in ("big1000_pct", "big400_pct"):
        a, b = number(selected[-1].get(key)), number(selected[0].get(key))
        if a is None or b is None:
            return {"passed": False, "reason": "大戶比例資料無效"}
        delta[key] = round(a - b, 4)
    passed = delta["big1000_pct"] > 0 and delta["big400_pct"] >= 0
    return {"passed": passed, "data_date": latest["data_date"], "from_date": selected[0]["data_date"],
            "available_at": latest.get("available_at"), "changes": delta,
            "reason": f"{selected[0]['data_date']} → {latest['data_date']}：千張 {delta['big1000_pct']:+.2f} 百分點、400 張以上 {delta['big400_pct']:+.2f} 百分點"}


def attach_scan(scan, known_at):
    # 呼叫端必須明確指定知識時點，避免研究默默套用今日資料。
    cutoff = known_at
    data = series([r["stock_id"] for r in scan.get("items", [])], weeks=9, as_of=scan["as_of"], known_at=cutoff)
    return {**scan, "holder_evaluated_at": cutoff.isoformat(), "items": [
        {**r, "holder_concentration": concentration(data.get(r["stock_id"], []), scan["as_of"])}
        for r in scan.get("items", [])]}
