"""今日查詢的多條件交集；主策略維持原本的進場與部位規則。

先篩選完整候選，再交給決策中心排序、配置資金。加強條件不增加共識分數，
不覆寫單一策略歷史，也不沿用單一策略績效。週線結構只認已收完且有效的突破。
"""
from copy import deepcopy
from math import isfinite

from . import decision_center, weekly_breakout

CONDITIONS = {
    "trend_template": "趨勢模板成立",
    "weekly_structure": "週線結構成立（已確認突破）",
    "weekly": "週線買點可執行",
    "breakout": "型態突破可執行",
    "consolidation": "整理突破可執行",
    "price_action": "裸 K 可執行",
    "holder_concentration": "籌碼集中：近 4 期千張增、400 張以上不減",
    "growth_quality": "營收成長品質通過（實驗）",
    "lin_bojin": "林柏錦選股條件可執行（圖卡研究版）",
}
VERSION = "decision-combination-v1"


def parse_conditions(value, logic="all", mode="momentum", gate="trend_template"):
    codes = tuple(sorted({code.strip() for code in (value or "").split(",") if code.strip()}))
    if logic not in ("all", "any"):
        raise ValueError("組合方式須為 all（全部符合）或 any（任一符合）")
    missing = [code for code in codes if code not in CONDITIONS]
    if missing:
        raise ValueError("不支援的加強條件：" + "、".join(missing))
    if codes and mode == "classic":
        raise ValueError("組合篩選只支援動能模式與趨勢持有")
    if gate in codes or (gate == "weekly" and "weekly_structure" in codes):
        raise ValueError("加強條件已包含在主買點內，請選擇其他條件")
    return codes


def _positive(value):
    try:
        number = float(value)
        return number if isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _primary(item, gate):
    strategies = [s for s in item.get("strategies", []) if decision_center.strategy_used(s["key"], gate)]
    lead = max(strategies, key=lambda s: (s["status"] == "priority", s["score"]), default=None)
    ready = any(s["status"] == "priority" for s in strategies)
    probe = {**item, "strategies": strategies,
             "breakout_ready": any(s["key"] == "breakout" and s["status"] == "priority" for s in strategies)}
    passed = ready and decision_center._gate_ok(probe, gate)
    check = {"code": gate, "label": "主買點：" + decision_center.GATES[gate], "role": "primary",
             "passed": passed, "reason": "主買點可執行" if passed else
             decision_center._gate_reason(probe, gate) if ready else "主買點仍在等待／觀察"}
    entry = (decision_center._buy_stop_trigger(item, lead) if lead else None) or \
        _positive(item.get("raw_close")) or _positive(item.get("close"))
    return strategies, check, entry


def _check(item, code, entry):
    check = {"code": code, "label": CONDITIONS[code], "role": "confirmation", "passed": False,
             "reason": "尚未成立", "max_entry": None}
    if code == "trend_template":
        check.update(passed=bool(item.get("trend_template")),
                     reason="趨勢模板成立" if item.get("trend_template") else "多頭排列或 RS 門檻未通過")
        return check
    if code in ("holder_concentration", "growth_quality"):
        data = item.get(code) or {}
        check.update(passed=data.get("passed") is True,
                     reason=data.get("reason") or "資料不足，暫不通過",
                     **{("holder_data" if code == "holder_concentration" else "growth_data"): data})
        return check
    key = "weekly" if code == "weekly_structure" else code
    strategy = next((s for s in item.get("strategies", []) if s["key"] == key), None)
    if not strategy:
        check["reason"] = "沒有此策略的有效訊號或資料不足"
        return check
    if code == "weekly_structure":
        age = (strategy.get("weekly") or {}).get("weeks_since_breakout")
        check["passed"] = (strategy.get("status") in ("priority", "waiting")
                           and strategy.get("stage") in ("entry", "pullback")
                           and isinstance(age, int) and 0 <= age < weekly_breakout.ENTRY_WEEKS)
        check["reason"] = ("已收完的週線確認突破，仍在有效期且未失敗；日線買點由主策略判斷"
                           if check["passed"] else "週線尚未收盤確認、突破已失敗或有效期已結束")
        return check
    check["passed"] = strategy.get("status") == "priority" and not strategy.get("blockers")
    if code == "lin_bojin":
        check["lin_data"] = strategy.get("lin_bojin")
    if code == "consolidation" and not item.get("trend_template"):
        check["passed"] = False
        check["reason"] = "整理突破另需趨勢模板成立"
    else:
        check["reason"] = "可執行訊號成立" if check["passed"] else \
            "、".join(strategy.get("blockers") or ["仍在等待／觀察，尚未可執行"])
    if check["passed"]:
        cap = _positive(strategy.get("max_entry"))
        if code in ("weekly", "consolidation", "lin_bojin") and cap is None:
            check.update(passed=False, reason="追價上限資料不足，暫不通過")
            return check
        if cap:
            cap /= _positive(item.get("price_factor")) or 1
            check["max_entry"] = cap
            if entry is None or entry * (1 + decision_center.execution.SLIPPAGE) > cap + 1e-8:
                check.update(passed=False, reason=f"主買點參考進場價含滑價超過此條件的追價上限 {cap:.2f}")
    return check


def filter_scan(scan, codes, logic="all", gate="trend_template"):
    if not codes:
        return scan
    scoped = {**scan, "items": []}
    excluded = []
    considered = 0
    for source in scan.get("items", []):
        active, primary, entry = _primary(source, gate)
        if not active:
            continue
        considered += 1
        checks = [_check(source, code, entry) for code in codes]
        passing = [c for c in checks if c["passed"]]
        matched = len(passing) == len(checks) if logic == "all" else bool(passing)
        # OR 採用一個通過的條件；無上限／上限較高者先，避免把 OR 誤變成所有追價上限的交集。
        used = passing if logic == "all" else sorted(passing, key=lambda c: c["max_entry"] or float("inf"), reverse=True)[:1]
        caps = [c["max_entry"] for c in used if c["max_entry"]]
        item = deepcopy(source)
        item.update(combination_checks=[primary, *checks], combination_match=matched,
                    combination_ready=matched and primary["passed"],
                    combination_satisfied_by=[c["code"] for c in used],
                    combination_max_entry=min(caps) if caps else None)
        if matched:
            scoped["items"].append(item)
        else:
            excluded.append({key: item.get(key) for key in
                             ("stock_id", "name", "close", "rs_rating", "themes", "combination_checks")})
    excluded.sort(key=lambda item: (sum(c["passed"] for c in item["combination_checks"]),
                                   item.get("rs_rating") or 0), reverse=True)
    scoped["combination_filter"] = {
        "codes": list(codes), "logic": logic, "logic_label": "全部符合" if logic == "all" else "任一符合",
        "conditions": [{"code": code, "label": CONDITIONS[code]} for code in codes],
        "primary_gate": gate, "primary_label": decision_center.GATES[gate],
        "candidate_count": considered, "matched_count": len(scoped["items"]), "excluded_count": len(excluded),
        "excluded_items": excluded[:100], "excluded_shown": min(len(excluded), 100), "version": VERSION,
        "validation": "新組合尚未回測；單一策略績效不適用；今日查詢不覆寫基準歷史",
    }
    if "holder_concentration" in codes:
        scoped["combination_filter"]["holder_evaluated_at"] = scan.get("holder_evaluated_at")
        scoped["combination_filter"]["validation"] += "；籌碼以本次取得時點判斷，歷史發布時間未知者不回填"
    return scoped


def attach(response, scan):
    config = scan.get("combination_filter")
    if not config:
        return response
    response["combination_filter"] = config
    response["validation"] = {"status": "unvalidated", "message": config["validation"]}
    response["method"] += "；加強條件（" + config["logic_label"] + "）：" + "、".join(
        c["label"] for c in config["conditions"])
    # 基準的校準率與型態先驗不能當作此組合的勝率，組合畫面改列未驗證。
    response["calibration"] = []
    for item in response.get("items", []):
        item["calibration"] = item["historical_reference"] = None
        for strategy in item.get("strategies", []):
            strategy["score_calibration"] = None
            strategy["pattern_prior"] = None
        cap = item.get("combination_max_entry")
        if cap:
            plan = item["position_plan"]
            previous = _positive(plan.get("max_entry"))
            plan["max_entry"] = round(min(cap, previous) if previous else cap, 2)
            rule = f"組合追價上限 {plan['max_entry']:.2f}，超過就放棄"
            plan["entry_rule"] += "；" + rule
            item.setdefault("entry_conditions", []).append({
                "strategy": "combination", "label": "組合追價限制", "trigger_low": None,
                "trigger_high": plan["max_entry"], "invalid_below": None, "deadline": "下一個交易日",
                "lines": [rule],
            })
    return response
