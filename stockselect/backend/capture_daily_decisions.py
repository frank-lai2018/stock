"""每晚刷新快照後，保存今日決策候選並結算到期觀察。

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
    rows = decision_center.calibration_rows()
    holdings = decision_center.current_holdings()
    response = decision_center.build_decision_response(
        scan, rows, holdings, limit=500)
    recorded = decision_center.record_candidates(scan, response)
    print(f"今日決策中心：資料日 {scan.get('as_of') or '—'}｜掃描 {scan['scanned']} 檔｜"
          f"候選 {len(scan['items'])}｜新增觀察 {recorded}｜結算 {settled}｜校準組 {len(rows)}")


if __name__ == "__main__":
    main()
