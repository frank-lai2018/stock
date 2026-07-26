# twstock 備份說明（backup.md）

一鍵備份腳本 [`backup_db.py`](backup_db.py)：每次備份建立一個「日期時間資料夾」，放入
**資料庫 dump + data.zip**，再用 **rclone 同步到 Google Drive**。

---

## 一、備份內容與結構

每跑一次，會在 `--out-dir` 底下建一個 `YYYYMMDD_HHMM` 快照資料夾：

```
E:\backup\
  20260726_1430\
    twstock_20260726_1430.dump   ← pg_dump -Fc（PostgreSQL 邏輯備份，custom 壓縮格式）
    globals_20260726_1430.sql    ← （--globals）角色 / 權限
    data.zip                     ← H:\data 整包壓縮（所有原始 / 還原價 CSV）
  20260726_0900\
    ...
```

- **DB**：`pg_dump -Fc`（含結構 + 資料，單一壓縮檔）。
- **data**：`H:\data` 是行情 / 基本面 CSV 的終極來源，整包壓成單一 `data.zip`
  （雲端同步 1 個檔遠快於數萬個小檔，也省空間）。
- **globals**（選配）：`pg_dumpall --globals-only`，備份角色 / 權限（需 postgres 超級使用者）。

---

## 二、前置：安裝並設定 rclone（只需做一次）

### 1. 安裝 rclone
1. 到 <https://rclone.org/downloads/> → **Windows / Intel-AMD 64 Bit** 下載 zip。
2. 解壓縮，取出 `rclone.exe`，放到固定資料夾，例如 `C:\rclone\rclone.exe`。
3. 兩種用法擇一：
   - **不設 PATH（推薦）**：備份指令加 `--rclone-bin C:\rclone`（給資料夾，不含 exe）。
   - **設 PATH**：Win→「編輯系統環境變數」→ 環境變數 → 系統變數 `Path` → 新增 `C:\rclone`
     → 確定 → 重開終端機 → `rclone version` 有版本號即成功。

### 2. 設定 Google Drive 連線（`rclone config`）
執行 `C:\rclone\rclone.exe config`（或已設 PATH 則 `rclone config`），依序：

| 提問 | 輸入 |
|---|---|
| `n/s/q>` | **`n`**（New remote） |
| `name>` | **`gdrive`** |
| `Storage>` | 找 **Google Drive**（輸入其編號，或直接打 `drive`） |
| `client_id>` | **Enter**（留空，用內建金鑰即可） |
| `client_secret>` | **Enter**（留空） |
| `scope>` | **`1`**（Full access 完整讀寫） |
| `service_account_file>` | **Enter**（留空，用互動登入） |
| `Edit advanced config?` | **`n`** |
| `Use web browser to automatically authenticate?` | **`y`** → 開瀏覽器登入 Google、按「允許」，看到 `Got code` 即授權成功 |
| `Configure this as a Shared Drive (Team Drive)?` | **`n`**（個人雲端硬碟） |
| `y/e/d>`（設定摘要確認） | **`y`** |
| 回主選單 | **`q`**（離開） |

### 3. 驗證
```
rclone lsd gdrive:
```
能列出雲端硬碟的資料夾 → 設定完成。

> 備註：留空的 client_id 是共用金鑰，尖峰可能被限速（不影響能否使用）。
> 嫌慢再依 rclone 文件申請自己的 client_id。

---

## 三、執行備份

> DSN 密碼請勿寫進版控檔。可改設環境變數 `DATABASE_URL` 後省略 `--dsn`。

```bat
python backup_db.py --dsn "postgresql://frank:密碼@localhost:5432/twstock" ^
  --out-dir E:\backup --globals ^
  --rclone-remote gdrive:twstock_backup --rclone-bin C:\rclone
```

常用參數：

| 參數 | 說明 |
|---|---|
| `--out-dir` | 備份根目錄（各快照建在其下，請放**非 H 碟**） |
| `--globals` | 順便備份角色 / 權限 |
| `--rclone-remote gdrive:twstock_backup` | rclone 目的地；給了才上傳雲端 |
| `--rclone-sync` | 改成 `rclone sync` 鏡像整個 out-dir → **雲端跟著本機輪替刪除**（雲端＝本機）。<br>預設不加＝`rclone copy` 只上傳本次快照（附加，不刪雲端） |
| `--rclone-bin C:\rclone` | rclone.exe 所在資料夾（未設 PATH 時用） |
| `--keep 8` | 本機保留最近 8 個快照，刪更舊的（0＝不刪） |
| `--skip-data` | 只備 DB，略過 data.zip |
| `--data-src H:\data` | data 來源（預設 `H:\data`） |
| `--pg-bin` | pg_dump 所在 bin 目錄（不在 PATH 時） |
| `--dry-run` | 只印會做什麼，不實際執行 |

**copy vs sync**：
- 預設（copy）：雲端只增不減，最安全；本機 `--keep` 只清本機。
- `--rclone-sync`：雲端完全鏡像本機，本機輪替刪掉的雲端也會刪，長期不佔雲端空間。

---

## 四、排程（Task Scheduler）

備份通常一週一次，建議與每日更新分開，另建一個 `run_backup.bat`（內含 DSN，**加入 .gitignore**）：

```bat
@echo off
set PYTHONIOENCODING=utf-8
cd /d C:\Users\apple\Desktop\work_suggest\stock_screener_starter
python backup_db.py --dsn "postgresql://frank:密碼@localhost:5432/twstock" ^
  --out-dir E:\backup --globals --rclone-remote gdrive:twstock_backup --rclone-bin C:\rclone ^
  >> E:\backup\backup.log 2>&1
```
Task Scheduler → 建立基本工作 → 每週 → 指到此 bat。

---

## 五、還原（Restore）

### 1.（雲端）先把快照抓回本機
```
rclone copy gdrive:twstock_backup/20260726_1430 E:\restore\20260726_1430 --progress
```

### 2. 還原資料庫
```bat
:: 還原到既有 twstock（--clean 會先清掉同名物件）
pg_restore -U frank -h localhost -d twstock -Fc --clean --if-exists ^
  E:\restore\20260726_1430\twstock_20260726_1430.dump

:: 或先建全新資料庫再還原
:: createdb -U frank twstock_new
:: pg_restore -U frank -d twstock_new -Fc E:\restore\...\twstock_....dump
```

### 3.（選配）還原角色 / 權限
```
psql -U postgres -f E:\restore\20260726_1430\globals_20260726_1430.sql
```

### 4. 還原 data
把 `data.zip` 解壓縮回 `H:\data`（覆蓋 / 放回原位）。

---

## 六、注意事項

- `--out-dir` 請放**非 H 碟**（H 碟壞了才有意義）。
- DSN / bat 內含密碼，**不要進版控**（已在 .gitignore 列 `run_nightly.bat`，`run_backup.bat` 也請加）。
- `data.zip` 每次全量壓縮，資料大時較耗時；週備份可接受。
- rclone 首次上傳大檔較久，之後 rclone 會依大小 / 時間增量比對，只傳新增 / 變動。
- 還原前務必確認要覆蓋的目標（DB / H:\data）正確，`--clean` 會刪除同名物件。
