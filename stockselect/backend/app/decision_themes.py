"""今日決策的族群查詢範圍；沿用 theme／stock_theme，不改變買點規則。

多選採聯集，L2 節點含直接子節點；有效成分與 /api/themes/options 一致。
先限定候選，再交給決策中心排序、套用全帳戶持股限制與截取筆數。
族群內的臨時入選結果不寫入全市場每日追蹤。
"""
from copy import deepcopy
import re

from . import db

MAX_THEMES = 30

# 表格使用短名稱，完整名稱仍保留供提示；代碼沿用既有族群，沒有逐檔猜測分類。
_DISPLAY_NAMES = {
    "tpex:L000:L630": "CCL",
    "memory": "記憶體",
    "passive": "被動元件",
    "power_semi": "功率元件",
    "pcb": "PCB",
}


def parse_codes(value):
    """API 使用逗號分隔的代碼；去空白、去重，空值代表全部族群。"""
    codes = tuple(sorted({code.strip() for code in (value or "").split(",") if code.strip()}))
    if len(codes) > MAX_THEMES:
        raise ValueError(f"一次最多選擇 {MAX_THEMES} 個族群")
    return codes


def filter_scan(scan, codes):
    """只篩選未截斷的掃描結果；不修改共用快取。沒有成分或訊號時回空清單。"""
    if not codes:
        return scan
    themes = db.query(
        "SELECT t.code, t.layer, CASE WHEN t.layer=2 AND c.name IS NOT NULL "
        "THEN c.name || ' > ' || t.name ELSE t.name END AS name "
        "FROM theme t LEFT JOIN theme c ON t.layer=2 AND c.parent_code IS NULL "
        "AND c.code=split_part(t.code, ':', 1) || ':' || split_part(t.code, ':', 2) AND c.code<>t.code "
        "WHERE t.is_active AND t.code=ANY(%(codes)s)", {"codes": list(codes)})
    by_code = {theme["code"]: theme for theme in themes}
    missing = [code for code in codes if code not in by_code]
    if missing:
        raise ValueError("不存在或停用的族群：" + "、".join(missing))
    members = db.query(
        "SELECT DISTINCT chosen.code, st.stock_id FROM theme chosen "
        "JOIN theme member ON (member.code=chosen.code OR member.parent_code=chosen.code) AND member.is_active "
        "JOIN stock_theme st ON st.theme_id=member.theme_id "
        "WHERE chosen.is_active AND chosen.code=ANY(%(codes)s) AND st.valid_to IS NULL "
        "AND ((member.layer=2 AND st.status='confirmed') "
        "OR (member.layer=3 AND st.status IN ('confirmed', 'seed')))", {"codes": list(codes)})
    memberships = {}
    for member in members:
        memberships.setdefault(member["stock_id"], set()).add(member["code"])
    scoped = {**scan, "items": []}
    for item in scan.get("items", []):
        matched = memberships.get(item["stock_id"], set())
        if matched:
            candidate = deepcopy(item)
            candidate["themes"] = [by_code[code] for code in codes if code in matched]
            scoped["items"].append(candidate)
    scoped["theme_filter"] = {
        "codes": list(codes), "themes": [by_code[code] for code in codes],
        "member_count": len(memberships),
    }
    return scoped


def record_candidates(scan, response, mode, gate, recorder):
    """族群或組合內重排不能覆寫同日、同模型的全市場入選結果。"""
    if scan.get("theme_filter") or scan.get("combination_filter") or scan.get("growth_quality_config"):
        return 0
    return recorder(scan, response, mode, gate)


def attach_display_themes(response):
    """決策完成後批次補上顯示族群；不修改產業風險分類或族群篩選的命中標籤。

    CCL 具體節點優先，其次為 L3 市場題材；沒有這些分類才使用 L2 產品節點。
    分類代表現有有效成分關係，不代表該族群今日熱度或新增買點條件。
    """
    items = [*response.get("items", []),
             *(response.get("combination_filter") or {}).get("excluded_items", [])]
    stock_ids = sorted({item["stock_id"] for item in items})
    if not stock_ids:
        return response
    members = db.query(
        "SELECT DISTINCT st.stock_id, t.code, t.name, t.layer FROM stock_theme st "
        "JOIN theme t USING (theme_id) "
        "WHERE st.stock_id=ANY(%(ids)s) AND st.valid_to IS NULL AND t.is_active "
        "AND ((t.layer=2 AND t.parent_code IS NOT NULL AND st.status='confirmed') "
        "OR (t.layer=3 AND st.status IN ('confirmed', 'seed'))) "
        "ORDER BY st.stock_id, t.layer DESC, t.code", {"ids": stock_ids})
    by_stock = {}
    for member in members:
        by_stock.setdefault(member["stock_id"], []).append(member)
    labels = {}
    for stock_id, themes in by_stock.items():
        preferred = [t for t in themes if t["code"] == "tpex:L000:L630" or t["layer"] == 3]
        ordered = sorted(preferred or themes,
                         key=lambda t: (t["code"] != "tpex:L000:L630", t["code"]))
        names = set()
        labels[stock_id] = []
        for theme in ordered:
            full_name = theme["name"]
            name = _DISPLAY_NAMES.get(theme["code"]) or re.split(r"[（(]", full_name.split(">")[-1])[0].strip()
            if not name or name in {"其他", "其它", "未分類"} or name in names:
                continue
            names.add(name)
            labels[stock_id].append({"code": theme["code"], "name": name,
                                     "full_name": full_name, "layer": theme["layer"]})
    for item in items:
        item["display_themes"] = deepcopy(labels.get(item["stock_id"], []))
    return response
