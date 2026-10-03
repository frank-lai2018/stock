"""us_theme_defs.py — 美台題材對照：L3 台股題材 → 美股代表股／ETF。要增減對照就改這裡。

欄位：
  BENCHMARK   算超額報酬用的美股大盤（SPY）
  INDICATORS  隔夜常看的指標，只抓價、不屬於任何題材籃子
  US_THEMES   題材代碼（同 theme_defs.py 的 code）→ {"symbols": [(Yahoo 代號, 顯示名稱)], "note": 對照理由}
              symbols 空清單＝美股沒有對應，這個題材在台股走自己的路
⚠️ 對照名單是 Claude 依公開市場資訊提供（2026-10）：挑「美股同業」或「台廠的主要客戶／需求來源」中最具代表性的幾檔。
   同一檔美股可以出現在多個題材（例如 VRT 同時代表散熱與電源）。
   每季用 analyze_us_tw_themes.py 看跟隨度：跟隨度低代表對照選錯，或台股這個題材另有自己的驅動力。
流程：fetch_us_prices.py 抓這裡列到的代號 → build_us_theme_daily.py 同步籃子到 us_theme_member 並算 us_theme_daily。
"""

BENCHMARK = "SPY"

INDICATORS = [
    ("SPY", "S&P 500 ETF"),
    ("QQQ", "Nasdaq 100 ETF"),
    ("^SOX", "費城半導體指數"),
]

US_THEMES = {
    "ai_server": {
        "symbols": [("NVDA", "輝達"), ("SMCI", "美超微"), ("DELL", "戴爾"), ("HPE", "慧與"), ("CLS", "Celestica")],
        "note": "GPU 需求來源＋伺服器品牌／代工同業",
    },
    "server_parts": {
        "symbols": [("SMCI", "美超微"), ("DELL", "戴爾"), ("CLS", "Celestica")],
        "note": "滑軌、機殼、BMC 在美股沒有同業，用伺服器出貨商代表需求",
    },
    "asic_ip": {
        "symbols": [("AVGO", "博通"), ("MRVL", "邁威爾"), ("ARM", "安謀"), ("SNPS", "新思"), ("CDNS", "益華")],
        "note": "客製化 ASIC 同業＋矽智財／EDA",
    },
    "chip_test": {
        "symbols": [("FORM", "FormFactor"), ("TER", "泰瑞達"), ("COHU", "Cohu"), ("AEHR", "Aehr")],
        "note": "探針卡、測試機、燒機設備同業",
    },
    "ai_power": {
        "symbols": [("VRT", "維諦"), ("ETN", "伊頓"), ("VICR", "Vicor"), ("MPWR", "芯源")],
        "note": "資料中心供電與電源模組",
    },
    "cooling": {
        "symbols": [("VRT", "維諦"), ("MOD", "Modine"), ("NVT", "nVent")],
        "note": "資料中心液冷（CDU、冷卻分配）",
    },
    "cpo": {
        "symbols": [("COHR", "Coherent"), ("LITE", "Lumentum"), ("FN", "Fabrinet"), ("AAOI", "應用光電"),
                    ("CIEN", "Ciena"), ("CRDO", "Credo")],
        "note": "光收發模組、雷射元件、光通訊設備與高速連接",
    },
    "defense": {
        "symbols": [("ITA", "iShares 航太國防 ETF"), ("LMT", "洛克希德"), ("RTX", "RTX"), ("NOC", "諾斯洛普"),
                    ("AVAV", "AeroVironment"), ("KTOS", "Kratos")],
        "note": "國防主承包商＋無人機",
    },
    "edge_ai": {
        "symbols": [("QCOM", "高通"), ("NXPI", "恩智浦"), ("AMBA", "Ambarella"), ("LSCC", "Lattice")],
        "note": "裝置端 AI 晶片；工業電腦在美股沒有同業",
    },
    "ev_tesla": {
        "symbols": [("TSLA", "特斯拉"), ("RIVN", "Rivian"), ("DRIV", "Global X 電動車 ETF")],
        "note": "特斯拉本身＋電動車族群",
    },
    "foplp": {
        "symbols": [("AMKR", "艾克爾"), ("GLW", "康寧"), ("ONTO", "Onto Innovation"), ("KLIC", "K&S")],
        "note": "先進封裝代工、玻璃基板、面板級封裝設備",
    },
    "leo_sat": {
        "symbols": [("ASTS", "AST SpaceMobile"), ("RKLB", "Rocket Lab"), ("IRDM", "Iridium"), ("GSAT", "Globalstar"),
                    ("UFO", "Procure 太空 ETF")],
        "note": "低軌衛星營運與發射",
    },
    "memory": {
        "symbols": [("MU", "美光"), ("WDC", "威騰"), ("SNDK", "SanDisk"), ("STX", "希捷")],
        "note": "DRAM／NAND／HBM 原廠與儲存；SanDisk 2025-02 才分拆上市，歷史較短",
    },
    "optics": {
        "symbols": [("AAPL", "蘋果")],
        "note": "大立光、玉晶光的主要客戶；只有一檔，雜訊大",
    },
    "passive": {
        "symbols": [("VSH", "Vishay")],
        "note": "美股唯一較純的被動元件股；風向球村田在日股（收盤時間與台股重疊，未納入）",
    },
    "pcb": {
        "symbols": [("TTMI", "TTM Technologies"), ("SANM", "Sanmina")],
        "note": "美系 PCB／電子製造同業；ABF 載板同業在日本（揖斐電），未納入",
    },
    "pcb_material": {
        "symbols": [("ROG", "Rogers")],
        "note": "高頻高速基板材料；只有一檔，雜訊大",
    },
    "power_grid": {
        "symbols": [("GEV", "GE Vernova"), ("PWR", "Quanta Services"), ("ETN", "伊頓"), ("HUBB", "Hubbell"),
                    ("GRID", "First Trust 智慧電網 ETF")],
        "note": "電網設備、輸配電工程",
    },
    "power_semi": {
        "symbols": [("ON", "安森美"), ("DIOD", "Diodes"), ("AOSL", "萬國半導體")],
        "note": "MOSFET、二極體、功率元件同業",
    },
    "robot": {
        "symbols": [("BOTZ", "Global X 機器人 ETF"), ("ISRG", "直覺手術"), ("ROK", "羅克韋爾"), ("SYM", "Symbotic")],
        "note": "機器人與自動化",
    },
    "semi_material": {
        "symbols": [("ENTG", "Entegris")],
        "note": "半導體材料與特用化學；只有一檔，雜訊大",
    },
    "si_wafer": {
        "symbols": [],
        "note": "美股沒有矽晶圓同業（信越、SUMCO 在日股）",
    },
    "solar": {
        "symbols": [("TAN", "Invesco 太陽能 ETF"), ("FSLR", "第一太陽能"), ("ENPH", "Enphase"), ("NXT", "Nextracker")],
        "note": "太陽能模組、逆變器、追日系統",
    },
    "tsmc": {
        "symbols": [("TSM", "台積電 ADR"), ("SMH", "VanEck 半導體 ETF"), ("AMAT", "應材"), ("LRCX", "科林研發"),
                    ("KLAC", "科磊"), ("ASML", "艾司摩爾")],
        "note": "台積電 ADR＋半導體設備（CoWoS 擴產的設備需求）",
    },
}


def all_symbols():
    """要抓價的全部代號（指標＋各題材籃子，去重、保持順序）。"""
    seen, out = set(), []
    for sym, _ in INDICATORS + [s for t in US_THEMES.values() for s in t["symbols"]]:
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out
