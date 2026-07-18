"""fetch_stock_codes.py — 抓台股代碼與中文名稱（普通股 / ETF），輸出 Excel。

來源：TWSE ISIN 國際證券辨識號碼清單
      上市 strMode=2、上櫃 strMode=4（同一份清單涵蓋普通股/權證/ETF/債券…）
分類：清單以「分類標題列」分段（如「ES 股票」「ETF」…）。
      --type stock → 只留普通股（CFICode 以 ES 開頭）
      --type etf   → 只留 ETF（分類標題含 ETF，或 CFICode 以 CE 開頭）
      --type all   → 兩者都要（多一欄「證券類別」= stock / etf）
輸出欄位：股票代碼、中文名稱、市場別、上市櫃日期、產業別、證券類別

需求：pip install pandas lxml openpyxl
用法：
  python fetch_stock_codes.py                              # 普通股 → 台股股票代碼NEW.xlsx（同舊行為）
  python fetch_stock_codes.py --type etf --out 台股ETF代碼.xlsx
  python fetch_stock_codes.py --type all --out 台股代碼含ETF.xlsx
  python fetch_stock_codes.py --type etf --codes-out etf_codes.txt   # 另存純代碼清單（供回補下載）
"""
import argparse
import io
import ssl
import urllib.request

import pandas as pd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"

# TWSE 憑證瑕疵 → 關掉 SSL 驗證（公開政府資料）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _classify(section, cfi):
    """依分類標題列 + CFICode 判斷證券類別。回傳 'stock' / 'etf' / ''（不要的）。"""
    if "ETF" in section or cfi.startswith("CE"):     # ETF 分類段，或 CFICode CE 開頭
        return "etf"
    if cfi.startswith("ES"):                         # 普通股
        return "stock"
    return ""


def fetch_isin(mode, want):
    """抓一份 ISIN 清單，回傳 DataFrame（依 want 篩選 stock/etf/all）。"""
    req = urllib.request.Request(ISIN_URL.format(mode=mode), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        html = r.read().decode("big5", errors="ignore")

    df = pd.read_html(io.StringIO(html))[0]
    df = df.iloc[1:]                                  # 第一列是欄名，丟掉
    # 欄位順序固定：0 代號及名稱 / 1 ISIN / 2 上市日 / 3 市場別 / 4 產業別 / 5 CFICode / 6 備註
    section, recs = "", []
    for _, row in df.iterrows():
        vals = [("" if pd.isna(x) else str(x)).strip() for x in row.tolist()]
        c0 = vals[0]
        isin = vals[1] if len(vals) > 1 else ""
        cfi = vals[5] if len(vals) > 5 else ""
        if not isin:                                  # 分類標題列（ISIN 欄空白）
            if c0:
                section = c0
            continue
        typ = _classify(section, cfi)
        if not typ or (want != "all" and typ != want):
            continue
        codename = c0.split("　", 1)                  # 全形空白分隔「代號　名稱」
        recs.append({
            "股票代碼": codename[0].strip(),
            "中文名稱": codename[1].strip() if len(codename) > 1 else "",
            "市場別": vals[3] if len(vals) > 3 else "",
            "上市櫃日期": vals[2] if len(vals) > 2 else "",
            "產業別": vals[4] if len(vals) > 4 else "",
            "證券類別": typ,
        })
    return pd.DataFrame(recs)


def main():
    ap = argparse.ArgumentParser(description="抓台股代碼與中文名稱（普通股/ETF）→ Excel")
    ap.add_argument("--type", choices=["stock", "etf", "all"], default="stock", help="要抓的證券類別")
    ap.add_argument("--out", default="", help="輸出 Excel 路徑（預設依 type 命名）")
    ap.add_argument("--codes-out", default="", help="另存純代碼清單 txt（每行一個，供回補下載餵入）")
    ap.add_argument("--include-otc", type=int, default=1, help="是否含上櫃(1/0)，預設 1")
    args = ap.parse_args()

    frames = [fetch_isin(2, args.type)]
    print(f"上市：{len(frames[0])} 檔")
    if args.include_otc:
        otc = fetch_isin(4, args.type)
        print(f"上櫃：{len(otc)} 檔")
        frames.append(otc)

    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates("股票代碼")
            .sort_values("股票代碼")
            .reset_index(drop=True))

    out = args.out or {"stock": "台股股票代碼NEW.xlsx", "etf": "台股ETF代碼.xlsx",
                       "all": "台股代碼含ETF.xlsx"}[args.type]
    df.to_excel(out, index=False)
    print(f"共 {len(df)} 檔（{args.type}）→ 已寫入 {out}")
    if args.codes_out:
        with open(args.codes_out, "w", encoding="utf-8") as f:
            f.write("\n".join(df["股票代碼"].tolist()))
        print(f"代碼清單 → {args.codes_out}")


if __name__ == "__main__":
    main()
