"""每晚刷新快照後，保存今日決策候選並結算到期觀察。

兩種模式（momentum 動能模式、classic 原始規則）都記錄，追蹤紀錄以 model_version 分開，方便實盤對照；
動能模式的每個篩選條件（趨勢模板／型態突破）也各記一次入選結果。

可獨立執行：
  python capture_daily_decisions.py
  python capture_daily_decisions.py --dsn postgresql://...
"""
import argparse
import os
import sys


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="保存今日決策中心訊號，累積分數校準資料")
    ap.add_argument("--dsn", default="", help="PostgreSQL 連線字串；省略時讀 DATABASE_URL")
    ap.add_argument("--min-amt", type=int, default=20_000_000)
    args = ap.parse_args()
    if args.dsn:
        os.environ["DATABASE_URL"] = args.dsn

    # 設好 DATABASE_URL 後才載入 app.config。
    from app import decision_center

    scan = decision_center.scan_market(min_amt=args.min_amt)
    settled = decision_center.settle_pending(scan.get("as_of"))
    holdings = decision_center.current_holdings()
    market = decision_center.market_regime(scan.get("as_of"))
    parts = []
    for mode in decision_center.MODES:
        rows = decision_center.calibration_rows(mode)
        # 動能模式每個篩選條件都記一次入選結果；訊號列只有第一次會新增，其餘只補 gate_selection。
        for gate in (decision_center.GATES if mode == "momentum" else [decision_center.DEFAULT_GATE]):
            response = decision_center.build_decision_response(
                scan, rows, holdings, limit=500, mode=mode, market=market, gate=gate)
            recorded = decision_center.record_candidates(scan, response, mode, gate)
            name = f"{mode}/{gate}" if mode == "momentum" else mode
            parts.append(f"{name}：入選 {response['summary']['selected']}、新增觀察 {recorded}")
    above = market.get("above")
    regime = "資料不足" if above is None else ("站上" if above else "跌破") + f" {market['ma_days']} 日線"
    print(f"今日決策中心：資料日 {scan.get('as_of') or '—'}｜掃描 {scan['scanned']} 檔｜"
          f"候選 {len(scan['items'])}｜大盤{regime}｜{'｜'.join(parts)}｜結算 {settled}")


if __name__ == "__main__":
    main()
