"""今日決策中心 API。"""
import threading
import time
from copy import deepcopy
from datetime import date

from fastapi import APIRouter

from .. import decision_center


router = APIRouter(prefix="/api", tags=["decision"])

_cache = {}
_cache_lock = threading.Lock()
_CACHE_SECONDS = 300


def _cached_scan(**kwargs):
    key = tuple(sorted(kwargs.items()))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return deepcopy(hit[1])
    result = decision_center.scan_market(**kwargs)
    with _cache_lock:
        _cache[key] = (now, result)
        # 避免開發時反覆改條件讓 cache 無限成長。
        if len(_cache) > 20:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
    return deepcopy(result)


@router.get("/screen/daily-decision")
def daily_decision(capital: float = 1_000_000, risk_per_trade_pct: float = 0.75,
                   max_new_positions: int = 3, max_industry_positions: int = 2,
                   max_position_pct: float = 25, lot_size: int = 1000,
                   min_amt: int = 20_000_000, recent: int = 3,
                   lookback: int = 5, expiry: int = 5,
                   eps_min: float | None = None, revenue_yoy_min: float | None = None,
                   gross_margin_chg_min: float | None = None, limit: int = 200):
    """整合多方突破與多方裸 K，回傳最多 N 檔新倉及未入選原因。

    capital 是這次可投入資金；risk_per_trade_pct 是每檔最多承擔的總資金風險。
    現有持股只用來限制同產業檔數，不會從 capital 重複扣除。
    """
    scan = _cached_scan(
        min_amt=max(0, int(min_amt)), recent=max(1, min(int(recent), 25)),
        lookback=max(1, min(int(lookback), 10)), expiry=max(1, min(int(expiry), 10)),
        eps_min=eps_min, revenue_yoy_min=revenue_yoy_min,
        gross_margin_chg_min=gross_margin_chg_min)
    # 先結算舊觀察，再建立完整決策畫面；保存時連同入選原因與部位一起留下。
    settled = decision_center.settle_pending(scan.get("as_of"))
    calibrations = decision_center.calibration_rows()
    holdings = decision_center.current_holdings()
    response = decision_center.build_decision_response(
        scan, calibrations, holdings,
        capital=max(1, float(capital)),
        risk_per_trade_pct=max(0.01, min(float(risk_per_trade_pct), 10)),
        max_new_positions=max(1, min(int(max_new_positions), 20)),
        max_industry_positions=max(1, min(int(max_industry_positions), 20)),
        max_position_pct=max(1, min(float(max_position_pct), 100)),
        lot_size=1 if int(lot_size) == 1 else 1000,
        limit=max(1, min(int(limit), 500)))
    recorded = decision_center.record_candidates(scan, response)
    response["tracking"] = {"recorded": recorded, "settled": settled}
    return response


@router.get("/screen/daily-decision/history")
def daily_decision_history(stock_id: str | None = None, strategy: str | None = None,
                           outcome_status: str | None = None,
                           date_from: date | None = None, date_to: date | None = None,
                           selected_only: bool = False, limit: int = 500):
    """依觀察日回看原始決策，並追蹤達標、停損、到期或目前 R。"""
    return decision_center.decision_history(
        stock_id=stock_id, strategy=strategy, outcome_status=outcome_status,
        date_from=date_from, date_to=date_to, selected_only=selected_only,
        limit=max(1, min(int(limit), 1000)))
