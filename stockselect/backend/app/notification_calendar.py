"""通知用證交所休市表；不能把「開始／最後交易日」資訊列誤當休市。

當日有新行情時以 DB 交易日為準；只有沒有當日行情時才查官方休市表。
颱風等臨時停市若不在表內，以「當日行情未取得」提醒，不推舊買點。
"""
import json
import time
from datetime import date
from urllib.request import urlopen

from .line_notify import LOCAL_DIR

HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"


def parse_holidays(rows, year):
    if not isinstance(rows, list) or len(rows) < 5:
        raise ValueError("休市表資料不足")
    dates = {}
    for row in rows:
        raw = str(row.get("Date", ""))
        if len(raw) != 7 or not raw.isdigit():
            raise ValueError("休市表日期格式異常")
        d = date(int(raw[:3]) + 1911, int(raw[3:5]), int(raw[5:7]))
        if d.year != year:
            raise ValueError("休市表年份不符")
        name = row.get("Name") or "官方休市日"
        if "開始交易" in name or "最後交易" in name:
            continue
        dates[d.isoformat()] = name
    return dates


def closed_reason(requested, *, cache_path=None, opener=urlopen, now=time.time):
    if requested.weekday() >= 5:
        return "週末休市"
    path = cache_path or LOCAL_DIR / "holiday_schedule.json"
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["year"] != requested.year or not isinstance(cached["fetched_at"], (int, float)) or not isinstance(cached["holidays"], dict):
            cached = None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if cached and now() - cached["fetched_at"] < 86400:
        return cached["holidays"].get(requested.isoformat())
    try:
        with opener(HOLIDAY_URL, timeout=10) as response:
            holidays = parse_holidays(json.load(response), requested.year)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"year": requested.year, "fetched_at": now(), "holidays": holidays},
                                   ensure_ascii=False), encoding="utf-8")
        return holidays.get(requested.isoformat())
    except (OSError, ValueError, KeyError, TypeError):
        # 無可信休市資訊時回 None；呼叫端顯示資料未取得，不當作正常更新成功。
        return cached["holidays"].get(requested.isoformat()) if cached else None
