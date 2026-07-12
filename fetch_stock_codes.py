"""fetch_stock_codes.py — 抓所有台股「普通股」的代碼與中文名稱，輸出 Excel。

來源：TWSE ISIN 國際證券辨識號碼清單
      上市 strMode=2、上櫃 strMode=4（同一份清單也涵蓋權證/ETF/債券…）
篩選：CFICode 以 'ES' 開頭 = 普通股（自動排除權證、ETF、特別股、債券等）
輸出欄位：股票代碼、中文名稱、市場別、上市櫃日期、產業別

需求：pip install pandas lxml openpyxl
用法：
  python fetch_stock_codes.py                          # 上市+上櫃 → 台股股票代碼.xlsx
  python fetch_stock_codes.py --out "H:\\data\\台股代碼.xlsx"
  python fetch_stock_codes.py --include-otc 0          # 只要上市
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


def fetch_isin(mode):
    """抓一份 ISIN 清單並回傳 DataFrame[股票代碼, 中文名稱, 市場別]（只含普通股）。"""
    req = urllib.request.Request(ISIN_URL.format(mode=mode), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        html = r.read().decode("big5", errors="ignore")

    df = pd.read_html(io.StringIO(html))[0]
    df = df.iloc[1:]                                  # 第一列是欄名，丟掉
    # 欄位順序固定：0 代號及名稱 / 1 ISIN / 2 上市日 / 3 市場別 / 4 產業別 / 5 CFICode / 6 備註
    cfi = df.iloc[:, 5].astype(str)
    df = df[cfi.str.startswith("ES")]                 # 只留普通股（排除權證/ETF/債…）

    codename = df.iloc[:, 0].astype(str).str.split("　", n=1, expand=True)  # 全形空白分隔
    out = pd.DataFrame({
        "股票代碼": codename[0].str.strip(),
        "中文名稱": codename[1].str.strip() if codename.shape[1] > 1 else "",
        "市場別": df.iloc[:, 3].astype(str).str.strip(),
        "上市櫃日期": df.iloc[:, 2].astype(str).str.strip().replace("nan", ""),  # ISIN 第2欄=上市/上櫃日(西元)
        "產業別": df.iloc[:, 4].astype(str).str.strip().replace("nan", ""),      # ISIN 第4欄=產業別
    })
    return out.dropna(subset=["股票代碼"])


def main():
    ap = argparse.ArgumentParser(description="抓台股普通股代碼與中文名稱 → Excel")
    ap.add_argument("--out", default="台股股票代碼NEW.xlsx", help="輸出 Excel 路徑")
    ap.add_argument("--include-otc", type=int, default=1, help="是否含上櫃(1/0)，預設 1")
    args = ap.parse_args()

    frames = [fetch_isin(2)]
    print(f"上市：{len(frames[0])} 檔")
    if args.include_otc:
        otc = fetch_isin(4)
        print(f"上櫃：{len(otc)} 檔")
        frames.append(otc)

    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates("股票代碼")
            .sort_values("股票代碼")
            .reset_index(drop=True))
    df.to_excel(args.out, index=False)
    print(f"共 {len(df)} 檔 → 已寫入 {args.out}")


if __name__ == "__main__":
    main()
