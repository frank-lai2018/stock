"""今日決策的族群查詢範圍；沿用 theme／stock_theme，不改變買點規則。

多選採聯集，L2 節點含直接子節點；有效成分與 /api/themes/options 一致。
先限定候選，再交給決策中心排序、套用全帳戶持股限制與截取筆數。
族群內的臨時入選結果不寫入全市場每日追蹤。
"""
from copy import deepcopy

from . import db

MAX_THEMES = 30


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
