r"""fetch_fundamentals.py — 給股票代碼，抓 FinMind 各項「個股」資料集存成 CSV。

來源：FinMind 開放 API（https://api.finmindtrade.com/api/v4/data）
存放：<輸出根目錄>\<股號>\（預設 H:\data\Fundamentals），每個資料集一個 CSV：
   <股號>_revenue.csv        月營收（含 月增率% / 年增率%）
   <股號>_financials.csv     綜合損益表（EPS、毛利率%、營業利益率%）
   <股號>_balance.csv        資產負債表（算 ROE/PB/負債比/每股淨值用）
   <股號>_cashflow.csv       現金流量表（營運/自由現金流）
   <股號>_dividend.csv       股利政策（現金/股票股利、除息日、殖利率算料）
   <股號>_institutional.csv  三大法人買賣超（各法人「淨額」逐日）
   <股號>_margin.csv         融資融券餘額
   <股號>_per.csv            每日 PE / PB / 殖利率
   <股號>_shareholding.csv   外資持股比率 + 發行股數（算股本/市值用）
   <股號>_capreduction.csv   減資恢復買賣參考價（build_adjusted_price.py 拿來做減資還原）

Token：FinMind 免費註冊拿 token（https://finmindtrade.com）。免 token 額度極低、易被擋。
       設環境變數 FINMIND_TOKEN，或用 --token。
限流：預設「每小時最多 550 次請求」（滑動視窗；帳號額度 600/hr，留 50 次緩衝），達上限自動等
      視窗釋放，不會再撞 402。用 --max-per-hour 調整（付費層可調高；0=不限）。
      用量記在 finmind_rate_state.json，**跨行程共用**：nightly 連續起 dividend / capreduction
      等多支子行程時共用同一份額度，不會各自重新計數而合計超額。batch_/update_fundamentals 皆沿用。
注意：還原股價（TaiwanStockPriceAdj）是 FinMind 付費資料集，免費 token 抓不到，故不在此列。

需求：pip install pandas
用法：
  python fetch_fundamentals.py 2330                                   # 抓全部資料集
  python fetch_fundamentals.py 2330 --datasets institutional,per,balance
  python fetch_fundamentals.py 2330 2317 --start 2015-01-01 --token xxxxx
  python fetch_fundamentals.py 2330 --refresh --start 2026-01-01      # 重抓 2026 起（之前的舊列保留，不整檔覆蓋）
"""
import argparse
import collections
import contextlib
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date

import pandas as pd

API = "https://api.finmindtrade.com/api/v4/data"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# ---- 每小時請求限流（滑動視窗，跨行程共用）----
# 帳號額度 600/hr，預設只用 550 留緩衝（重試、其他手動腳本也會吃額度）。
MAX_PER_HOUR = int(os.environ.get("FINMIND_MAX_PER_HOUR") or 550)   # 每小時請求上限；0=不限流
_WINDOW = 3600.0
_REQ_TIMES = collections.deque()          # 記憶體鏡像；只有搶不到鎖時才單獨依賴它

# 用量落地成檔案，讓「連續啟動的多支子行程」共用同一份額度。
# nightly 會依序起 dividend、capreduction…，若各自在記憶體計數，每支都拿到完整 550 次 → 合計超額撞 402。
_STATE_FILE = os.environ.get("FINMIND_RATE_STATE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "finmind_rate_state.json")
_LOCK_FILE = _STATE_FILE + ".lock"


@contextlib.contextmanager
def _state_lock(timeout=30.0):
    """用 O_EXCL 建鎖檔互斥（Windows 也適用）。搶不到就 yield False 放行——
    寧可這幾次少算，也不要讓整批抓取卡死在鎖上。"""
    deadline = time.time() + timeout
    fd = None
    while fd is None:
        try:
            fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:                                        # 撿走殘留鎖（上次被 Ctrl-C 中斷）
                if time.time() - os.path.getmtime(_LOCK_FILE) > 60:
                    os.unlink(_LOCK_FILE)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                yield False
                return
            time.sleep(0.05)
        except OSError:                                 # 目錄唯讀等 → 退回記憶體計數
            yield False
            return
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
            os.unlink(_LOCK_FILE)


def _load_times(now):
    """讀出「還在一小時視窗內」的請求時間戳。檔案壞掉就當作空的重來。"""
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            times = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(times, list):
        return []
    return sorted(t for t in times if isinstance(t, (int, float)) and 0 <= now - t < _WINDOW)


def _save_times(times):
    tmp = f"{_STATE_FILE}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([round(t, 3) for t in times], f)
        os.replace(tmp, _STATE_FILE)                    # 原子寫入，避免讀到半截 JSON
    except OSError as e:
        print(f"    [限流] 用量檔寫入失敗（{e}），本行程改用記憶體計數", flush=True)
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _rate_limit():
    """全機每小時最多 MAX_PER_HOUR 次：達上限就睡到最舊那次請求滿 1 小時、視窗釋放為止。"""
    if not MAX_PER_HOUR:
        return
    while True:
        now = time.time()
        with _state_lock() as locked:
            if locked:
                times = _load_times(now)
            else:                                       # 搶不到鎖 → 退回本行程記憶體
                times = [t for t in _REQ_TIMES if 0 <= now - t < _WINDOW]
            if len(times) < MAX_PER_HOUR:
                times.append(now)
                _REQ_TIMES.clear()
                _REQ_TIMES.extend(times)
                if locked:
                    _save_times(times)
                return
            wait = _WINDOW - (now - times[0]) + 1
        # 鎖已釋放才睡，不然會擋住其他行程
        print(f"    [限流] 近一小時已用 {len(times)}/{MAX_PER_HOUR} 次，暫停 {wait:.0f}s 等視窗釋放…", flush=True)
        time.sleep(max(wait, 1.0))

# 與其他抓取器一致：對公開資料站放寬 SSL（避免憑證瑕疵造成失敗）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch(dataset, data_id, start, end, token, retries=4):
    """打 FinMind API，回傳 DataFrame（無資料/失敗回空 DataFrame）。"""
    params = {"dataset": dataset, "data_id": data_id, "start_date": start}
    if end:
        params["end_date"] = end
    if token:
        params["token"] = token
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            _rate_limit()                              # 每次請求前先過限流（含重試）
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                j = json.loads(r.read().decode("utf-8", errors="ignore"))
            if j.get("status") != 200:                 # 402/429：額度或限流
                print(f"    API status={j.get('status')} msg={j.get('msg', '')}")
                if j.get("status") in (402, 429):      # 限流 → 退避重試
                    time.sleep(10 * (attempt + 1))
                    continue
                return pd.DataFrame()
            return pd.DataFrame(j.get("data", []))
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"    重試 {attempt + 1}/{retries}（{e}）等 {wait}s")
            time.sleep(wait)
    return pd.DataFrame()


# ---- 各資料集的整形函式 --------------------------------------------------

def build_revenue(df):
    """月營收：依日期排序，加 月增率% / 年增率%（同月比去年）。"""
    if df.empty or "revenue" not in df.columns:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
    df["月增率(%)"] = (df["revenue"].pct_change() * 100).round(2)
    df["年增率(%)"] = (df["revenue"].pct_change(12) * 100).round(2)   # 月資料連續時=去年同月
    return df


def pivot_long(df):
    """type/value 長格式（財報家族）→ wide（一期一列）。"""
    if df.empty or "type" not in df.columns:
        return df
    wide = (df.pivot_table(index="date", columns="type", values="value", aggfunc="first")
              .reset_index())
    wide.columns.name = None
    return wide.sort_values("date").reset_index(drop=True)


def build_financials(df):
    """綜合損益表：pivot 後加 毛利率% / 營業利益率%。"""
    wide = pivot_long(df)
    if wide.empty or "Revenue" not in wide.columns:
        return wide
    rev = pd.to_numeric(wide["Revenue"], errors="coerce")
    if "GrossProfit" in wide.columns:
        wide["毛利率(%)"] = (pd.to_numeric(wide["GrossProfit"], errors="coerce") / rev * 100).round(2)
    if "OperatingIncome" in wide.columns:
        wide["營業利益率(%)"] = (pd.to_numeric(wide["OperatingIncome"], errors="coerce") / rev * 100).round(2)
    return wide


def net_institutional(df):
    """三大法人（每列一種投資人）→ 每日一列，各法人淨額(買-賣)。"""
    if df.empty or "name" not in df.columns:
        return df
    df = df.copy()
    df["buy"] = pd.to_numeric(df["buy"], errors="coerce")
    df["sell"] = pd.to_numeric(df["sell"], errors="coerce")
    df["net"] = df["buy"] - df["sell"]
    wide = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum")
    wide.columns = [f"{c}_net" for c in wide.columns]
    wide = wide.reset_index()
    wide.columns.name = None
    return wide.sort_values("date").reset_index(drop=True)


# key -> (FinMind dataset, 整形函式 或 None, 中文說明)
DATASETS = {
    "revenue":       ("TaiwanStockMonthRevenue", build_revenue, "月營收"),
    "financials":    ("TaiwanStockFinancialStatements", build_financials, "綜合損益表(EPS/毛利)"),
    "balance":       ("TaiwanStockBalanceSheet", pivot_long, "資產負債表"),
    "cashflow":      ("TaiwanStockCashFlowsStatement", pivot_long, "現金流量表"),
    "dividend":      ("TaiwanStockDividend", None, "股利政策"),
    "institutional": ("TaiwanStockInstitutionalInvestorsBuySell", net_institutional, "三大法人買賣超"),
    "margin":        ("TaiwanStockMarginPurchaseShortSale", None, "融資融券"),
    "per":           ("TaiwanStockPER", None, "PE/PB/殖利率"),
    "shareholding":  ("TaiwanStockShareholding", None, "外資持股+發行股數"),
    "capreduction":  ("TaiwanStockCapitalReductionReferencePrice", None, "減資參考價(還原用)"),
}


def merge_existing(path, new, start):
    """--refresh 只重抓 start 之後：保留舊檔 start 之前的列，再接上新抓的，不要整檔覆蓋。
    （踩過的雷：股利旺季每週 --refresh --start 今年1/1，1,511 檔 dividend.csv 只剩今年；季報也只剩近 6 季。
     DB 表是 upsert 不刪，歷史還在，但 CSV 沒了。）"""
    try:
        old = pd.read_csv(path, dtype=str)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return new
    if "date" not in old.columns or "date" not in new.columns:
        return new
    keep = old[old["date"].astype(str).str[:10] < str(start)[:10]]
    if keep.empty:
        return new
    out = pd.concat([keep, new], ignore_index=True, sort=False)
    return out.iloc[out["date"].astype(str).argsort(kind="stable")].reset_index(drop=True)


def download_one(code, keys, start, end, out_root, token, delay, refresh=False):
    """抓指定股票的多個資料集；每個資料集存一個 CSV，已存在(非 refresh)則跳過。
    refresh：重抓 start~end 並與舊檔合併（start 之前的舊列保留）。"""
    folder = os.path.join(out_root, code)
    os.makedirs(folder, exist_ok=True)
    for key in keys:
        dataset, transform, desc = DATASETS[key]
        path = os.path.join(folder, f"{code}_{key}.csv")
        if not refresh and os.path.exists(path):        # 資料集層級可續抓
            print(f"  [跳過] {code} {desc}")
            continue
        df = fetch(dataset, code, start, end, token)
        if transform is not None:
            df = transform(df)
        if df is not None and not df.empty:
            n_new = len(df)
            if refresh and os.path.exists(path):
                df = merge_existing(path, df, start)
            df.to_csv(path, index=False, encoding="utf-8-sig")
            print(f"  [存]   {code} {desc} {n_new} 列" + (f"（合併舊檔共 {len(df)} 列）" if len(df) != n_new else "")
                  + f" → {code}_{key}.csv")
        else:
            print(f"  [無]   {code} {desc} 無資料")
        time.sleep(delay)                               # 只有真的打了 API 才等


def resolve_keys(spec):
    if spec == "all":
        return list(DATASETS)
    keys, bad = [], []
    for k in spec.split(","):
        k = k.strip()
        if not k:
            continue
        (keys if k in DATASETS else bad).append(k)
    if bad:
        raise SystemExit(f"未知資料集 {bad}；可選：all,{','.join(DATASETS)}")
    return keys


def main():
    ap = argparse.ArgumentParser(description="抓 FinMind 個股資料集（基本面/籌碼/估值）存 CSV")
    ap.add_argument("stocks", nargs="+", help="股票代碼（可多個）")
    ap.add_argument("--datasets", default="all",
                    help="要抓的資料集，逗號分隔；all=全部。可選：" + ",".join(DATASETS))
    ap.add_argument("--start", default="2010-01-01", help="起始日 YYYY-MM-DD（預設 2010-01-01）")
    ap.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out", default=r"H:\data\Fundamentals", help=r"輸出根目錄（預設 H:\data\Fundamentals）")
    ap.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""),
                    help="FinMind token（或設環境變數 FINMIND_TOKEN）")
    ap.add_argument("--delay", type=float, default=2.0, help="每請求間隔秒數（預設 2）")
    ap.add_argument("--max-per-hour", type=int, default=int(os.environ.get("FINMIND_MAX_PER_HOUR") or 550),
                    help="每小時請求上限（滑動視窗、跨行程共用；預設讀環境變數 FINMIND_MAX_PER_HOUR 或 550，0=不限）")
    ap.add_argument("--refresh", action="store_true", help="重抓已存在的 CSV（只換掉 --start 之後的列，之前的舊列保留）")
    args = ap.parse_args()

    global MAX_PER_HOUR
    MAX_PER_HOUR = args.max_per_hour

    keys = resolve_keys(args.datasets)
    end = args.end or date.today().isoformat()
    if not args.token:
        print("⚠️ 未提供 FinMind token（--token 或環境變數 FINMIND_TOKEN）；免 token 額度很低、可能被擋。")
    print(f"輸出根目錄：{args.out}｜範圍 {args.start} ~ {end}｜資料集 {keys}｜共 {len(args.stocks)} 檔")
    for s in args.stocks:
        print(f"\n=== {s} ===")
        download_one(s, keys, args.start, end, args.out, args.token, args.delay, args.refresh)
    print("\n全部完成。")


if __name__ == "__main__":
    main()
