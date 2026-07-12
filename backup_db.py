r"""backup_db.py — twstock 備份一鍵：pg_dump 邏輯備份 +（選配）robocopy 鏡像 H:\data + 自動輪替。

搭配 資料庫備份建議.md 的策略。建議每週跑一次（Task Scheduler），存到「非 H 碟」的備份空間。

做的事：
  1) pg_dump -Fc twstock → <out-dir>\twstock_YYYYMMDD_HHMM.dump（custom 壓縮格式）
  2) （給了 --data-dest 才做）robocopy /MIR 鏡像 H:\data → 備份碟
  3) 輪替：只保留最近 --keep 份 dump，刪更舊的
  4) （--globals）順便備份角色/權限 pg_dumpall --globals-only

密碼：從 --dsn 解析，或用環境變數 PGPASSWORD / DATABASE_URL。
需求：PostgreSQL 的 pg_dump / pg_dumpall 在 PATH（或用 --pg-bin 指到 bin 目錄）。

用法：
  python backup_db.py --dsn "postgresql://frank:pwd@localhost:5432/twstock" --out-dir E:\backup
  python backup_db.py --dsn "..." --out-dir E:\backup --data-dest E:\backup\data   # 連 H:\data 一起鏡像
  python backup_db.py --dsn "..." --out-dir E:\backup --keep 8 --globals
  python backup_db.py --dsn "..." --out-dir E:\backup --dry-run                     # 只印會做什麼
"""
import argparse
import glob
import os
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse, unquote


def parse_dsn(dsn):
    """postgresql://user:pw@host:port/db → dict。"""
    u = urlparse(dsn)
    return {
        "user": unquote(u.username) if u.username else "",
        "password": unquote(u.password) if u.password else "",
        "host": u.hostname or "localhost",
        "port": str(u.port or 5432),
        "db": (u.path or "/").lstrip("/") or "",
    }


def exe(pg_bin, name):
    return os.path.join(pg_bin, name) if pg_bin else name


def run(cmd, env, dry):
    printable = " ".join(cmd)
    print(f"    $ {printable}")
    if dry:
        return 0
    return subprocess.run(cmd, env=env).returncode


def rotate(out_dir, keep, dry):
    """只保留最近 keep 份 twstock_*.dump，刪更舊的（依檔名排序＝時間序）。"""
    dumps = sorted(glob.glob(os.path.join(out_dir, "twstock_*.dump")))
    old = dumps[:-keep] if keep > 0 else []
    if not old:
        print(f"  輪替：目前 {len(dumps)} 份，未超過保留 {keep} 份，不刪。")
        return
    print(f"  輪替：保留最近 {keep} 份，刪除 {len(old)} 份舊檔：")
    for p in old:
        print(f"    - {os.path.basename(p)}")
        if not dry:
            try:
                os.remove(p)
            except OSError as e:
                print(f"      （刪除失敗：{e}）")


def main():
    ap = argparse.ArgumentParser(description="twstock 備份：pg_dump + 鏡像 + 輪替")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--out-dir", required=True, help="dump 存放目錄（請放非 H 碟）")
    ap.add_argument("--keep", type=int, default=8, help="保留最近幾份 dump（預設 8；0=不刪）")
    ap.add_argument("--data-src", default=r"H:\data", help=r"要鏡像的 CSV 來源（預設 H:\data）")
    ap.add_argument("--data-dest", default="", help="CSV 鏡像目的地；給了才會做 robocopy /MIR")
    ap.add_argument("--globals", action="store_true", help="順便 pg_dumpall --globals-only 備份角色/權限")
    ap.add_argument("--pg-bin", default="", help="PostgreSQL bin 目錄（pg_dump 不在 PATH 時指定）")
    ap.add_argument("--dry-run", action="store_true", help="只印會執行什麼，不實際跑")
    args = ap.parse_args()

    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
    d = parse_dsn(args.dsn)
    if not d["db"]:
        raise SystemExit("--dsn 未指定資料庫名稱")

    env = dict(os.environ)
    if d["password"]:
        env["PGPASSWORD"] = d["password"]               # 讓 pg_dump 免互動輸密碼

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    dump_path = os.path.join(args.out_dir, f"twstock_{stamp}.dump")

    print(f"=== backup_db {datetime.now():%Y-%m-%d %H:%M:%S}"
          + ("（dry-run）" if args.dry_run else "") + " ===")
    fail = 0

    # 1) pg_dump
    print(f"\n[1] pg_dump {d['db']} → {dump_path}")
    rc = run([exe(args.pg_bin, "pg_dump"), "-U", d["user"], "-h", d["host"], "-p", d["port"],
              "-d", d["db"], "-Fc", "-f", dump_path], env, args.dry_run)
    if rc != 0:
        print(f"    ✗ pg_dump 失敗 (rc={rc})"); fail += 1
    elif not args.dry_run:
        sz = os.path.getsize(dump_path) / 1e9 if os.path.exists(dump_path) else 0
        print(f"    ✓ 完成，{sz:.2f} GB")

    # 2) globals（選配）
    if args.globals:
        gpath = os.path.join(args.out_dir, f"globals_{stamp}.sql")
        print(f"\n[2] pg_dumpall --globals-only → {gpath}")
        rc = run([exe(args.pg_bin, "pg_dumpall"), "-U", d["user"], "-h", d["host"], "-p", d["port"],
                  "--globals-only", "-f", gpath], env, args.dry_run)
        if rc != 0:
            print(f"    ✗ 失敗 (rc={rc})（角色備份常需 postgres 超級使用者）"); fail += 1
        else:
            print("    ✓ 完成")

    # 3) 鏡像 H:\data（選配）
    if args.data_dest:
        print(f"\n[3] robocopy /MIR {args.data_src} → {args.data_dest}")
        rc = run(["robocopy", args.data_src, args.data_dest, "/MIR", "/R:2", "/W:5", "/NFL", "/NDL"],
                 env, args.dry_run)
        # robocopy 回傳碼 0~7 為成功（>=8 才是錯誤）
        if not args.dry_run and rc >= 8:
            print(f"    ✗ robocopy 失敗 (rc={rc})"); fail += 1
        else:
            print(f"    ✓ 完成（robocopy rc={rc}）")
    else:
        print("\n[3] （未給 --data-dest，略過 H:\\data 鏡像）"
              "\n    ⚠️ 提醒：H:\\data 是資料的終極來源，強烈建議也鏡像到另一顆碟！")

    # 4) 輪替
    print(f"\n[4] 輪替舊 dump")
    rotate(args.out_dir, args.keep, args.dry_run)

    print(f"\n=== {'dry-run 結束' if args.dry_run else '完成'}"
          + (f"，{fail} 個步驟失敗" if fail else "，全部成功") + " ===")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
