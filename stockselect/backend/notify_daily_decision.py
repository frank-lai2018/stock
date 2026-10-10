"""LINE 每日日報；nightly 使用同次決策快照，獨立執行可唯讀重算。

python notify_daily_decision.py --check-config   # 僅檢查設定及 LINE 額度
python notify_daily_decision.py --test           # 傳一則連線測試（每天防重）
python notify_daily_decision.py --dry-run        # 僅預覽最新行情，不寫決策帳／不推播
python notify_daily_decision.py                 # 僅當日行情可補送，休市／歷史日略過
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from app import line_notify


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="型態突破＋趨勢持有60日 LINE 日報")
    ap.add_argument("--dsn", default="")
    ap.add_argument("--date", help="預期排程日；歷史／未來日期不推播")
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument("--check-config", action="store_true")
    actions.add_argument("--test", action="store_true")
    actions.add_argument("--dry-run", action="store_true")
    ap.add_argument("--snapshot", type=Path, help="同次 capture 產生的通知快照")
    ap.add_argument("--failed-jobs", default="", help="必要資料更新失敗的工作名，逗號分隔")
    ap.add_argument("--warning-jobs", default="", help="其他更新未完成的工作名，逗號分隔")
    args = ap.parse_args(argv)
    if args.dsn:
        os.environ["DATABASE_URL"] = args.dsn
    try:
        # check-config／test 不依賴 DB 或資料分析套件。
        from datetime import timedelta, timezone
        local_today = datetime.now(timezone(timedelta(hours=8))).date()
        requested = date.fromisoformat(args.date) if args.date else local_today
        if args.check_config:
            auth = line_notify.credentials()
            if auth is None:
                raise line_notify.NotificationError("尚未設定 LINE_TOKEN 與 LINE_USER_ID")
            quota = line_notify.check_quota(auth[0])
            print("LINE_TOKEN／LINE_USER_ID 已讀取，格式正確。")
            print(f"本月額度：{quota['limit'] or '無固定上限'}；已使用：{quota['used']}；剩餘：{quota['remaining']}")
            return 0
        if not args.dry_run:
            auth = line_notify.credentials()
            if auth is None:
                print("未設定 LINE 環境變數，略過通知。")
                return 0
            if requested != local_today:
                print("歷史或未來排程日，略過 LINE 推播。")
                return 0
        if args.test:
            text = ("✅ 台股選股 LINE 通知連線測試\n"
                    "已接上：型態突破＋趨勢持有60日（實驗）\n"
                    "交易日每晚必要資料更新與決策保存完成後，發送策略摘要與交易帳出場檢查。\n"
                    "休市日略過正常日報；這是連線測試，不是買進訊號。")
            result = line_notify.send_once(f"test/{requested}", text, auth=auth)
            print(result["message"])
            return 0
        from app import decision_digest as digest, notification_calendar
        failures = [j for j in args.failed_jobs.split(",") if j]
        warnings = [j for j in args.warning_jobs.split(",") if j]
        if failures:
            text = digest.failure_text(requested, ["更新工作失敗：" + "、".join(failures)])
            key = f"failure/{requested}/{digest.PROFILE}"
        else:
            if args.snapshot:
                snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(snapshot["created_at"])
                if (snapshot.get("version") != digest.VERSION or snapshot.get("profile") != digest.PROFILE
                        or snapshot.get("requested_date") != requested.isoformat()
                        or created.tzinfo is None
                        or not 0 <= (datetime.now(digest.TAIPEI) - created).total_seconds() < 12 * 3600):
                    raise line_notify.NotificationError("通知快照過期或版本／日期不符")
                response = snapshot["response"]
            else:
                from app import db, decision_center as dc
                latest = db.query("SELECT max(trade_date) AS d FROM price_daily")[0]["d"]
                if not args.dry_run and latest != requested:
                    reason = notification_calendar.closed_reason(requested)
                    if reason:
                        print(f"{reason}，略過正常 LINE 日報。")
                        return 0
                scan = dc.scan_market(min_amt=20_000_000, recent=3)
                response = digest.build_response(scan, dc.current_holdings(), dc.market_regime(scan.get("as_of")))
                snapshot = digest.make_snapshot(response, requested)
            if response.get("mode") != "trend_hold" or response.get("gate") != "breakout" or response.get("settings", {}).get("ranking") != "rs":
                raise line_notify.NotificationError("通知快照策略設定不符")
            if not args.dry_run and response.get("as_of") != requested.isoformat():
                reason = notification_calendar.closed_reason(requested)
                if reason:
                    print(f"{reason}，略過正常 LINE 日報。")
                    return 0
                failures.append("行情尚未更新到排程日；本次不推播舊買點")
            if not args.dry_run and response.get("data_health", {}).get("healthy") is not True:
                failures.append("選股資料尚未刷新到最新行情")
            if not args.dry_run and response.get("scanned", 0) <= 0:
                failures.append("沒有可用的選股母體資料")
            text = (digest.failure_text(requested, failures) if failures else digest.render_digest(snapshot, warnings))
            key = f"{'failure' if failures else 'daily'}/{requested}/{digest.PROFILE}"
        if args.dry_run:
            print("[預覽：未推播、未寫入決策紀錄]\n" + text)
            return 0
        result = line_notify.send_once(key, text, auth=auth)
        print(result["message"])
        return 2 if failures and not args.failed_jobs else 0  # 2：已告警，但行情／快照尚未完成
    except line_notify.NotificationError as exc:
        print("LINE 通知：" + str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # SQL／系統例外可能帶 DSN，不回顯內容。
        print(f"LINE 通知：處理失敗（{type(exc).__name__}），未發送正常日報。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
