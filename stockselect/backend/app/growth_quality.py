"""營收成長品質 v1：同月年增消除部分季節性，評分與買點分開。

資料庫 available_date 為推估日期；隔一天才使用，仍無法還原晚報與歷史修訂。
同一純函式供今日查詢及歷史研究使用，不將現在的分數套回過去。
"""
from datetime import date, timedelta
from math import expm1, isfinite, log

from . import db

VERSION = "revenue-quality-v1"
LABEL = "營收成長品質（實驗）"
TIMING = "推估可得日後一天；缺日期不使用，未保存歷史公告時間與修訂版本"
RULE = "近3月營收年增≥10%、近3個同月年增皆正、近6期年增趨勢向上且R²≥50%、年增加速>0"
MAX_AGE_DAYS = 75


def day(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value is not None else None
    except ValueError:
        return None


def shift_month(month, n):
    year, index = divmod(month.year * 12 + month.month - 1 + n, 12)
    return date(year, index + 1, 1)


def finite(value):
    try:
        v = float(value)
        return v if isfinite(v) else None
    except (ValueError, TypeError, OverflowError):
        return None


def visible(row, as_of):
    month, available = day(row.get("revenue_month")), day(row.get("available_date"))
    return bool(month and month.day == 1 and available
                and available >= shift_month(month, 1) and available < as_of
                and month < as_of.replace(day=1))


def analyze(rows, as_of):
    as_of = day(as_of)
    result = {"version": VERSION, "as_of": str(as_of), "available": False, "passed": False,
              "score": None, "timing": TIMING, "rule": RULE, "warnings": []}

    def fail(reason):
        return {**result, "reason": reason}

    if as_of is None:
        return fail("缺少價格基準日")
    known = {day(r["revenue_month"]): r for r in rows if visible(r, as_of)}
    if not known:
        return fail("沒有基準日前可用的月營收")
    latest = max(known)
    result["revenue_month"] = str(latest)
    end = shift_month(latest, 1) - timedelta(days=1)
    if (as_of - end).days > MAX_AGE_DAYS:
        return fail("月營收資料過期（距月份結束超過75天）")
    months = [shift_month(latest, i) for i in range(-17, 1)]
    if any(m not in known for m in months):
        return fail("需18個連續月份營收，含去年同期；缺月暫不評分")
    values = [finite(known[m].get("revenue")) for m in months]
    if any(v is None or v <= 0 for v in values):
        return fail("18個月營收含缺值、非有限值或非正值，暫不評分")
    # 最後6期同月年增倍率：對照相同月份，避免把旺季當成成長加速。
    ratios = [values[i + 12] / values[i] for i in range(6)]
    if any(not isfinite(v) or v <= 0 for v in ratios):
        return fail("年增倍率無效，暫不評分")
    yoy = [(r - 1) * 100 for r in ratios]
    recent = (sum(values[-3:]) / sum(values[3:6]) - 1) * 100
    previous = (sum(values[-6:-3]) / sum(values[:3]) - 1) * 100
    accel = recent - previous
    y = [log(r) for r in ratios]
    mean = sum(y) / 6
    slope = sum((i - 2.5) * (v - mean) for i, v in enumerate(y)) / 17.5
    total = sum((v - mean) ** 2 for v in y)
    residual = sum((v - (mean + slope * (i - 2.5))) ** 2 for i, v in enumerate(y))
    r2 = max(0.0, min(1.0, 1 - residual / total)) if total > 1e-12 else 0.0
    slope_pct = expm1(slope) * 100
    if any(not isfinite(v) for v in [*yoy, recent, previous, accel, slope_pct]):
        return fail("成長指標超出可計算範圍，暫不評分")
    # 同一營收因子只給一個0～100分；不把RS、籌碼或買點加進來。
    components = {"growth": max(0, min(recent / 60, 1)) * 40,
                  "persistence": r2 * 30 if slope > 0 else 0,
                  "acceleration": max(0, min(accel / 30, 1)) * 30}
    failures = []
    if recent < 10:
        failures.append("近3月年增未達10%")
    if not all(v > 0 for v in yoy[-3:]):
        failures.append("近3個同月年增未全部為正")
    if slope <= 0 or r2 < .5:
        failures.append("近6期年增趨勢不足（需斜率>0、R²≥50%）")
    if accel <= 0:
        failures.append("近3月年增未高於前3月年增")
    if min(values[:6]) < sum(values[:6]) / 6 * .1 or sum(values[3:6]) < sum(values[:3]) * .25:
        result["warnings"].append("去年同期存在極低基期，年增可能被放大")
    result.update(available=True, passed=not failures, score=round(sum(components.values()), 1),
                  components={k: round(v, 2) for k, v in components.items()},
                  yoy_1m=round(yoy[-1], 2), yoy_3m=round(recent, 2), previous_yoy_3m=round(previous, 2),
                  acceleration_pp=round(accel, 2), excess_slope_pct=round(slope_pct, 2),
                  linearity_pct=round(r2 * 100, 2), months=18,
                  usable_from=str(max(day(known[m]["available_date"]) for m in months) + timedelta(days=1)))
    detail = (f"{latest:%Y-%m}｜近3月年增 {recent:+.2f}%、加速 {accel:+.2f} 百分點、"
              f"年增趨勢斜率 {slope_pct:+.2f}%／月、R² {r2:.1%}")
    result["reason"] = detail + ("；" + "、".join(failures) if failures else "；成長品質通過")
    return result


def series(stock_ids, as_of):
    """單次查詢每檔最多18月；NULL可得日不回填，避免把未知當已知。"""
    if not stock_ids:
        return {}
    rows = db.query(
        "SELECT stock_id,revenue_month,revenue,available_date FROM ("
        "SELECT stock_id,revenue_month,revenue,available_date,"
        "row_number() OVER(PARTITION BY stock_id ORDER BY revenue_month DESC) rn "
        "FROM monthly_revenue WHERE stock_id=ANY(%(ids)s) AND available_date<%(as_of)s "
        "AND available_date>=revenue_month+interval '1 month' "
        "AND revenue_month<date_trunc('month',%(as_of)s::date)) q "
        "WHERE rn<=18 ORDER BY stock_id,revenue_month", {"ids": list(stock_ids), "as_of": day(as_of)})
    grouped = {}
    for row in rows:
        grouped.setdefault(row["stock_id"], []).append(row)
    return grouped


def attach_scan(scan):
    rows = series({x["stock_id"] for x in scan.get("items", [])}, scan.get("as_of"))
    items = [{**x, "growth_quality": analyze(rows.get(x["stock_id"], []), scan.get("as_of"))}
             for x in scan.get("items", [])]
    return {**scan, "items": items, "growth_quality_config": {
        "version": VERSION, "label": LABEL, "rule": RULE, "timing": TIMING,
        "available_count": sum(x["growth_quality"]["available"] for x in items),
        "passed_count": sum(x["growth_quality"]["passed"] for x in items),
        "candidate_count": len(items), "as_of": scan.get("as_of"),
    }}


def attach(response, scan, ranking):
    config = scan.get("growth_quality_config")
    if not config:
        return response
    response["growth_quality_config"] = {**config, "ranking": ranking}
    response["validation"] = {"status": "unvalidated", "message": "營收品質為新實驗；歷史研究使用推估可得日，尚無前瞻驗證。組合績效另須驗證。"}
    response["calibration"] = []
    for item in response.get("items", []):
        item["calibration"] = item["historical_reference"] = None
        for strategy in item.get("strategies", []):
            strategy["score_calibration"] = strategy["pattern_prior"] = None
    return response
