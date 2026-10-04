<script setup>
// 週線突破頁最下方的欄位與規則說明。
// 內容照 backend/app/weekly_breakout.py（週線趨勢、壓力線、日線進場）和 decision_center.py 的週線突破篩選條件寫；
// 那幾支的門檻改了，這裡要跟著改。字級沿用各頁「欄位說明」的 24px。
import { ref } from 'vue'

const open = ref(['read', 'cols', 'decision', 'weekly', 'daily', 'plan', 'notes', 'filters'])

const COLS = [
  ['結論', '依下方「結論規則」判斷：可進場、等回測、接近壓力、待週收盤；指定個股分析時也會列出突破失敗、不成立。'],
  ['股票', '代號、名稱、產業、RS；「趨勢模板」標籤＝日線的趨勢模板也成立（50／150／200 日線多頭排列、RS ≥ 70），只是參考，這個策略不要求。'],
  ['週線趨勢', '週收＝最近一個收完的週收盤；30週線後面的 % 是週收離 30 週線多遠；「30週線 4 週」＝30 週線比 4 週前高或低幾 %；最後一行是 10 週線在 30 週線上或下。'],
  ['週線突破', '壓力線＝突破那週之前 52 週裡最高的週收盤，括號是那個高點所在的週；突破週＝週收盤站上壓力線的那一週；整理＝壓力線那週到突破週隔了幾週；最深回檔＝整理期間最低價比壓力線低幾 %；週量比＝突破週的日均成交量 ÷ 前 10 週的日均成交量。接近壓力的列，整理週數是壓力線那週到最近一個收完的週。'],
  ['日線進場', '收盤＝最新一天收盤；離壓力線超過 5% 標紅；買點區＝壓力線～壓力線 +5%；紅字＝還不能進場的原因，綠字＝可進場的理由。'],
  ['交易計畫', '參考進場＝最新收盤；停損＝壓力線 − 1 個 ATR（括號是停損距離占進場價的 %）；追價上限＝壓力線 +5%，隔天開盤高於這個價就不追。'],
  ['提醒', '黃字，不影響結論：量能沒放大、10 週線還在 30 週線下、整理期回檔很深、上方還有更高的 52 週最高價。'],
  ['基本面', 'EPS YoY＝最近一季 EPS 比去年同季；營收 YoY＝最近一個月營收比去年同月；均額＝近 20 日平均成交金額（元）。'],
  ['追蹤', '加入自選股。'],
]

const DECISIONS = [
  ['可進場', 'success', 'dark', '週線已突破，而且今天收盤在買點區（壓力線～+5%），今天又是：突破那週的週收盤，或收盤高於前一天的最高價（轉強）。'],
  ['等回測', 'warning', 'plain', '週線已突破，但還沒到日線進場點：離壓力線超過 5%（等拉回）、跌回壓力線下（等收回）、或在買點區但今天沒轉強。'],
  ['接近壓力', 'info', 'plain', '週線趨勢向上、整理已滿 6 週，收盤離壓力線不到 5%，還沒突破。'],
  ['待週收盤', 'info', 'plain', '這週還沒收完，但目前收盤已經站上壓力線；要等週五收盤確認才算突破。'],
  ['突破失敗', 'danger', 'plain', '突破後有一天收盤跌破「壓力線 − 1 ATR」。只在指定個股分析時列出。'],
  ['不成立', 'info', 'plain', '週線趨勢不對、還在一路創新高（沒有整理）、或離壓力線超過 5%。只在指定個股分析時列出。'],
]

const WEEKLY = [
  ['週 K 怎麼來', '用日 K 合成：週一到週五為一週，開盤＝第一天開盤、收盤＝最後一天收盤、最高／最低＝這週的最高／最低。'],
  ['只用收完的週', '最新一天是週五，或已經進入下一週，那一週才算收完。週五放假時（例如中秋），要到下週一才會判定那週收完，晚一天。'],
  ['週線趨勢', '週收盤在 30 週線上，而且 30 週線比 4 週前高（30 週線往上）。30 週約等於日線的 150 日線。'],
  ['壓力線', '前 52 週（約一年）裡最高的週收盤。用收盤不用最高價：長上影線常是一兩天的急拉，收盤比較能代表賣壓所在。'],
  ['整理至少 6 週', '壓力線那週到突破週至少隔 6 週，代表股價在壓力線下整理了一段時間；一路創新高的股票不算週線突破。'],
  ['突破', '週收盤高於壓力線，而且那週的週線趨勢成立。突破後約 4 週內找日線進場點，超過就不再列入。'],
]

const DAILY = [
  ['買點區', '壓力線～壓力線 +5%。突破後漲太遠的不追，等拉回到這個區間。'],
  ['轉強', '收盤高於前一天的最高價。在買點區裡出現轉強才進場，避免接到還在往下的刀。'],
  ['突破週收在買點區', '突破那週的週收盤（通常是週五）本來就在買點區，當天就算可進場，隔天開盤買。'],
  ['突破失敗', '突破後任何一天收盤跌破「壓力線 − 1 ATR」，這次突破就作廢。ATR＝近 14 日平均真實波幅。'],
]

const PLAN = [
  ['這頁的計畫', '參考進場＝最新收盤；停損＝壓力線 − 1 ATR；不設目標價；隔天開盤高於追價上限（壓力線 +5%）就不買。'],
  ['今日決策中心', '選篩選條件「週線突破（實驗）」時，只收這頁的「可進場」，再套用大盤站上 60 日線、產業上限、總持股與總風險；停損改成實際成交價下 8%，出場依模式：動能模式第 20 個交易日收盤，趨勢持有跌破 50 日線隔天出場、最多 60 日。'],
  ['回測', '「今日決策中心 → 策略研究」有「動能20日・週線突破（實驗）」和「趨勢持有60日・週線突破（實驗）」兩組，和其他篩選條件用同一套成交、成本與資金限制比較。'],
]

const FILTERS = [
  ['只看個股／ETF', '證券類別。'],
  ['均額', '近 20 日平均成交金額的門檻。另外只掃「母體」：上市櫃滿 60 個交易日、20 日均額 ≥ 500 萬。'],
  ['全部結論', '只篩選目前的結果，不會重新掃描。點上方的統計卡也能篩選，再點一次取消。'],
  ['指定個股分析', '只分析這一檔，不管證券類別、流動性和母體；不成立也會列出原因。'],
]
</script>

<template>
  <el-card shadow="never" class="guide">
    <template #header>
      <span class="guide-title">📖 欄位與規則說明</span>
      <span class="guide-sub">週線突破（實驗）・紅漲綠跌・價格都是還原價</span>
    </template>
    <el-collapse v-model="open" class="guide-collapse">
      <el-collapse-item name="read" title="怎麼讀這張表">
        <ul>
          <li><b>先看週線，再看日線。</b>第一步確認週線趨勢向上；第二步找週收盤突破整理區壓力線的股票；第三步在日線上等股價回到壓力線附近、出現轉強，才算進場點。</li>
          <li><b>排序：</b>結論（可進場 → 等回測 → 接近壓力）→ RS。</li>
          <li><b>規則是待驗證的起始參數。</b>回測與其他篩選條件的比較在「今日決策中心 → 策略研究」；「可進場」不是買進指令，下單前要看 K 線圖、隔天跳空和自己的產業持股。</li>
          <li><b>價格都是還原價：</b>最新一根等於實際股價，除權息以前的舊價格會往下調，所以壓力線可能比看原始價圖畫的線低一點。</li>
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
              <tr v-for="[name, type, effect, cond] in DECISIONS" :key="name">
                <td><el-tag :type="type" :effect="effect">{{ name }}</el-tag></td><td>{{ cond }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </el-collapse-item>

      <el-collapse-item name="weekly" title="第一步、第二步：週線趨勢與突破">
        <dl class="defs">
          <template v-for="[k, v] in WEEKLY" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="daily" title="第三步：日線進場點">
        <dl class="defs">
          <template v-for="[k, v] in DAILY" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="plan" title="交易計畫與回測">
        <dl class="defs">
          <template v-for="[k, v] in PLAN" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="notes" title="黃字提醒">
        <ul class="checks">
          <li><span class="note">突破週日均量只有前 10 週的 x 倍</span>：低於 1.2 倍就提醒。台股漲停鎖住時量放不出來，所以只提醒、不擋。</li>
          <li><span class="note">10 週線仍在 30 週線下</span>：剛從回檔回升，中期均線還沒翻多。</li>
          <li><span class="note">整理期最深回檔超過 35%</span>：深回檔後的 V 型回升，上方套牢賣壓重，風險較高。</li>
          <li><span class="note">上方 52 週最高價</span>：壓力線是用週收盤算的；如果之前有長上影線，上面還有更高的價位要過。</li>
        </ul>
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
ul { margin: 6px 0; padding-left: 1.4em; }
li { margin: 4px 0; }
.defs { display: grid; grid-template-columns: 240px 1fr; gap: 8px 20px; margin: 6px 0; }
.defs dt { font-weight: 600; color: #303133; }
.defs dd { margin: 0; color: #555; }
.table-wrap { overflow-x: auto; }
.rules { border-collapse: collapse; margin: 6px 0 10px; }
.rules th, .rules td { border: 1px solid #dcdfe6; padding: 8px 14px; text-align: left; vertical-align: middle; }
.rules th { background: #f5f7fa; font-weight: 600; }
.rules :deep(.el-tag) { font-size: 20px; height: 36px; padding: 0 12px; }
.checks { list-style: none; padding-left: 0; }
.note { color: #a77700; font-weight: 600; }
</style>
