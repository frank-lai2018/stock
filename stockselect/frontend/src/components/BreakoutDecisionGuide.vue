<script setup>
// 突破決策頁最下方的欄位與規則說明。
// 內容照 backend/app/breakout_rank.py（五面向配分、硬條件、結論）、swings.py（型態偵測）和
// backtest_patterns.py（型態回測）寫；那幾支的門檻或配分改了，這裡要跟著改。字級沿用各頁「欄位說明」的 24px。
import { ref } from 'vue'

const open = ref(['read', 'cols', 'decision', 'score', 'plan', 'check', 'patterns', 'filters'])

const COLS = [
  ['結論', '依「總分＋硬條件」給的結論，規則見下方「結論規則」。'],
  ['總分', '五面向加總，滿分 100。'],
  ['股票', '代號、名稱、產業；點名稱進個股頁看 K 線。'],
  ['型態', '命中的多方波段型態，定義見下方「型態」。'],
  ['五面向', '趨＝趨勢與相對強度、破＝突破品質、盈＝盈餘動能、籌＝估值與籌碼、險＝風險與可執行性；斜線後是各面向滿分。'],
  ['量比', '突破當天成交量 ÷ 前 50 日平均量。'],
  ['離頸線', '目前收盤比頸線高幾 %。超過 5% 標紅（追高）；負的代表已經跌回頸線下。'],
  ['RS', '相對強弱評等 0～100：近 3、6、12 個月的漲幅（近 3 個月算兩倍）在同類證券裡的百分位，≥ 70 屬強勢。'],
  ['參考進場', '目前收盤，不是突破當天的收盤。'],
  ['停損', '頸線 − 1 個 ATR；括號是停損距離占進場價的 %。'],
  ['目標・R/R', '目標＝型態的量測滿足價；R/R＝(目標 − 進場) ÷ (進場 − 停損)。'],
  ['型態20日回測', '這個型態過去在全市場所有突破事件的統計，不是這檔股票自己的：突破當天收盤買進、抱 20 個交易日，期間盤中跌 8% 就停損，已扣 0.6% 來回成本。超額＝平均報酬減同期加權指數；勝率＝扣成本後賺錢的事件比例；n＝事件數。'],
  ['EPS YoY', '最近一季 EPS 比去年同季的成長率。'],
  ['營收 YoY', '最近一個月營收比去年同月的成長率。'],
  ['PER 位階', '目前本益比在這檔股票近 3 年的百分位：0%＝最便宜、100%＝最貴。'],
  ['均額', '近 20 日平均成交金額（元）。'],
  ['檢查結果', '紅字＝沒通過的硬條件；黃字＝提醒；綠字「硬條件全數通過」＝沒有紅字。'],
  ['追蹤', '加入自選股。'],
]

const DECISIONS = [
  ['優先評估', 'success', '總分 ≥ 75，而且硬條件全部通過'],
  ['等待／觀察', 'warning', '總分 ≥ 65，最多 1 項硬條件沒過'],
  ['略過', 'info', '其他'],
]

const PLAN = [
  ['參考進場', '目前收盤。突破後已經漲一段的，進場價會比突破當天高。'],
  ['參考停損', '頸線 − 1 個 ATR。ATR＝近 14 日的平均真實波幅（每天最高減最低；有跳空時，改用和前一天收盤的差距）。所有型態用同一種算法，方便互相比較；實際停損可以依型態結構調整，例如放在 W 底第二個底的下方。'],
  ['目標', '量測滿足價＝突破價 ＋ 型態高度。底部型態的高度＝頸線 − 型態最低點；箱型＝箱頂 − 箱底；三角形、楔形＝型態起點上下緣的距離；旗形＝旗桿的漲幅。'],
  ['R/R', '(目標 − 進場) ÷ (進場 − 停損)。'],
]

const RED = [
  ['RS 評等未達 70', '相對強度不夠。'],
  ['突破量比未達 1.5', '型態本身的量能門檻是 1.3～1.5 倍，這裡再要求 1.5 倍以上。'],
  ['離頸線超過 5%，避免追價', '目前收盤已經比頸線高 5% 以上。'],
  ['目前已回到頸線下', '突破後又跌回頸線下，可能是假突破。'],
  ['參考停損超過 8%', '頸線 − 1 ATR 離目前收盤太遠。'],
  ['報酬風險比未達 2', '到目標的空間不到停損距離的 2 倍。'],
  ['已接近或超過量測目標', '目前收盤已經到達或超過目標價。'],
  ['無法計算離頸線距離／停損距離資料不足', '資料不夠算（例如 K 棒太少），或目前收盤已經跌破參考停損。'],
]
const YELLOW = [
  ['ETF 與個股基本面分數不可直接比較', 'ETF 沒有 EPS、營收，盈餘分數通常是 0。'],
  ['此型態 20 日回測中位數仍為負', '這個型態一半以上的事件 20 天後是虧的；就算平均報酬是正的，也是靠少數大漲撐起來。'],
]

// [名稱, 量能門檻, 定義]；順序＝掃描優先序（swings.DETECTORS → DETECTORS_CONT）
const PATTERNS = [
  ['W底／雙重底', '1.5 倍', '最近兩個低點差距 5% 以內、相隔 8～90 根；頸線＝兩底之間的最高點；頸線比底部高 8% 以上。'],
  ['三重底', '1.5 倍', '最近三個低點差距 5% 以內、各相隔 6～60 根；頸線＝兩次反彈的較高點；深度 ≥ 8%。'],
  ['頭肩底', '1.5 倍', '三個低點中間最低（比兩肩低 2% 以上）、兩肩差距 6% 以內、各相隔 5～60 根；頸線＝兩次反彈的較高點；深度 ≥ 8%。'],
  ['杯柄', '1.4 倍', '約 80 根的杯身（深度 ≥ 10%，右緣回到左緣的 93% 以上），加上右側最多 15 根的淺回檔（柄，回檔不超過杯深的 40%）；突破價＝柄的高點和杯右緣的較高者。'],
  ['圓弧底', '1.3 倍', '約 70 根的碗形寬底：最低點在中段、貼近底部的 K 棒占 25% 以上、右段比中段高；突破價＝左側杯口的高點；深度 ≥ 10%。'],
  ['V型反轉', '1.3 倍', '近 45 根內，先在 15 根內急跌 ≥ 12%，接著 15 根內急彈 ≥ 10%；突破價＝下跌起點的高點。'],
  ['矩形／箱型', '1.5 倍', '近 45 根的上下緣都接近水平、箱高 3%～20%；突破箱頂。'],
  ['上升三角', '1.4 倍', '近 50 根上緣水平、下緣的低點抬升；收盤突破上緣。'],
  ['下降三角', '—', '系統只判向下跌破（偏空），不會出現在這頁。'],
  ['對稱三角', '1.4 倍', '近 50 根上緣下壓、下緣抬升；這頁只列向上突破。'],
  ['旗形／三角旗', '1.4 倍', '旗桿（12 根內漲 ≥ 15%）之後，最近 18 根小幅整理；突破整理區的高點。'],
  ['楔形', '1.4 倍', '近 50 根上下緣都往下、而且越來越窄（下降楔，上緣下降得比下緣快）；收盤突破上緣。'],
]

const FILTERS = [
  ['只看個股／ETF', '證券類別。'],
  ['近 3 日／2 週／1 月', '突破要發生在最近 3／10／20 個交易日內。'],
  ['均額', '近 20 日平均成交金額的門檻。另外只掃「母體」：上市櫃滿 60 個交易日、20 日均額 ≥ 500 萬。'],
  ['全部結論／最低分', '只篩選目前的結果，不會重新掃描。點上方的統計卡也能依結論篩選。'],
  ['指定個股分析', '只分析這一檔，不管證券類別、流動性和母體限制。'],
]
</script>

<template>
  <el-card shadow="never" class="guide">
    <template #header>
      <span class="guide-title">📖 欄位與規則說明</span>
      <span class="guide-sub">規則評分 v1・紅漲綠跌・價格都是還原價</span>
    </template>
    <el-collapse v-model="open" class="guide-collapse">
      <el-collapse-item name="read" title="怎麼讀這張表">
        <ul>
          <li><b>先找型態，再排序。</b>掃描最近幾天「收盤帶量突破頸線」的多方型態（底部反轉和整理突破），再用趨勢、突破品質、盈餘、估值籌碼、風險五個面向打分數；另外用硬條件擋掉追高、停損太遠、報酬風險比不夠的標的。</li>
          <li><b>每檔只列一個型態：</b>照固定順序找，第一個命中的就用（順序就是下方「型態」表的順序）。</li>
          <li><b>排序：</b>結論 → 總分 → RS。</li>
          <li><b>型態是程式自動找的，一定有假訊號。</b>分數只用來比較同一批候選，不是買進指令，也不是報酬預測；下單前要看 K 線圖確認結構、隔天跳空和自己的產業持股。</li>
          <li><b>價格都是還原價：</b>最新一根等於實際股價，除權息以前的舊價格會往下調。</li>
        </ul>
      </el-collapse-item>

      <el-collapse-item name="cols" title="表格欄位">
        <dl class="defs">
          <template v-for="[k, v] in COLS" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="decision" title="結論規則">
        <div class="table-wrap">
          <table class="rules">
            <thead><tr><th>結論</th><th>條件</th></tr></thead>
            <tbody>
              <tr v-for="[name, type, cond] in DECISIONS" :key="name">
                <td><el-tag :type="type" effect="dark">{{ name }}</el-tag></td><td>{{ cond }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p>硬條件就是下方「檢查結果」的紅字。高分不能蓋掉追高、停損太遠或報酬風險比不夠，所以另外判斷。</p>
      </el-collapse-item>

      <el-collapse-item name="score" title="五面向怎麼算（滿分 100）">
        <h4>趨：趨勢與相對強度（30）</h4>
        <ul>
          <li>RS 評等 × 0.15（RS 100 ＝ 15 分）。</li>
          <li><b>符合「趨勢範本」加 10 分</b>：Minervini 的 8 個條件全部成立 — 站上 150 日和 200 日均線、150 日線在 200 日線之上、200 日線比一個月前高、50 日線在 150 日和 200 日線之上、站上 50 日線、比 52 週低點高 30% 以上、距 52 週高點 25% 以內、RS ≥ 70。</li>
          <li><b>12-1 動能</b>（12 個月前到 1 個月前的漲幅，略過最近一個月）：−10% 以下 0 分、+40% 以上 5 分，中間按比例。</li>
        </ul>

        <h4>破：突破品質（25）</h4>
        <ul>
          <li><b>量比</b>：1.3 倍以下 0 分、2.5 倍以上 8 分，中間按比例。</li>
          <li><b>突破前收斂</b>：近 15 日收盤最高和最低的差距 ÷ 目前收盤，≤ 8% 加 7 分、≤ 12% 加 5 分、≤ 18% 加 3 分。整理得越緊，突破越乾淨。</li>
          <li><b>離頸線</b>：0～3% 加 5 分、3～5% 加 3 分；超過 5% 或跌回頸線下不加分。</li>
          <li><b>型態歷史超額</b>：20 日回測的平均超額先乘上 n ÷ (n + 500) 打折，樣本少的型態才不會因為偶然的好成績排到前面；打折後 −0.5% 以下 0 分、+1% 以上 5 分。</li>
        </ul>

        <h4>盈：盈餘動能（20）</h4>
        <ul>
          <li><b>EPS 年增率逐季擴大加 7 分</b>：本季的年增率比上一季的年增率高，而且是正的。</li>
          <li><b>EPS 連兩季成長加 5 分</b>：本季 ＞ 上一季 ＞ 前一季，而且本季是正的。</li>
          <li><b>最新月營收年增</b>：0% 以下 0 分、30% 以上 5 分，中間按比例。</li>
          <li><b>毛利率不低於上一季加 3 分。</b></li>
        </ul>

        <h4>籌：估值與籌碼（10）</h4>
        <ul>
          <li>PER 位階 ≤ 30% 加 4 分、≤ 50% 加 2 分。</li>
          <li>千張大戶的持股比例比 4 週前高，加 3 分。</li>
          <li>三大法人近 20 日合計買超，加 3 分（只看買超還是賣超，不看張數）。</li>
        </ul>

        <h4>險：風險與可執行性（15）</h4>
        <ul>
          <li>R/R ≥ 3 加 8 分、≥ 2 加 6 分、≥ 1.5 加 3 分。</li>
          <li>停損距離 ≤ 5% 加 4 分、≤ 8% 加 3 分、≤ 10% 加 1 分。</li>
          <li>20 日均額 ≥ 1 億加 3 分、≥ 5 千萬加 2 分、≥ 2 千萬加 1 分。</li>
        </ul>
        <p>每個面向都有上限，超過就以滿分計。</p>
      </el-collapse-item>

      <el-collapse-item name="plan" title="風險計畫">
        <dl class="defs">
          <template v-for="[k, v] in PLAN" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="check" title="檢查結果：紅字與黃字">
        <ul class="checks">
          <li v-for="[k, v] in RED" :key="k"><span class="blocker">{{ k }}</span>：{{ v }}</li>
          <li v-for="[k, v] in YELLOW" :key="k"><span class="note">{{ k }}</span>：{{ v }}</li>
        </ul>
      </el-collapse-item>

      <el-collapse-item name="patterns" title="型態">
        <p>共同條件：最近幾個交易日內（上方的近 3 日／近 2 週／近 1 月），某一天收盤由頸線下方站上頸線，而且那天的成交量 ≥ 前 50 日平均量 × 量能門檻。表格順序就是掃描順序。</p>
        <div class="table-wrap">
          <table class="rules">
            <thead><tr><th>型態</th><th>量能門檻</th><th>定義</th></tr></thead>
            <tbody>
              <tr v-for="[name, vol, desc] in PATTERNS" :key="name">
                <td class="pname">{{ name }}</td><td class="num">{{ vol }}</td><td>{{ desc }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </el-collapse-item>

      <el-collapse-item name="filters" title="上方篩選條件">
        <dl class="defs">
          <template v-for="[k, v] in FILTERS" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<style scoped>
.guide { margin-top: 14px; }
.guide-title { font-size: 26px; font-weight: 700; color: #303133; }
.guide-sub { margin-left: 12px; font-size: 18px; color: #909399; }
.guide-collapse {
  --el-collapse-header-height: 60px;
  --el-collapse-header-font-size: 24px;
  --el-collapse-content-font-size: 24px;
  --el-collapse-content-text-color: #444;
}
.guide-collapse :deep(.el-collapse-item__header) { font-weight: 650; }
.guide-collapse :deep(.el-collapse-item__content) { line-height: 1.6; padding-bottom: 18px; }
h4 { margin: 14px 0 6px; font-size: 24px; color: #303133; }
p { margin: 6px 0; }
ul { margin: 6px 0; padding-left: 1.4em; }
li { margin: 4px 0; }
.defs { display: grid; grid-template-columns: 240px 1fr; gap: 8px 20px; margin: 6px 0; }
.defs dt { font-weight: 600; color: #303133; }
.defs dd { margin: 0; color: #555; }
.table-wrap { overflow-x: auto; }
.rules { border-collapse: collapse; margin: 6px 0 10px; }
.rules th, .rules td { border: 1px solid #dcdfe6; padding: 8px 14px; text-align: left; vertical-align: middle; }
.rules th { background: #f5f7fa; font-weight: 600; }
.rules td.num { text-align: center; white-space: nowrap; }
.rules td.pname { font-weight: 600; white-space: nowrap; }
.rules :deep(.el-tag) { font-size: 20px; height: 36px; padding: 0 12px; }
.checks { list-style: none; padding-left: 0; }
.blocker { color: #f56c6c; font-weight: 600; }
.note { color: #a77700; font-weight: 600; }
</style>
