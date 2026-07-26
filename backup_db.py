r"""backup_db.py — twstock 備份一鍵：每次建「日期時間資料夾」，放入 DB dump + data.zip，再 rclone 同步到雲端。

流程（一次備份 = 一個自足快照資料夾）：
  <out-dir>\
    20260726_1430\
      twstock_20260726_1430.dump   ← pg_dump -Fc（custom 壓縮）
      globals_20260726_1430.sql    ← （--globals）角色/權限
      data.zip                     ← H:\data 整包壓縮（--skip-data 可略過）
  1) 建立 <out-dir>\<YYYYMMDD_HHMM>\ 資料夾
  2) pg_dump 資料庫 → 該資料夾
  3) （--globals）pg_dumpall --globals-only → 該資料夾
  4) 把 H:\data 壓成 data.zip → 該資料夾
  5) 輪替：只保留最近 --keep 個日期資料夾，刪更舊的
  6) （--rclone-remote）rclone 同步到 Google Drive
       預設 rclone copy 這次的資料夾（附加、不刪雲端）；
       --rclone-sync 改成 rclone sync 整個 <out-dir>（鏡像，雲端會跟著輪替刪除）。

密碼：從 --dsn 解析，或環境變數 PGPASSWORD / DATABASE_URL。
需求：pg_dump / pg_dumpall 在 PATH（或 --pg-bin）；rclone 在 PATH（或 --rclone-bin）且已 `rclone config` 設好 remote。

用法：
  python backup_db.py --dsn "postgresql://frank:pwd@localhost:5432/twstock" --out-dir E:\backup
  python backup_db.py --dsn "..." --out-dir E:\backup --globals
  python backup_db.py --dsn "..." --out-dir E:\backup --rclone-remote gdrive:twstock_backup
  python backup_db.py --dsn "..." --out-dir E:\backup --rclone-remote gdrive:twstock_backup --rclone-sync
  python backup_db.py --dsn "..." --out-dir E:\backup --dry-run
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse, unquote

STAMP_RE = re.compile(r"^\d{8}_\d{4}$")     # 日期資料夾名 YYYYMMDD_HHMM


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
    print(f"    $ {' '.join(cmd)}")
    if dry:
        return 0
    return subprocess.run(cmd, env=env).returncode


def zip_data(data_src, dest_zip, dry):
    """把 data_src 整包壓成 dest_zip（單一 zip，利於雲端同步）。"""
    if not os.path.isdir(data_src):
        print(f"    ⚠️ 找不到 {data_src}，略過 data.zip")
        return False
    if dry:
        print(f"    （dry-run：會壓 {data_src} → {dest_zip}）")
        return True
    base = dest_zip[:-4] if dest_zip.lower().endswith(".zip") else dest_zip
    shutil.make_archive(base, "zip", root_dir=data_src)     # 產生 <base>.zip
    return os.path.exists(dest_zip)


def rotate_folders(out_dir, keep, dry):
    """只保留最近 keep 個日期資料夾（YYYYMMDD_HHMM），刪更舊的。"""
    folders = sorted(d for d in os.listdir(out_dir)
                     if STAMP_RE.match(d) and os.path.isdir(os.path.join(out_dir, d)))
    old = folders[:-keep] if keep > 0 else []
    if not old:
        print(f"  輪替：目前 {len(folders)} 個快照，未超過保留 {keep} 個，不刪。")
        return
    print(f"  輪替：保留最近 {keep} 個，刪除 {len(old)} 個舊快照：")
    for d in old:
        print(f"    - {d}")
        if not dry:
            try:
                shutil.rmtree(os.path.join(out_dir, d))
            except OSError as e:
                print(f"      （刪除失敗：{e}）")


def main():
    ap = argparse.ArgumentParser(description="twstock 備份：日期資料夾(dump + data.zip) + rclone 同步")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--out-dir", required=True, help="備份根目錄（各次快照建在其下，請放非 H 碟）")
    ap.add_argument("--data-src", default=r"H:\data", help=r"要壓縮的 CSV 來源（預設 H:\data）")
    ap.add_argument("--skip-data", action="store_true", help="略過 data.zip（只備份 DB）")
    ap.add_argument("--globals", action="store_true", help="順便 pg_dumpall --globals-only 備份角色/權限")
    ap.add_argument("--keep", type=int, default=8, help="保留最近幾個日期資料夾（預設 8；0=不刪）")
    ap.add_argument("--rclone-remote", default="",
                    help="rclone 目的地，如 gdrive:twstock_backup（給了才上傳雲端）")
    ap.add_argument("--rclone-sync", action="store_true",
                    help="用 rclone sync 鏡像整個 out-dir（雲端跟著輪替刪除）；預設是 copy 這次資料夾（不刪雲端）")
    ap.add_argument("--rclone-bin", default="", help="rclone.exe 路徑（不在 PATH 時指定）")
    ap.add_argument("--pg-bin", default="", help="PostgreSQL bin 目錄（pg_dump 不在 PATH 時指定）")
    ap.add_argument("--dry-run", action="store_true", help="只印會執行什麼，不實際跑")
    args = ap.parse_args()

    try:                                    # Windows cp950 console 印不出 ✓/✗ → 轉 UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if not args.dsn:
        raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
    d = parse_dsn(args.dsn)
    if not d["db"]:
        raise SystemExit("--dsn 未指定資料庫名稱")

    env = dict(os.environ)
    if d["password"]:
        env["PGPASSWORD"] = d["password"]               # 讓 pg_dump 免互動輸密碼

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    snap_dir = os.path.join(args.out_dir, stamp)        # 本次快照資料夾
    if not args.dry_run:
        os.makedirs(snap_dir, exist_ok=True)

    print(f"=== backup_db {datetime.now():%Y-%m-%d %H:%M:%S}"
          + ("（dry-run）" if args.dry_run else "") + f" ｜ 快照 {snap_dir} ===")
    fail = 0

    # 1) pg_dump → 快照資料夾
    dump_path = os.path.join(snap_dir, f"twstock_{stamp}.dump")
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
        gpath = os.path.join(snap_dir, f"globals_{stamp}.sql")
        print(f"\n[2] pg_dumpall --globals-only → {gpath}")
        rc = run([exe(args.pg_bin, "pg_dumpall"), "-U", d["user"], "-h", d["host"], "-p", d["port"],
                  "--globals-only", "-f", gpath], env, args.dry_run)
        if rc != 0:
            print(f"    ✗ 失敗 (rc={rc})（角色備份常需 postgres 超級使用者）"); fail += 1
        else:
            print("    ✓ 完成")

    # 3) data.zip（選配，預設做）
    if args.skip_data:
        print("\n[3] --skip-data：略過 data.zip")
    else:
        zpath = os.path.join(snap_dir, "data.zip")
        print(f"\n[3] 壓縮 {args.data_src} → {zpath}（大量 CSV，稍久）")
        try:
            ok = zip_data(args.data_src, zpath, args.dry_run)
            if ok and not args.dry_run:
                sz = os.path.getsize(zpath) / 1e9 if os.path.exists(zpath) else 0
                print(f"    ✓ 完成，{sz:.2f} GB")
            elif not ok:
                fail += 1
        except Exception as e:
            print(f"    ✗ 壓縮失敗：{e}"); fail += 1

    # 4) 輪替本機日期資料夾
    print(f"\n[4] 輪替舊快照")
    if not args.dry_run or os.path.isdir(args.out_dir):
        rotate_folders(args.out_dir, args.keep, args.dry_run)

    # 5) rclone 同步到雲端（選配）
    if args.rclone_remote:
        rclone = exe(args.rclone_bin, "rclone")
        if args.rclone_sync:
            print(f"\n[5] rclone sync（鏡像整個 out-dir）→ {args.rclone_remote}")
            cmd = [rclone, "sync", args.out_dir, args.rclone_remote, "--progress"]
        else:
            dest = f"{args.rclone_remote}/{stamp}"
            print(f"\n[5] rclone copy（本次快照）→ {dest}")
            cmd = [rclone, "copy", snap_dir, dest, "--progress"]
        rc = run(cmd, env, args.dry_run)
        if rc != 0:
            print(f"    ✗ rclone 失敗 (rc={rc})（確認已 rclone config 設好 remote）"); fail += 1
        else:
            print("    ✓ 雲端同步完成")
    else:
        print("\n[5] （未給 --rclone-remote，略過雲端同步）")

    print(f"\n=== {'dry-run 結束' if args.dry_run else '完成'}"
          + (f"，{fail} 個步驟失敗" if fail else "，全部成功") + " ===")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
