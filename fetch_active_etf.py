r"""fetch_active_etf.py — 抓主動式 ETF 每日持股（各投信官網公告）→ etf_snapshot／etf_holding，接著算進出 → etf_flow。

來源（各投信官網；2026-09 實測，都能指定日期，可回補上市以來的歷史）：
  uni      統一 ezmoney 申購買回清單 Excel：/ETF/Transaction/PCFExcelNPOI?fundCode=49YTW&date=115/09/29&specificDate=true
           date 是「清單日」（民國年），檔內持股是前一交易日收盤；最新的清單日取 PCF 頁面的預設值。
  capital  群益 CFWeb JSON：POST /CFWeb/api/etf/buyback {"fundId":399,"date":"2026-09-29"}；不帶 date＝最新。
           pcf.date1＝清單日、pcf.date2＝持股日。
  fh       復華 Excel：/api/assetsExcel/ETF23/YYYYMMDD，日期就是持股日；非交易日或還沒公布回「查無資料」。
  ── 第二階段（2026-09-28）──
  nomura   野村 JSON：POST www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundTradeInfo
           {"Type":1,"Keyword":"","FundNo":"00980A","Date":"2026/09/29"}：Date＝清單日，CNavDt＝持股日；
           最新清單日取 Fund/GetFundTradeInfoDate 的 LatestDate。沒資料回 Entries=null。
  ctbc     中信 JSON：先 POST www.ctbcinvestments.com.tw/API/home/AuthToken 拿 token，再 POST etf/ETFHoldingWeight
           {"FID":"E0038","StartDate":"2026-09-24"}（投資組合）：StartDate＝持股日，但伺服器回「≤ 該日的最近一天」，
           所以要核對「資料日期」。
  cathay   國泰 Excel：GET cwapi.cathaysite.com.tw/api/ETF/DownloadETFWeightExcel?FundCode=EA&SearchDate=2026-09-24，
           日期＝持股日；沒資料回空白內容。要完整 Chrome UA（Akamai 擋 python-requests）。
  fubon    富邦 HTML：GET websys.fsit.com.tw/FubonETF/Trade/Assets.aspx?stkId=00405A&ddate=2026/09/24&lan=TW，
           ddate＝持股日；伺服器回「≤ ddate 的最近一天」，要核對「資料日期」。robots.txt 只開放 /FubonETF。
  kgi      凱基 HTML 片段：POST www.kgifund.com.tw/Fund/RedemptionVC（fundID=J024&queryDate=2026/09/29）：
           queryDate＝清單日，括號裡的 (2026/09/24) 是持股日；沒資料回 HTTP 500（不重試）；日期格式不對會默默回最新一份。
  ── 第三階段（2026-09-28）──
  allianz  安聯 JSON：先 GET etf.allianzgi.com.tw/webapi/api/AntiForgery/GetAntiForgeryToken 拿 X-XSRF-TOKEN cookie，再
           POST Fund/GetFundTradeInfo {"FundNo":"E0001","Date":"2026-09-29"}（header 帶 X-XSRF-TOKEN，沒帶回 HTTP 400）：
           Date＝清單日（只收 yyyy-mm-dd），CNavDt＝持股日；欄位跟野村同一套，持股在 DynamicTableData。沒資料回 Entries=null。
  fsitc    第一金 JSON：POST www.fsitc.com.tw/WebAPI.aspx/Get_BuySellA（淨值、單位數）與 Get_hd（持股），
           {"pStrFundID":"182","pStrDate":"2026/09/29"}：日期＝清單日，Get_hd 的 sdate＝持股日。回應是 {"d":"<JSON 字串>"}，
           沒資料時 d 是空字串；一定要 Content-Type: application/json（表單格式會回 HTML 錯誤頁）。
  mega     兆豐 HTML：POST www.megafunds.com.tw/MEGA/etf/trade_pcf.aspx（ASP.NET 表單 fund_id=23、qdt=2026/09/29）：
           qdt＝清單日，'2026/09/24 每基數實際申購總價金(元)' 的日期是持股日；沒資料回空白樣板；日期格式不對會默默回最新一份。
  taishin  台新 HTML：GET www.tsit.com.tw/ETF/Home/Pcf/00987A?DataDate=2026-09-29：DataDate＝清單日。假日或還沒公布時
           會回最新一份、卻照抄請求日期，要用隱藏欄位 MAX_DATE（最新清單日）和標籤上的持股日識破。
  jpm      摩根 Excel：GET am.jpmorgan.com/FundsMarketingHandler/excel?type=m12_pcf&cusip=TW00000401A1&country=tw
           &role=twetf&locale=zh-TW&date=2026-09-29：date＝清單日；沒資料回 HTTP 404。最新清單日取 product-data 的
           fundData.breakdowns.m12Details.effectiveDate。**只保留約 31 天**，更早的歷史拿不到，要靠每晚累積。
  聯博 00404A 不抓：資料只在 webapi.alliancebernstein.com，它的 robots.txt 全站禁止。
  野村、中信、凱基、第一金的憑證鏈在 Python 3.13 的嚴格檢查下會失敗（TWCA 根憑證缺 Subject Key Identifier），
  改用 Windows 系統的憑證驗證（truststore）；驗證沒有放寬，瀏覽器、curl 走的也是這套。
各家約傍晚到晚上陸續公布（台新是清單日當天早上，所以晚一天拿到）。run_nightly.bat（晚上 9 點後執行）在 nightly 跑完後
會再補抓一次（見 主動ETF追蹤設計.md）。

防呆：
  - 入庫用「檔案內容」解析出的持股日，不信任請求日期；同一 (ETF, 持股日) 重抓整批覆蓋（冪等）。
  - 每次都回頭檢查近 --gap-days 個交易日，缺哪天就用日期參數補哪天（漏抓自動補）。
  - 原始檔存 H:\data\ETF_HOLD\<ETF>\<持股日>.xlsx|json，解析規則改了可以重跑。
  - 同一家投信的請求依序送、每次間隔 --delay 秒；不同投信並行。

用法：
  python fetch_active_etf.py                         # 每晚：抓最新＋補近 10 個交易日的缺口 → 算進出
  python fetch_active_etf.py --backfill              # 回補上市以來全部歷史（約 3,500 個請求，各家並行約 30 分鐘；群益最慢）
  python fetch_active_etf.py --backfill --since 2026-09-01 --etf 00981A
  python fetch_active_etf.py --dry-run --etf 00991A  # 只抓最新、解析、印摘要，不寫 DB
  python fetch_active_etf.py --no-flow               # 只抓持股，不算進出
"""
import argparse
import io
import json
import os
import re
import sys
import threading
import time
import warnings
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

from active_etf_defs import FUNDS

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")          # 短 UA 會被部分投信的 WAF 擋
RAW_ROOT = r"H:\data\ETF_HOLD"

UNI_HOME = "https://www.ezmoney.com.tw/"
UNI_PAGE = "https://www.ezmoney.com.tw/ETF/Transaction/PCF?FundCode={code}"
UNI_XLSX = "https://www.ezmoney.com.tw/ETF/Transaction/PCFExcelNPOI?fundCode={code}&date={roc}&specificDate=true"
CAP_API = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"
CAP_PAGE = "https://www.capitalfund.com.tw/etf/product/detail/{code}/portfolio"
FH_XLSX = "https://www.fhtrust.com.tw/api/assetsExcel/{code}/{ymd}"
FH_PAGE = "https://www.fhtrust.com.tw/ETF/etf_detail/{code}"
NOMURA_HOST = "https://www.nomurafunds.com.tw"
NOMURA_API = NOMURA_HOST + "/API/ETFAPI/api/"
NOMURA_PAGE = NOMURA_HOST + "/ETFWEB/product-description?fundNo={code}"
CTBC_SITE = "https://www.ctbcinvestments.com"
CTBC_API = "https://www.ctbcinvestments.com.tw/API/"
CTBC_BOOT = "www.ctbcinvestments.com"           # 網頁啟動時拿 token 用的固定字串
CTBC_PAGE = CTBC_SITE + "/Etf/{etf}/Combination"
CATHAY_API = "https://cwapi.cathaysite.com.tw/api/"
CATHAY_PAGE = "https://www.cathaysite.com.tw/ETF/detail/E{code}"
FUBON_ASSETS = "https://websys.fsit.com.tw/FubonETF/Trade/Assets.aspx"
FUBON_PAGE = "https://websys.fsit.com.tw/FubonETF/Trade/Pcf.aspx?stkId={code}&lan=TW"
KGI_VC = "https://www.kgifund.com.tw/Fund/RedemptionVC"
KGI_PAGE = "https://www.kgifund.com.tw/Fund/RedemptionList"
ALLIANZ_SITE = "https://etf.allianzgi.com.tw"
ALLIANZ_API = ALLIANZ_SITE + "/webapi/api/"
FSITC_HOST = "https://www.fsitc.com.tw"
FSITC_API = FSITC_HOST + "/WebAPI.aspx/"
FSITC_PAGE = FSITC_HOST + "/FundDetail.aspx?ID={code}"
MEGA_HOST = "https://www.megafunds.com.tw"
MEGA_PCF = MEGA_HOST + "/MEGA/etf/trade_pcf.aspx"
TAISHIN_PCF = "https://www.tsit.com.tw/ETF/Home/Pcf/{etf}"
JPM_HANDLER = "https://am.jpmorgan.com/FundsMarketingHandler/"
JPM_PAGE = "https://am.jpmorgan.com/tw/zh/asset-management/twetf/"

Fund = namedtuple("Fund", "etf_id issuer adapter code name")
_print_lock = threading.Lock()
# 投信的 Excel 常缺預設樣式，openpyxl 每個檔都會警告；多執行緒下 catch_warnings 不可靠，直接在模組層級過濾
warnings.filterwarnings("ignore", message="Workbook contains no default style", category=UserWarning)


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def clean_dsn(dsn):
    """容錯＋防呆：剝掉誤貼進值裡的旗標／引號，並先擋掉明顯不合法的連線字串。（同 nightly.py）"""
    dsn = (dsn or "").strip().strip('"').strip("'").strip()
    if dsn.startswith("--dsn"):                      # 誤把旗標本身貼進值裡
        dsn = dsn[5:].lstrip().lstrip("=").lstrip()
    if dsn and not (dsn.startswith(("postgresql://", "postgres://")) or "=" in dsn):
        raise SystemExit(f"連線字串格式不對：{dsn!r}；"
                         "應為 postgresql://user:pw@host:port/db（或 key=value 形式）")
    return dsn


# ---------- 解析共用 ----------

def _num(v):
    """'NTD 1,234.5' / '9.77%' / '-20,000,000' / 12.3 → float；無數字回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(t) if t not in ("", "-", ".", "-.") else None
    except ValueError:
        return None


def _roc(s):
    """'115/08/03' → date(2026, 8, 3)。"""
    m = re.search(r"(?<!\d)(\d{2,3})/(\d{1,2})/(\d{1,2})", str(s or ""))
    return date(int(m[1]) + 1911, int(m[2]), int(m[3])) if m else None


def _ad(s):
    """'2026/08/03'、'2026-09-24'、'2026/9/29 上午 12:00:00' → date。"""
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", str(s or ""))
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _acct(v):
    """會計格式的負數：'(3,500,000)' → -3500000.0（台新）；其他同 _num。"""
    x = _num(v)
    return -abs(x) if x is not None and re.search(r"\(\s*[\d,.]+\s*\)", str(v)) else x


def _xlsx_sheets(content, first_only=False):
    """工作表 → [(表名, 列)]。摩根一個檔有 5 張表，其他家只看第一張。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        out = []
        for ws in wb.worksheets[:1] if first_only else wb.worksheets:
            ws.reset_dimensions()       # 國泰、凱基的檔宣告範圍只有 A1、摩根宣告 A1:ALL19，唯讀模式要重算
            out.append((ws.title, [["" if c is None else str(c).strip() for c in r] for r in ws.iter_rows(values_only=True)]))
        return out
    finally:
        wb.close()


def _xlsx_rows(content):
    return _xlsx_sheets(content, first_only=True)[0][1]


def _col(idx, *keys):
    for name, i in idx.items():
        if any(k in name for k in keys):
            return i
    return None


def _cell(r, i):
    return r[i] if i is not None and i < len(r) else ""


CODE_RE = re.compile(r"[0-9A-Z]{4,6}")
FUT_RE = re.compile(r"[0-9A-Z]{2,6}")


def _parse_tables(rows):
    """在工作表（或 HTML 表格轉成的列）裡找「股票」（表頭含 代號/代碼＋股數）與「期貨」（代號/代碼＋口數）明細表。
    欄位用表頭文字定位，不寫死欄號；表頭之後遇到不像代號的列即結束該表。選擇權表（有 履約價／買賣權）不收。"""
    stocks, futures, cur = [], [], None
    for r in rows:
        idx = {v: i for i, v in enumerate(r) if v}
        names = " ".join(idx)
        if ("代號" in names or "代碼" in names) and ("股數" in names or "口數" in names):
            if "履約價" in names or "買賣權" in names:
                cur = None                                # 選擇權（國泰、中信賣買權）：不是持股
            elif "口數" in names:
                cur = ("futures", _col(idx, "代號", "代碼"), _col(idx, "名稱"), _col(idx, "口數"),
                       _col(idx, "權重"), _col(idx, "年月", "月份"))
            else:
                cur = ("stock", _col(idx, "代號", "代碼"), _col(idx, "名稱"), _col(idx, "股數"),
                       _col(idx, "權重"), _col(idx, "金額", "市值"))
            continue
        if not cur:
            continue
        kind, ci, ni, qi, wi, xi = cur
        code = _cell(r, ci).upper()
        if not (CODE_RE if kind == "stock" else FUT_RE).fullmatch(code):
            cur = None                                    # 表格結束
            continue
        qty = _num(_cell(r, qi))
        if qty is None:
            continue
        if kind == "stock":
            stocks.append((code, _cell(r, ni), qty, _num(_cell(r, wi)), _num(_cell(r, xi))))
        else:
            month = re.sub(r"\D", "", _cell(r, xi))
            futures.append((code + month, (_cell(r, ni) + " " + _cell(r, xi)).strip(), qty, _num(_cell(r, wi))))
    return stocks, futures


def _valid(snap):
    return snap if snap.get("as_of") and snap.get("stocks") else None


def parse_uni(content):
    """統一申購買回清單：表頭幾列是 淨資產／已發行單位數／與前日差異／'115/08/03 每受益權單位淨資產價值'。"""
    rows = _xlsx_rows(content)
    snap = {}
    for r in rows:
        cells = [c for c in r if c]
        if not cells:
            continue
        c0, v = cells[0], (cells[1] if len(cells) > 1 else None)
        if "申購買回清單" in c0 and "list_date" not in snap:
            snap["list_date"] = _roc(c0)
        elif "基金淨資產價值" in c0:
            snap["nav_total"] = _num(v)
        elif "已發行受益權單位總數" in c0:
            snap["units"] = _num(v)
        elif "與前日已發行單位差異數" in c0:
            snap["units_chg"] = _num(v)
        elif "每受益權單位淨資產價值" in c0 and "每現金" not in c0:
            snap["as_of"], snap["nav_unit"] = _roc(c0), _num(v)
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def parse_fh(content):
    """復華：'日期: 2026/08/03'；淨值／單位數的數字在標籤的下一列。"""
    rows = _xlsx_rows(content)
    snap, pending = {}, None
    labels = {"基金資產淨值": "nav_total", "流通單位數": "units", "每單位淨值": "nav_unit"}
    for r in rows:
        cells = [c for c in r if c]
        if not cells:
            continue
        if pending:
            snap[pending] = _num(cells[0])
            pending = None
            continue
        c0 = cells[0]
        if c0.startswith("日期"):
            snap["as_of"] = _ad(c0)
            continue
        for key, field in labels.items():
            if key in c0:
                if len(cells) > 1:
                    snap[field] = _num(cells[1])
                else:
                    pending = field
                break
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def parse_capital(obj):
    """群益 JSON：data.pcf（date1 清單日、date2 持股日、nav、totUnit、disUnit、pUnit）、data.stocks、data.futures。"""
    data = (obj or {}).get("data") or {}
    pcf = data.get("pcf") or {}
    snap = {"list_date": _ad(pcf.get("date1")), "as_of": _ad(pcf.get("date2")),
            "nav_total": _num(pcf.get("nav")), "units": _num(pcf.get("totUnit")),
            "units_chg": _num(pcf.get("disUnit")), "nav_unit": _num(pcf.get("pUnit"))}
    snap["stocks"] = [(str(s.get("stocNo") or "").strip().upper(), s.get("stocName"), _num(s.get("share")),
                       _num(s.get("weight")), None)
                      for s in data.get("stocks") or [] if s.get("stocNo") and _num(s.get("share")) is not None]
    futures = []
    for f in data.get("futures") or []:
        code = f.get("futuNo") or f.get("futNo") or f.get("stocNo") or f.get("code")
        qty = _num(f.get("lots") or f.get("qty") or f.get("share") or f.get("contracts"))
        if not code or qty is None:
            log(f"    （群益期貨欄位看不懂，略過：{sorted(f)}）")
            continue
        futures.append((str(code).strip(), f.get("futuName") or f.get("stocName") or "", qty, _num(f.get("weight"))))
    snap["futures"] = futures
    return _valid(snap)


def _html_rows(content):
    """HTML → (表格列, 文字行)。表格列跟 Excel 的列一樣交給 _parse_tables；文字行用來找「標籤 → 下一個元素的值」。"""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.rows, self.lines, self.row, self.cell, self.skip = [], [], None, None, 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip += 1
            elif tag == "tr":
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell = []

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self.skip = max(0, self.skip - 1)
            elif tag in ("td", "th") and self.cell is not None:
                self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None

        def handle_data(self, data):
            if self.skip:
                return
            if self.cell is not None:
                self.cell.append(data)
            t = " ".join(data.split())
            if t:
                self.lines.append(t)

    p = _P()
    p.feed(content.decode("utf-8", "replace") if isinstance(content, bytes) else content)
    p.close()
    return p.rows, p.lines


def _after(lines, key):
    """第一個含 key 的文字行的下一行（富邦、凱基的標籤和值是相鄰的兩個元素）。"""
    for i, t in enumerate(lines[:-1]):
        if key in t:
            return lines[i + 1]
    return None


def parse_nomura(obj):
    """野村 GetFundTradeInfo：Entries.CPcfdate 清單日、CNavDt 持股日、CAnceTotalIssues 單位數、CAnceIssuesDiff 與前日差異、
    CAnceTotalAv 淨資產、CAnceNav 每單位淨值；Stocks（CStockCode、CQuantity 股數、CWeightsPct）、Futures（CContractYm）。"""
    e = (obj or {}).get("Entries") or {}
    if (obj or {}).get("StatusCode") != 0 or not e:
        return None                                       # 非交易日、還沒公布：Entries=null
    snap = {"list_date": _ad(e.get("CPcfdate")), "as_of": _ad(e.get("CNavDt")),
            "units": _num(e.get("CAnceTotalIssues")), "units_chg": _num(e.get("CAnceIssuesDiff")),
            "nav_total": _num(e.get("CAnceTotalAv")), "nav_unit": _num(e.get("CAnceNav"))}
    stocks = []
    for x in (e.get("Stocks") or []) + (e.get("Etfs") or []):
        code = next((v for k, v in x.items() if k.endswith("Code") and v), None)
        name = next((v for k, v in x.items() if k.endswith("Name") and v), "")
        if code and _num(x.get("CQuantity")) is not None:
            stocks.append((str(code).strip().upper(), str(name).strip(), _num(x.get("CQuantity")),
                           _num(x.get("CWeightsPct")), None))
    snap["stocks"] = stocks
    snap["futures"] = [(str(f["CFuturesCode"]).strip() + re.sub(r"\D", "", str(f.get("CContractYm") or "")),
                        f"{(f.get('CFuturesName') or '').strip()} {f.get('CContractYm') or ''}".strip(),
                        _num(f.get("CQuantity")), _num(f.get("CWeightsPct")))
                       for f in e.get("Futures") or [] if f.get("CFuturesCode") and _num(f.get("CQuantity")) is not None]
    return _valid(snap)


def parse_ctbc(obj):
    """中信 etf/ETFHoldingWeight：Data.FundAssets[0] 的 資料日期／基金淨資產／基金在外流通單位數／基金每單位淨值；
    FundAssetsDetail 各區塊的 invtp_：STOCK 股票（qty_ 股數、weights_、amount_）、FUTURE 期貨（ym_ 契約年月）；
    選擇權（00406A 賣台指買權）、現金、保證金略過。上市前只有淨值、沒有持股的日子回 None。"""
    if (obj or {}).get("ResultCode") != 0:
        return None
    data = obj.get("Data") or {}
    fa = (data.get("FundAssets") or [None])[0]
    if not fa:
        return None
    snap = {"as_of": _ad(fa.get("資料日期")), "nav_total": _num(fa.get("基金淨資產")),
            "units": _num(fa.get("基金在外流通單位數")), "nav_unit": _num(fa.get("基金每單位淨值"))}
    stocks, futures = [], []
    for sec in data.get("FundAssetsDetail") or []:
        for r in sec.get("Data") or []:
            kind, code, qty = (r.get("invtp_") or "").upper(), (r.get("code_") or "").strip().upper(), _num(r.get("qty_"))
            if not code or qty is None:
                continue
            name = (r.get("name_") or "").strip()
            if kind == "STOCK":
                stocks.append((code, name, qty, _num(r.get("weights_")), _num(r.get("amount_"))))
            elif kind == "FUTURE":
                ym = (r.get("ym_") or "").strip()
                futures.append((code + ym, f"{name} {ym}".strip(), qty, _num(r.get("weights_"))))
    snap["stocks"], snap["futures"] = stocks, futures
    return _valid(snap)


def parse_cathay(content):
    """國泰持股權重 Excel：標題 '2026/09/24基金持股權重'；基金淨資產價值／基金在外流通單位數／基金每單位淨值 的值在同一列；
    股票表頭 股票代號／股票名稱／股數／持股權重（沒有市值），期貨 期貨代號／口數／契約年月；選擇權不收。兩字的名稱中間有空白。"""
    rows = _xlsx_rows(content)
    snap = {}
    for r in rows:
        cells = [c for c in r if c]
        if not cells:
            continue
        c0, v = cells[0], (cells[1] if len(cells) > 1 else None)
        m = re.search(r"(\d{4}/\d{1,2}/\d{1,2})\s*基金持股權重", c0)
        if m and "as_of" not in snap:
            snap["as_of"] = _ad(m.group(1))
        elif c0.startswith("基金淨資產價值"):
            snap["nav_total"] = _num(v)
        elif c0.startswith("基金在外流通單位數"):
            snap["units"] = _num(v)
        elif c0.startswith("基金每單位淨值"):
            snap["nav_unit"] = _num(v)
    stocks, snap["futures"] = _parse_tables(rows)
    snap["stocks"] = [(c, re.sub(r"\s+", "", n or ""), q, w, a) for c, n, q, w, a in stocks]
    return _valid(snap)


def parse_fubon(content):
    """富邦 Trade/Assets.aspx：'資料日期：2026/09/24'；基金淨資產(新台幣)／基金在外流通單位數(單位)／基金每單位淨值(新台幣)
    的值在下一個元素；股票表頭 股票代碼／股票名稱／股數／金額／權重(%)，期貨 期貨代碼／口數。沒資料時寫「尚未有資料！」。"""
    rows, lines = _html_rows(content)
    m = re.search(r"資料日期[：:]\s*(\d{4}/\d{1,2}/\d{1,2})", "\n".join(lines))
    snap = {"as_of": _ad(m.group(1)) if m else None, "nav_total": _num(_after(lines, "基金淨資產(新台幣)")),
            "units": _num(_after(lines, "基金在外流通單位數")), "nav_unit": _num(_after(lines, "基金每單位淨值"))}
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def parse_kgi(content):
    """凱基 RedemptionVC 片段：<input id="DataDate" value="清單日">；'(2026/09/24)每受益權單位淨資產價值(元)' 括號裡是持股日；
    基金淨資產價值／已發行受益權單位總數／與前日已發行單位差異數 的值在下一個元素；股票表頭 股票代號／股數／權重(%)（沒有市值）。"""
    raw = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    rows, lines = _html_rows(raw)
    m = re.search(r'id="DataDate"[^>]*value="([^"]*)"', raw)
    snap = {"list_date": _ad(m.group(1)) if m else None, "nav_total": _num(_after(lines, "基金淨資產價值")),
            "units": _num(_after(lines, "已發行受益權單位總數")), "units_chg": _num(_after(lines, "與前日已發行單位差異數"))}
    for i, t in enumerate(lines[:-1]):
        if "每受益權單位淨資產價值" in t:
            snap["as_of"], snap["nav_unit"] = _ad(t), _num(lines[i + 1])
            break
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def parse_allianz(obj):
    """安聯 Fund/GetFundTradeInfo：基金層級欄位跟野村同一套（CPcfdate 清單日、CNavDt 持股日、CAnceTotalIssues、
    CAnceIssuesDiff、CAnceTotalAv、CAnceNav）；持股在 DynamicTableData（每張表 Columns／Rows，儲存格是字串）：
    股票 股票代號／股票名稱／股數／權重(%)（沒有市值），期貨 期貨代號／口數／契約年月；附買回債券表不收。
    2025 年的檔沒有「序號」欄、字串尾端補空白。沒資料：StatusCode=1 或 Entries=null。"""
    e = (obj or {}).get("Entries") or {}
    if (obj or {}).get("StatusCode") != 0 or not e:
        return None
    snap = {"list_date": _ad(e.get("CPcfdate")), "as_of": _ad(e.get("CNavDt")),
            "units": _num(e.get("CAnceTotalIssues")), "units_chg": _num(e.get("CAnceIssuesDiff")),
            "nav_total": _num(e.get("CAnceTotalAv")), "nav_unit": _num(e.get("CAnceNav"))}
    rows = []
    for t in e.get("DynamicTableData") or []:
        rows.append([str(c.get("Name") or "").strip() for c in t.get("Columns") or []])
        rows += [["" if v is None else str(v).strip() for v in r] for r in t.get("Rows") or []]
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


_FUT_CODES = (("小型", "MTX"), ("微型", "TMF"), ("臺股期貨", "TX"), ("台股期貨", "TX"), ("臺指", "TX"), ("台指", "TX"))


def parse_fsitc(obj):
    """第一金：fetch 把 Get_BuySellA、Get_hd 兩支的內容合成 {"Get_BuySellA": [...], "Get_hd": [...]}。
    Get_BuySellA 是 標籤 A／值 B 的列（sdate＝清單日）：基金淨資產價值(元)、每受益權單位淨資產價值(元)-台幣交易、
    已發行受益權單位總數-台幣交易、與前日已發行單位差異數-台幣交易。Get_hd（sdate＝持股日）：group 1 股票（A 代號、B 名稱、
    C 權重、D 股數；沒有市值）、group 2 期貨（A 名稱、B 權重、C 口數、D 契約年月；沒有期貨代號，用名稱對照）、4 現金、5 資產配置。"""
    a, hd = (obj or {}).get("Get_BuySellA") or [], (obj or {}).get("Get_hd") or []
    if not a or not hd:
        return None
    labels = [(str(r.get("A") or ""), r.get("B")) for r in a]
    val = lambda key: next((_num(v) for k, v in labels if k.startswith(key)), None)
    snap = {"list_date": _ad(a[0].get("sdate")), "as_of": _ad(hd[0].get("sdate")),
            "nav_total": val("基金淨資產價值"), "nav_unit": val("每受益權單位淨資產價值"),
            "units": val("已發行受益權單位總數"), "units_chg": val("與前日已發行單位差異數")}
    stocks, futures = [], []
    for r in hd:
        g = str(r.get("group"))
        if g == "1":
            code, qty = str(r.get("A") or "").strip().upper(), _num(r.get("D"))
            if CODE_RE.fullmatch(code) and qty is not None:
                stocks.append((code, str(r.get("B") or "").strip(), qty, _num(r.get("C")), None))
        elif g == "2":
            name, ym, qty = str(r.get("A") or "").strip(), str(r.get("D") or "").strip(), _num(r.get("C"))
            if name and qty is not None:
                code = next((c for k, c in _FUT_CODES if k in name), name)
                futures.append(((code + re.sub(r"\D", "", ym))[:20], f"{name} {ym}".strip(), qty, _num(r.get("B"))))
    snap["stocks"], snap["futures"] = stocks, futures
    return _valid(snap)


def parse_mega(content):
    """兆豐 trade_pcf.aspx：標題 '2026/09/29 現金 申購買回清單公告' 是清單日，'2026/09/24 每基數實際申購總價金(元)' 這類標籤的
    日期是持股日；基金淨資產價值(元)／已發行受益權單位總數／與前日已發行單位差異數／每受益權單位淨資產價值(元) 的值在下一個元素
    （頁尾警語也有「基金淨資產價值」，所以比對整個標籤）。股票表頭 股票代號／股票名稱／股數／持股權重（沒有市值）；
    期貨 期貨代號／契約年月／口數，空單的口數是負數、權重不帶正負號（store 會轉成負的）。沒資料時標題沒有日期。"""
    rows, lines = _html_rows(content)
    snap = {"list_date": _ad(next((t for t in lines if "申購買回清單公告" in t), None)),
            "as_of": _ad(next((t for t in lines if re.match(r"\d{4}/\d{1,2}/\d{1,2}\s", t) and "申購總價金" in t), None)),
            "nav_total": _num(_after(lines, "基金淨資產價值(元)")), "units": _num(_after(lines, "已發行受益權單位總數")),
            "units_chg": _num(_after(lines, "與前日已發行單位差異數")),
            "nav_unit": _num(_after(lines, "每受益權單位淨資產價值(元)"))}
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def parse_taishin(content):
    """台新 Pcf/<ETF>：隱藏欄位 DATA_DATE＝清單日（假日、還沒公布時照抄請求日期，內容卻是最新一份）、MAX_DATE＝最新清單日，
    所以清單日取兩者較早的；'2026/9/23每基數實際申購總價金(元)' 的日期是持股日（空白樣板是 0001/1/1）。
    基金淨資產價值(元)／已發行受益權單位總數／與前日已發行單位差異數（負數用括號）／每受益權單位淨資產價值(元) 的值在下一格；
    股票代號是彭博格式 '2330 TT'，沒有市值欄；清單日 2025-12-17～24 只有淨值、沒有持股。"""
    raw = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    rows, lines = _html_rows(raw)

    def hidden(name):
        m = re.search(r'id="%s"[^>]*value="([^"]*)"' % name, raw)
        return _ad(m.group(1)) if m else None

    m = re.search(r"(\d{4}/\d{1,2}/\d{1,2})\s*每基數實際申購總價金", raw)
    as_of = _ad(m.group(1)) if m else None
    ld, mx = hidden("DATA_DATE") or hidden("PUB_DATE"), hidden("MAX_DATE")
    snap = {"as_of": as_of if as_of and as_of.year >= 2000 else None,
            "list_date": min(ld, mx) if ld and mx else ld,
            "nav_total": _num(_after(lines, "基金淨資產價值(元)")), "units": _num(_after(lines, "已發行受益權單位總數")),
            "units_chg": _acct(_after(lines, "與前日已發行單位差異數")),
            "nav_unit": _num(_after(lines, "每受益權單位淨資產價值(元)"))}
    rows = [[re.sub(r"^([0-9A-Z]{4,6}) TT$", r"\1", c) for c in r] for r in rows]
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def parse_jpm(content):
    """摩根 m12_pcf Excel（5 張工作表）：'現金申購買回清單公告 (2026-09-29)' 是清單日，'2026/09/24 每受益權單位淨資產價值(元)'
    的日期是持股日；基金淨資產價值(元)／已發行受益權單位總數／與前日已發行單位差異數 的值在同一列。股票表 股票代碼／股票名稱／
    股數／金額／權重(%)；期貨 商品代碼（彭博格式，如 FTV6）／商品數量 (口數)／權重，權重是保證金市值、不是名目本金。
    選擇權（掩護性買權，賣買權）的表頭跟期貨一樣，用工作表名稱排除；現金表沒有代碼欄，不會被當成持股。"""
    rows = [r for title, rs in _xlsx_sheets(content) if "選擇權" not in title for r in rs]
    snap = {}
    for r in rows:
        cells = [c for c in r if c]
        if not cells:
            continue
        c0, v = cells[0], (cells[1] if len(cells) > 1 else None)
        if c0.startswith("現金申購買回清單公告") and "list_date" not in snap:
            snap["list_date"] = _ad(c0)
        elif c0.startswith("基金淨資產價值"):
            snap["nav_total"] = _num(v)
        elif c0.startswith("已發行受益權單位總數"):
            snap["units"] = _num(v)
        elif c0.startswith("與前日已發行單位差異數"):
            snap["units_chg"] = _acct(v)
        elif "每受益權單位淨資產價值" in c0 and re.match(r"\d{4}/\d{1,2}/\d{1,2}", c0):
            snap["as_of"], snap["nav_unit"] = _ad(c0), _num(v)
    snap["stocks"], snap["futures"] = _parse_tables(rows)
    return _valid(snap)


def _json(r):
    try:
        return r.json()
    except ValueError:
        return None


# ---------- 抓取器 ----------

class FetchError(Exception):
    pass


class _SystemTrust(requests.adapters.HTTPAdapter):
    """用 Windows 系統的憑證驗證（truststore）。Python 3.13 預設的嚴格檢查會擋掉 TWCA 根憑證（缺 Subject Key
    Identifier），野村、中信、凱基官網因此連不上；瀏覽器、curl 走的是系統驗證所以沒問題。這裡沒有放寬任何檢查。"""

    def init_poolmanager(self, *args, **kwargs):
        import ssl
        import truststore
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().init_poolmanager(*args, **kwargs)


class Adapter:
    """每家投信一個實例（一個 session），同一家的請求依序送。"""
    pcf_dates = True        # True：請求日期是「清單日」，內容是前一交易日的持股；False：請求日期＝持股日
    system_trust = False    # True：憑證用系統驗證（見 _SystemTrust）
    min_delay = 0.0         # 這家至少間隔幾秒

    def __init__(self, delay):
        self.delay = max(delay, self.min_delay)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
        if self.system_trust:
            self.s.mount("https://", _SystemTrust())

    def _req(self, method, url, none_on=(), **kw):
        """200 回 response；none_on 裡的狀態碼（凱基沒資料回 500）直接回 None、不重試；其他錯誤重試 3 次後丟 FetchError。"""
        last = None
        for attempt in range(3):
            time.sleep(self.delay)
            try:
                r = self.s.request(method, url, timeout=45, **kw)
                if r.status_code == 200:
                    return r
                if r.status_code in none_on:
                    return None
                last = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last = str(e)[:100]
            time.sleep(3 * (attempt + 1))
        raise FetchError(f"{url} → {last}")

    def request_date_for(self, as_of, trading_days):
        """要拿到某持股日，該送哪個請求日期（清單日＝下一個交易日；未知則 None）。"""
        if not self.pcf_dates:
            return as_of
        later = [d for d in trading_days if d > as_of]
        return later[0] if later else None


class Uni(Adapter):
    def __init__(self, delay):
        super().__init__(delay)
        self._warm = False
        self._latest = None

    def _warmup(self):
        if not self._warm:
            self._req("GET", UNI_HOME)                   # 拿 cookie，否則 Excel 端點會被導回首頁
            self._warm = True

    def fetch(self, fund, d):
        self._warmup()
        url = UNI_XLSX.format(code=fund.code, roc=f"{d.year - 1911}/{d:%m/%d}")
        r = self._req("GET", url, headers={"Referer": UNI_PAGE.format(code=fund.code)})
        if "spreadsheetml" not in r.headers.get("Content-Type", ""):
            return None
        snap = parse_uni(r.content)
        return (snap, r.content, "xlsx", url) if snap else None

    def fetch_latest(self, fund):
        if self._latest is None:                         # PCF 頁面預設的清單日＝最新一份已公布的清單（各基金相同）
            self._warmup()
            html = self._req("GET", UNI_PAGE.format(code=fund.code)).text
            m = re.search(r'#ED"\)\.val\("(\d{2,3}/\d{2}/\d{2})"\)', html)
            if not m:
                raise FetchError("統一 PCF 頁面找不到預設清單日（頁面改版？）")
            self._latest = _roc(m.group(1))
        return self.fetch(fund, self._latest)


class Capital(Adapter):
    def fetch(self, fund, d):
        body = {"fundId": int(fund.code)}
        if d is not None:
            body["date"] = d.isoformat()
        r = self._req("POST", CAP_API, json=body,
                      headers={"Referer": CAP_PAGE.format(code=fund.code), "Accept": "application/json, text/plain, */*"})
        try:
            obj = r.json()
        except ValueError:
            return None
        if str(obj.get("code")) != "200":
            return None
        snap = parse_capital(obj)
        return (snap, r.content, "json", f"{CAP_API} {body}") if snap else None

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Fh(Adapter):
    pcf_dates = False

    def fetch(self, fund, d):
        url = FH_XLSX.format(code=fund.code, ymd=f"{d:%Y%m%d}")
        r = self._req("GET", url, headers={"Referer": FH_PAGE.format(code=fund.code)})
        if r.content[:2] != b"PK":                       # 沒資料時回 JSON「查無資料」
            return None
        snap = parse_fh(r.content)
        return (snap, r.content, "xlsx", url) if snap else None

    def fetch_latest(self, fund):
        d = date.today()
        for _ in range(10):                              # 由今天往回找第一個有資料的平日
            if d.weekday() < 5:
                got = self.fetch(fund, d)
                if got:
                    return got
            d -= timedelta(days=1)
        return None


class Nomura(Adapter):
    system_trust = True
    min_delay = 1.0

    def _post(self, fund, path, body):
        return self._req("POST", NOMURA_API + path, json=body,
                         headers={"Referer": NOMURA_PAGE.format(code=fund.code), "Origin": NOMURA_HOST,
                                  "Accept": "application/json, text/plain, */*"})

    def fetch(self, fund, d):
        body = {"Type": 1, "Keyword": "", "FundNo": fund.code, "Date": f"{d:%Y/%m/%d}"}
        r = self._post(fund, "Fund/GetFundTradeInfo", body)
        snap = parse_nomura(_json(r))
        return (snap, r.content, "json", f"{NOMURA_API}Fund/GetFundTradeInfo {body}") if snap else None

    def fetch_latest(self, fund):
        body = {"Type": 1, "Keyword": "", "FundNo": fund.code, "Date": f"{date.today():%Y/%m/%d}"}
        latest = _ad(((_json(self._post(fund, "Fund/GetFundTradeInfoDate", body)) or {}).get("Entries") or {})
                     .get("LatestDate"))
        return self.fetch(fund, latest) if latest else None


class Ctbc(Adapter):
    pcf_dates = False
    system_trust = True
    min_delay = 1.0

    def __init__(self, delay):
        super().__init__(delay)
        self._token = None

    def _call(self, path, body, referer):
        """POST API/<path>?token=…，body 也要帶 token；token 過期就重拿一次。錯誤是 HTTP 200 + ResultCode≠0。"""
        headers = {"Referer": referer, "Origin": CTBC_SITE, "Accept": "application/json, text/plain, */*"}
        for _ in range(2):
            if not self._token:
                r = self._req("POST", CTBC_API + "home/AuthToken", params={"token": CTBC_BOOT},
                              json={"token": CTBC_BOOT}, headers=headers)
                self._token = ((_json(r) or {}).get("Data") or {}).get("token") or CTBC_BOOT
            r = self._req("POST", CTBC_API + path, params={"token": self._token},
                          json=dict(body, token=self._token), headers=headers)
            obj = _json(r) or {}
            if obj.get("ResultCode") == 0:
                return r, obj
            if "Token" in str(obj.get("ResultMsg")):
                self._token = None
                continue
            raise FetchError(f"中信 {path}：{str(obj.get('ResultMsg'))[:80]}")
        raise FetchError("中信 token 連續兩次無效")

    def fetch(self, fund, d, strict=True):
        body = {"FID": fund.code, "StartDate": d.isoformat()}
        r, obj = self._call("etf/ETFHoldingWeight", body, CTBC_PAGE.format(etf=fund.etf_id))
        snap = parse_ctbc(obj)
        if not snap or (strict and snap["as_of"] != d):  # 伺服器回「≤ 指定日的最近一天」
            return None
        return (snap, r.content, "json", f"{CTBC_API}etf/ETFHoldingWeight {body}")

    def fetch_latest(self, fund):
        return self.fetch(fund, date.today(), strict=False)


class Cathay(Adapter):
    pcf_dates = False
    min_delay = 1.0

    def fetch(self, fund, d):
        url = f"{CATHAY_API}ETF/DownloadETFWeightExcel?FundCode={fund.code}&SearchDate={d.isoformat()}"
        r = self._req("GET", url, headers={"Referer": CATHAY_PAGE.format(code=fund.code)})
        if not r.content.startswith(b"PK"):              # 非交易日、還沒公布：空白內容
            return None
        snap = parse_cathay(r.content)
        return (snap, r.content, "xlsx", url) if snap and snap["as_of"] == d else None

    def fetch_latest(self, fund):
        r = self._req("GET", CATHAY_API + "ETF/GetETFAssets", params={"FundCode": fund.code, "status": "1"},
                      headers={"Referer": CATHAY_PAGE.format(code=fund.code), "Accept": "application/json, text/plain, */*"})
        d = _ad(((_json(r) or {}).get("result") or {}).get("preDate"))   # 最新持股日
        return self.fetch(fund, d) if d else None


class Fubon(Adapter):
    pcf_dates = False
    min_delay = 1.0

    def fetch(self, fund, d):
        params = {"stkId": fund.code, "lan": "TW"}
        if d:
            params["ddate"] = f"{d:%Y/%m/%d}"
        r = self._req("GET", FUBON_ASSETS, params=params, headers={"Referer": FUBON_PAGE.format(code=fund.code)})
        snap = parse_fubon(r.content)
        if not snap or (d and snap["as_of"] != d):       # 伺服器回「≤ ddate 的最近一天」
            return None
        return (snap, r.content, "html", r.url)

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Kgi(Adapter):
    system_trust = True
    min_delay = 1.0

    def fetch(self, fund, d):
        body = {"fundID": fund.code, "queryDate": f"{d:%Y/%m/%d}" if d else ""}
        r = self._req("POST", KGI_VC, data=body, none_on=(500,),
                      headers={"Referer": KGI_PAGE, "Origin": "https://www.kgifund.com.tw",
                               "X-Requested-With": "XMLHttpRequest"})
        if r is None:                                     # 非交易日、還沒公布：HTTP 500
            return None
        snap = parse_kgi(r.content)
        if not snap or (d and snap.get("list_date") != d):   # 日期格式不對時會默默回最新一份
            return None
        return (snap, r.content, "html", f"{KGI_VC} {body}")

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Allianz(Adapter):
    min_delay = 1.0

    def __init__(self, delay):
        super().__init__(delay)
        self._xsrf = None

    def fetch(self, fund, d):
        body = {"FundNo": fund.code, "Date": d.isoformat() if d else None}
        for _ in range(2):
            if not self._xsrf:                            # 防偽 token（cookie 有效 24 小時）：值要再放進 header
                t = self._req("GET", ALLIANZ_API + "AntiForgery/GetAntiForgeryToken",
                              headers={"Accept": "application/json, text/plain, */*", "Referer": ALLIANZ_SITE + "/list-trade"})
                self._xsrf = self.s.cookies.get("X-XSRF-TOKEN") or (_json(t) or {}).get("token")
                if not self._xsrf:
                    raise FetchError("安聯拿不到 X-XSRF-TOKEN（網站改版？）")
            r = self._req("POST", ALLIANZ_API + "Fund/GetFundTradeInfo", json=body, none_on=(400,),
                          headers={"X-XSRF-TOKEN": self._xsrf, "Accept": "application/json, text/plain, */*",
                                   "Origin": ALLIANZ_SITE, "Referer": ALLIANZ_SITE + "/list-trade"})
            if r is not None:
                break
            self._xsrf = None                             # HTTP 400＝token 過期或無效：重拿一次
        else:
            raise FetchError("安聯 token 連續兩次無效")
        snap = parse_allianz(_json(r))
        if not snap or (d and snap["list_date"] != d):
            return None
        return (snap, r.content, "json", f"{ALLIANZ_API}Fund/GetFundTradeInfo {body}")

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Fsitc(Adapter):
    system_trust = True
    min_delay = 1.0

    def _call(self, fund, method, ds):
        r = self._req("POST", FSITC_API + method, data=json.dumps({"pStrFundID": fund.code, "pStrDate": ds}),
                      headers={"Content-Type": "application/json; charset=utf-8",
                               "Accept": "application/json, text/javascript, */*; q=0.01",
                               "X-Requested-With": "XMLHttpRequest", "Origin": FSITC_HOST,
                               "Referer": FSITC_PAGE.format(code=fund.code)})
        if not r.headers.get("Content-Type", "").startswith("application/json"):
            raise FetchError(f"第一金 {method} 回的不是 JSON（{r.headers.get('Content-Type')}）")
        d = (_json(r) or {}).get("d")
        try:
            rows = json.loads(d) if d else []             # 沒資料（假日、還沒公布、上市前）：d 是空字串
        except ValueError:
            raise FetchError(f"第一金 {method} 的 d 不是 JSON")
        if any(str(x.get("fundid")) != fund.code for x in rows):
            raise FetchError(f"第一金 {method} 回的不是基金 {fund.code}")
        return rows

    def fetch(self, fund, d):
        a = self._call(fund, "Get_BuySellA", f"{d:%Y/%m/%d}" if d else "")
        ld = _ad(a[0].get("sdate")) if a else None
        if not ld or (d and ld != d):
            return None
        obj = {"Get_BuySellA": a, "Get_hd": self._call(fund, "Get_hd", f"{ld:%Y/%m/%d}")}   # 兩支用同一個清單日
        snap = parse_fsitc(obj)
        if not snap or snap["as_of"] >= ld:
            return None
        return (snap, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "json",
                f"{FSITC_API}Get_BuySellA+Get_hd {fund.code} {ld}")

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Mega(Adapter):
    min_delay = 1.0

    def fetch(self, fund, d):
        body = {"ctl00$ContentPlaceHolder1$category_id": "", "ctl00$ContentPlaceHolder1$fund_id": fund.code,
                "ctl00$ContentPlaceHolder1$qdt": f"{d:%Y/%m/%d}" if d else "", "ctl00$ContentPlaceHolder1$button2": "查 詢"}
        r = self._req("POST", MEGA_PCF, data=body, headers={"Referer": MEGA_PCF, "Origin": MEGA_HOST})
        m = re.search(r"股票代號[：:]\s*([0-9A-Z]{4,7})", r.content.decode("utf-8", "replace"))
        if not m or m.group(1) != fund.etf_id:            # 伺服器沒吃 fund_id 時會回預設的 00690
            raise FetchError(f"兆豐回的不是 {fund.etf_id}（{m.group(1) if m else '找不到代號'}；頁面改版？）")
        snap = parse_mega(r.content)
        if not snap or (d and snap["list_date"] != d):   # 日期格式不對時會默默回最新一份
            return None
        return (snap, r.content, "html", f"{MEGA_PCF} fund_id={fund.code} qdt={d or ''}")

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Taishin(Adapter):
    min_delay = 1.0

    def fetch(self, fund, d):
        url = TAISHIN_PCF.format(etf=fund.etf_id)
        params = {"FundType": "ALL", **({"DataDate": d.isoformat()} if d else {})}
        r = self._req("GET", url, params=params, headers={"Referer": url}, allow_redirects=False, none_on=(301, 302))
        if r is None:                                     # 日期格式不對、代號不存在：轉址回 /ETF/
            return None
        snap = parse_taishin(r.content)
        if not snap or (d and snap["list_date"] != d) or snap["as_of"] >= snap["list_date"]:
            return None                                   # 假日、還沒公布：內容是最新一份（清單日對不上）
        return (snap, r.content, "html", r.url)

    def fetch_latest(self, fund):
        return self.fetch(fund, None)


class Jpm(Adapter):
    min_delay = 10.0        # robots.txt 對 AI 爬蟲設 Crawl-delay 10；每晚只要 2 個請求，就照這個間隔
    history_days = 40       # 官網只留約 31 天（2026-09-28 最早是清單日 8/24），更早的日期不送請求

    def fetch(self, fund, d):
        if (date.today() - d).days > self.history_days:
            return None
        r = self._req("GET", JPM_HANDLER + "excel", none_on=(404,), headers={"Referer": JPM_PAGE},
                      params={"type": "m12_pcf", "cusip": fund.code, "country": "tw", "role": "twetf",
                              "locale": "zh-TW", "date": d.isoformat()})
        if r is None or not r.content.startswith(b"PK"):   # 假日、還沒公布、超過保存期限：HTTP 404
            return None
        snap = parse_jpm(r.content)
        if not snap or snap["list_date"] != d:
            return None
        return (snap, r.content, "xlsx", r.url)

    def fetch_latest(self, fund):
        r = self._req("GET", JPM_HANDLER + "product-data",
                      headers={"Referer": JPM_PAGE, "Accept": "application/json, text/plain, */*"},
                      params={"cusip": fund.code, "country": "tw", "role": "twetf", "language": "zh", "userLoggedIn": "false"})
        fd = (_json(r) or {}).get("fundData") or {}
        d = _ad(((fd.get("breakdowns") or {}).get("m12Details") or {}).get("effectiveDate"))   # 最新清單日
        return self.fetch(fund, d) if d else None


ADAPTERS = {"uni": Uni, "capital": Capital, "fh": Fh,
            "nomura": Nomura, "ctbc": Ctbc, "cathay": Cathay, "fubon": Fubon, "kgi": Kgi,
            "allianz": Allianz, "fsitc": Fsitc, "mega": Mega, "taishin": Taishin, "jpm": Jpm}


# ---------- 入庫 ----------

def sync_funds(cur):
    from psycopg2.extras import execute_values
    execute_values(cur, """
        INSERT INTO etf_fund (etf_id, issuer, adapter, fund_code, name) VALUES %s
        ON CONFLICT (etf_id) DO UPDATE SET issuer = EXCLUDED.issuer, adapter = EXCLUDED.adapter,
            fund_code = EXCLUDED.fund_code, name = EXCLUDED.name, updated_at = now()""", FUNDS)


def save_raw(fund, snap, raw, ext, root):
    if not root:
        return None
    try:
        path = os.path.join(root, fund.etf_id, f"{snap['as_of']:%Y%m%d}.{ext}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
        return path
    except OSError as e:
        log(f"    （原始檔存不進 {root}：{e}；只寫 DB）")
        return None


def store(cur, fund, snap, source, raw_file):
    from psycopg2.extras import execute_values
    agg = {}
    for code, name, qty, w, amt in snap["stocks"]:                    # 同代號出現兩次就合併
        a = agg.setdefault(("stock", code), [name, 0.0, None, None])
        a[1] += qty
        a[2] = (a[2] or 0) + w if w is not None else a[2]
        a[3] = (a[3] or 0) + amt if amt is not None else a[3]
    for code, name, qty, w in snap["futures"]:
        if qty < 0 and w and w > 0:
            w = -w                                                     # 空單（兆豐口數為負、權重不帶號）：曝險要扣掉
        a = agg.setdefault(("futures", code), [name, 0.0, None, None])
        a[1] += qty
        a[2] = (a[2] or 0) + w if w is not None else a[2]
    stock_w = sum(v[2] or 0 for k, v in agg.items() if k[0] == "stock")
    fut_w = sum(v[2] or 0 for k, v in agg.items() if k[0] == "futures")
    cur.execute("DELETE FROM etf_snapshot WHERE etf_id = %s AND as_of = %s", (fund.etf_id, snap["as_of"]))
    cur.execute("""INSERT INTO etf_snapshot (etf_id, as_of, list_date, units, units_chg, nav_total, nav_unit,
                        stock_weight, futures_weight, n_stocks, source, raw_file)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (fund.etf_id, snap["as_of"], snap.get("list_date"), snap.get("units"), snap.get("units_chg"),
                 snap.get("nav_total"), snap.get("nav_unit"), round(stock_w, 4), round(fut_w, 4),
                 sum(1 for k in agg if k[0] == "stock"), source[:500], raw_file))
    execute_values(cur, "INSERT INTO etf_holding (etf_id, as_of, kind, code, name, shares, weight, amount) VALUES %s",
                   [(fund.etf_id, snap["as_of"], k[0], k[1], v[0], round(v[1]), v[2], v[3]) for k, v in agg.items()])


def summary(fund, snap):
    top = sorted(snap["stocks"], key=lambda s: -(s[3] or 0))[:5]
    fut = "、".join(f"{c} {q:,.0f} 口" for c, _, q, _ in snap["futures"]) or "無"
    units = f"{snap['units']:,.0f}" if snap.get("units") else "?"
    return (f"{fund.etf_id} 持股日 {snap['as_of']}（清單日 {snap.get('list_date') or '-'}）單位數 {units}，"
            f"股票 {len(snap['stocks'])} 檔，期貨 {fut}；前五大 " + "、".join(f"{s[1]} {s[3]}%" for s in top))


# ---------- 主流程 ----------

def run_group(adapter_name, funds, args, trading_days, list_dates):
    """同一家投信的基金依序處理（每個 thread 一個 DB 連線）。回傳 {etf_id: [新入庫的持股日]}。"""
    ad = ADAPTERS[adapter_name](args.delay)
    added = {}
    conn = None
    if not args.dry_run:
        import psycopg2
        conn = psycopg2.connect(args.dsn)
    try:
        for fund in funds:
            got_dates = []
            stored = set()
            if conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT as_of FROM etf_snapshot WHERE etf_id = %s", (fund.etf_id,))
                    stored = {r[0] for r in cur.fetchall()}

            def take(got, why):
                snap, raw, ext, url = got
                if args.dry_run:
                    log(f"  [{why}] {summary(fund, snap)}")
                    return
                if snap["as_of"] in stored and not args.refetch:
                    return
                with conn, conn.cursor() as cur:
                    store(cur, fund, snap, f"{adapter_name} {url}", save_raw(fund, snap, raw, ext, args.raw_root))
                stored.add(snap["as_of"])
                got_dates.append(snap["as_of"])

            # 1) 回補／補缺口：找出應有但沒有的持股日，用日期參數逐日補
            if not args.dry_run:
                ld = list_dates.get(fund.etf_id)
                if args.backfill or args.since:
                    start = max(x for x in (ld, args.since, date(2025, 1, 1)) if x)
                    days = [d for d in trading_days if d >= start]
                else:
                    days = trading_days[-args.gap_days:]
                missing = [d for d in days if d not in stored and (ld is None or d >= ld)]
                if missing:
                    log(f"  {fund.etf_id} 要補 {len(missing)} 個持股日（{missing[0]} ~ {missing[-1]}）")
                miss_n = 0
                for i, as_of in enumerate(missing, 1):
                    req = ad.request_date_for(as_of, trading_days)
                    if req is None:
                        continue                          # 最後一個交易日的清單日還不知道，交給下面的「最新」
                    try:
                        got = ad.fetch(fund, req)
                    except FetchError as e:
                        log(f"    {fund.etf_id} {as_of} 抓取失敗：{e}")
                        continue
                    if got:
                        take(got, "補")
                    else:
                        miss_n += 1
                    if i % 50 == 0:
                        log(f"    {fund.etf_id} 進度 {i}/{len(missing)}，已入庫 {len(got_dates)}")
                if missing and miss_n:
                    log(f"    {fund.etf_id} 有 {miss_n} 個日期官網沒有資料（上市前、尚未公布或超過保存期限）")
            # 2) 最新一份
            try:
                got = ad.fetch_latest(fund)
            except FetchError as e:
                log(f"  {fund.etf_id} 最新持股抓取失敗：{e}")
                got = None
            if got:
                take(got, "最新")
            elif not args.dry_run:
                log(f"  {fund.etf_id} 沒抓到最新持股（還沒公布？）")
            if not args.dry_run:
                latest = max(stored) if stored else None
                log(f"  {fund.etf_id} {fund.name}：新入庫 {len(got_dates)} 天，最新持股日 {latest}")
            added[fund.etf_id] = got_dates
    finally:
        if conn:
            conn.close()
    return added


def main():
    ap = argparse.ArgumentParser(description="抓主動式 ETF 每日持股 → etf_snapshot/etf_holding，並算進出 → etf_flow")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL 連線字串")
    ap.add_argument("--etf", default="", help="只處理這些 ETF（逗號分隔，如 00981A,00991A）")
    ap.add_argument("--backfill", action="store_true", help="回補上市以來（或 --since 起）所有缺的持股日")
    ap.add_argument("--since", type=date.fromisoformat, help="回補起日 YYYY-MM-DD（搭配 --backfill）")
    ap.add_argument("--gap-days", type=int, default=10, help="平常每次回頭檢查幾個交易日的缺口（預設 10）")
    ap.add_argument("--refetch", action="store_true", help="已有的持股日也重抓覆蓋")
    ap.add_argument("--delay", type=float, default=0.8, help="同一家投信兩次請求的間隔秒數（預設 0.8）")
    ap.add_argument("--raw-root", default=RAW_ROOT, help=r"原始檔目錄（預設 H:\data\ETF_HOLD；空字串＝不存）")
    ap.add_argument("--no-flow", action="store_true", help="只抓持股，不算進出")
    ap.add_argument("--dry-run", action="store_true", help="只抓最新、解析、印摘要，不寫 DB")
    args = ap.parse_args()
    args.dsn = clean_dsn(args.dsn)
    try:
        sys.stdout.reconfigure(line_buffering=True, errors="replace")   # 復華檔名有「證劵」這類 cp950 印不出的字
    except (AttributeError, ValueError):
        pass

    want = {s.strip().upper() for s in args.etf.split(",") if s.strip()}
    funds = [Fund(*f) for f in FUNDS if f[2] and (not want or f[0] in want)]
    if want - {f.etf_id for f in funds}:
        log(f"（略過尚未支援或不在清單的：{sorted(want - {f.etf_id for f in funds})}）")
    if not funds:
        raise SystemExit("沒有可抓的 ETF")

    trading_days, list_dates = [], {}
    if not args.dry_run:
        if not args.dsn:
            raise SystemExit("需要 --dsn 或環境變數 DATABASE_URL")
        import psycopg2
        conn = psycopg2.connect(args.dsn)
        with conn, conn.cursor() as cur:
            sync_funds(cur)
            cur.execute("SELECT min(list_date) FROM stock WHERE stock_id = ANY(%s)", ([f.etf_id for f in funds],))
            first = cur.fetchone()[0] or date(2025, 5, 1)
            cur.execute("SELECT DISTINCT trade_date FROM price_daily WHERE trade_date >= %s ORDER BY 1",
                        (first - timedelta(days=10),))
            trading_days = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT stock_id, list_date FROM stock WHERE stock_id = ANY(%s)", ([f.etf_id for f in funds],))
            list_dates = dict(cur.fetchall())
        conn.close()

    groups = {}
    for f in funds:
        groups.setdefault(f.adapter, []).append(f)
    mode = "dry-run（只抓最新）" if args.dry_run else ("回補歷史" if args.backfill else f"最新＋補近 {args.gap_days} 個交易日")
    log(f"主動式 ETF 持股：{len(funds)} 檔、{len(groups)} 家投信，{mode}")
    added, failed = {}, []
    with ThreadPoolExecutor(max_workers=len(groups)) as ex:
        futs = {a: ex.submit(run_group, a, fs, args, trading_days, list_dates) for a, fs in groups.items()}
        for a, fu in futs.items():
            try:
                added.update(fu.result())
            except Exception as e:                        # 一家投信出錯不影響其他家
                failed.append(a)
                log(f"  [{a}] 執行失敗：{type(e).__name__}: {str(e)[:200]}")

    if not args.dry_run:
        log(f"持股入庫完成：新增 {sum(len(v) for v in added.values())} 個 (ETF, 持股日)")
        if not args.no_flow:
            import psycopg2
            import build_etf_flow
            conn = psycopg2.connect(args.dsn)
            try:
                build_etf_flow.run(conn, etf_ids=[f.etf_id for f in funds], full=bool(args.backfill or args.since))
            finally:
                conn.close()
    if failed:
        sys.exit(1)                                       # 讓 nightly 標成失敗



if __name__ == "__main__":
    main()
