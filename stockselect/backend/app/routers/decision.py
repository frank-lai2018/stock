"""今日決策中心 API。"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import decision_center, decision_combinations, decision_themes, entry_conditions, growth_quality, holder_data, research_results
from ..scan_cache import ScanCache


router = APIRouter(prefix="/api", tags=["decision"])


@router.get("/screen/daily-decision/research")
def decision_research():
    return research_results.load_report()


@router.get("/screen/daily-decision/growth-research")
def growth_research():
    from .. import growth_research
    return growth_research.load_report()

_scan_cache = ScanCache(ttl=300, max_entries=20)


def _cached_scan(**kwargs):
    return _scan_cache.get(decision_center.scan_market, **kwargs)


@router.get("/screen/daily-decision")
def daily_decision(capital: float = 1_000_000, risk_per_trade_pct: float = 0.75,
                   max_new_positions: int = 3, max_industry_positions: int = 2,
                   max_position_pct: float = 25, lot_size: int = 1000,
                   min_amt: int = 20_000_000, recent: int = 3,
                   lookback: int = 5, expiry: int = 5,
                   eps_min: float | None = None, revenue_yoy_min: float | None = None,
                   gross_margin_chg_min: float | None = None, limit: int = 200,
                   mode: str = decision_center.DEFAULT_MODE, gate: str = decision_center.DEFAULT_GATE,
                   available_capital: float | None = None, max_total_positions: int = 10,
                   max_total_risk_pct: float = 4, theme_codes: str | None = None,
                   combine: str | None = None, combine_logic: str = "all", ranking: str = "rs"):
    """整合多方突破與多方裸 K，回傳最多 N 檔新倉及未入選原因。

    capital 是帳戶淨值；available_capital 是可用現金，未傳入時假設全額可用。
    持股計入產業、總檔數與總風險；現有交易帳缺停損欄位，風險以市值 8% 估計。
    momentum 是 20 日基準；trend_hold 是最長 60 日／跌破 50 日線隔日出場。
    gate 可切換 trend_template、breakout、consolidation、weekly、reclaim、w_bottom；
    reclaim／w_bottom 是隔日盤中漲到觸發價才買。classic 保留原策略停損／目標。
    theme_codes 是逗號分隔的族群代碼，多選採聯集、L2 含子節點；在族群內重排，
    全帳戶持股仍納入風險。族群查詢不覆蓋全市場每日追蹤。
    combine 是加強條件代碼，combine_logic=all 要求全部符合，any 要求其中一個符合。
    主買點仍必須可執行；weekly_structure 只認已收完且有效的突破，weekly 要求日線買點。
    組合在排名與配置資金前套用；臨時組合不覆寫基準歷史、不沿用基準校準與績效。
    """
    mode = decision_center.normalize_mode(mode)
    gate = decision_center.normalize_gate(gate)
    try:
        codes = decision_themes.parse_codes(theme_codes)
        conditions = decision_combinations.parse_conditions(combine, combine_logic, mode, gate)
        if ranking not in ("rs", "growth_quality") or (mode == "classic" and ranking != "rs"):
            raise ValueError("排序僅支援 rs／growth_quality；原始規則不支援營收品質排序")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    lookback, expiry = max(1, min(int(lookback), 10)), max(1, min(int(expiry), 10))
    scan = _cached_scan(
        min_amt=max(0, int(min_amt)), recent=max(1, min(int(recent), 25)),
        lookback=lookback, expiry=expiry,
        eps_min=eps_min, revenue_yoy_min=revenue_yoy_min,
        gross_margin_chg_min=gross_margin_chg_min)
    try:
        scan = decision_themes.filter_scan(scan, codes)
        if "growth_quality" in conditions or ranking == "growth_quality":
            scan = growth_quality.attach_scan(scan)
        if "holder_concentration" in conditions:
            scan = holder_data.attach_scan(scan, datetime.now(timezone.utc))
        scan = decision_combinations.filter_scan(scan, conditions, combine_logic, gate)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # 先結算舊觀察，再建立完整決策畫面；保存時連同入選原因與部位一起留下。
    settled = decision_center.settle_pending(scan.get("as_of"))
    calibrations = decision_center.calibration_rows(mode)
    holdings = decision_center.current_holdings()
    market = decision_center.market_regime(scan.get("as_of"))
    response = decision_center.build_decision_response(
        scan, calibrations, holdings, mode=mode, market=market, gate=gate,
        capital=max(1, float(capital)),
        risk_per_trade_pct=max(0.01, min(float(risk_per_trade_pct), 10)),
        max_new_positions=max(1, min(int(max_new_positions), 20)),
        max_industry_positions=max(1, min(int(max_industry_positions), 20)),
        max_position_pct=max(1, min(float(max_position_pct), 100)),
        lot_size=1 if int(lot_size) == 1 else 1000,
        available_capital=max(0, float(available_capital)) if available_capital is not None else None,
        max_total_positions=max(1, min(int(max_total_positions), 50)),
        max_total_risk_pct=max(0, min(float(max_total_risk_pct), 100)),
        limit=max(1, min(int(limit), 500)), ranking=ranking)
    # 等待確認的訊號附上進場條件（只做說明，不影響入選）；放在記錄前，追蹤快照也看得到
    entry_conditions.attach(response, lookback=lookback, expiry=expiry)
    decision_combinations.attach(response, scan)
    growth_quality.attach(response, scan, ranking)
    response["theme_filter"] = scan.get("theme_filter")
    recorded = decision_themes.record_candidates(scan, response, mode, gate, decision_center.record_candidates)
    response["tracking"] = {"recorded": recorded, "settled": settled}
    return response


@router.get("/screen/daily-decision/history")
def daily_decision_history(stock_id: str | None = None, strategy: str | None = None,
                           outcome_status: str | None = None,
                           date_from: date | None = None, date_to: date | None = None,
                           selected_only: bool = False, limit: int = 500,
                           mode: str = decision_center.DEFAULT_MODE,
                           gate: str = decision_center.DEFAULT_GATE, legacy: bool = False):
    """依觀察日回看原始決策，並追蹤達標、停損、到期或目前 R；mode 決定讀哪一套規則的紀錄，
    gate 決定「當時入選」依哪個篩選條件（只對動能模式有效）。"""
    return decision_center.decision_history(
        stock_id=stock_id, strategy=strategy, outcome_status=outcome_status,
        date_from=date_from, date_to=date_to, selected_only=selected_only,
        limit=max(1, min(int(limit), 1000)), mode=mode, gate=gate, legacy=legacy)
