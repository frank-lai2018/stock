<script setup>
// 型態＋裸 K 決策頁最下方的欄位與規則說明。
// 內容照 backend/app/price_action.py（裸 K 評分、狀態、決策）和 routers/patterns.py（篩選）寫；
// 那兩支的門檻或配分改了，這裡要跟著改。字級沿用各頁「欄位說明」的 24px。
// watchlist：自選股的「裸K決策」檢視用，預設收起，並換掉只適用決策頁的說明（篩選、排序、最後一欄）。
import { computed, ref } from 'vue'

const props = defineProps({
  watchlist: { type: Boolean, default: false },
})
const shown = ref(!props.watchlist)
const toggle = () => { if (props.watchlist) shown.value = !shown.value }

const open = ref(['read', 'cols', 'state', 'decision', 'score', 'plan', 'check', 'fakey', 'kbar', 'filters'])

const COLS = computed(() => [
  ['決策', '依「狀態＋分數＋紅字」給的結論，規則見下方「決策規則」。'
    + (props.watchlist ? '「無訊號」＝最近幾根 K 棒沒有可評估的裸 K 訊號。' : '')],
  ['分數', '五層評分加總，滿分 100。'],
  ['股票', '代號、名稱、產業；點名稱進個股頁看 K 線。'],
  ['訊號', '「多方／空方・K 棒型態」，紅字＝多方、綠字＝空方。標籤是訊號狀態；後面是訊號 K 的日期和距今幾根（0 根前＝最新一根）；已觸發的會多一行觸發日。'],
  ['波段型態', '近期收盤帶量突破頸線的多方波段型態，附突破日和頸線價。「未限制」＝上方波段型態選了「不限」。'],
  ['結構與位置', '第一行是市場結構和「區間」：訊號 K 收盤在前 60 根最高與最低之間的位置，0%＝最低、100%＝最高。標籤是位置加分的原因。最後一行是訊號當時的支撐與壓力。'],
  ['五層評分', '構＝結構、位＝位置、K＝K 棒品質、確＝確認、險＝風險；斜線後是各層滿分。'],
  ['交易計畫', '觸發價、進場價、停損價（括號是停損距離占進場價的 %，超過 8% 標紅）、目標價和 R/R。小字是目標從哪裡來，以及目前收盤。'],
  ['成長過濾', '最近一季 EPS、月營收連續月增幾個月、季營收連續季增幾季、最近一季毛利率和連續季增幾季；「營收月」是最新營收資料的月份。只用來篩選，不算進分數。'],
  ['檢查結果', '紅字＝會擋下決策的問題；黃字＝提醒；綠字「可依觸發條件執行」＝沒有紅字。'],
  ['流動性', '近 20 日平均成交金額（元）。'],
  props.watchlist ? ['操作', '✕ 把這檔移出本分類。'] : ['追蹤', '加入自選股。'],
])

const STATES = [
  ['等待突破', '訊號已經形成，還沒觸發、也還沒碰到停損，而且還在「N 根內觸發」的期限內。'],
  ['已觸發', '多方：之後有 K 棒的高點超過訊號 K 高點。空方：之後有 K 棒的低點跌破訊號 K 低點。'],
  ['觸發前失效', '還沒觸發就先碰到停損。同一根 K 棒同時碰到觸發價和停損時，保守算失效。'],
  ['觸發後停損', '觸發之後又碰到停損。'],
  ['訊號過期', '超過「N 根內觸發」的期限還沒觸發。'],
]

const DECISIONS = [
  ['優先評估', 'success', '已觸發、分數 ≥ 75、沒有紅字', '條件最完整的一群，仍要看圖確認。'],
  ['等待確認', 'warning', '等待突破、分數 ≥ 65、沒有紅字', '還沒觸發：可以把觸發價設成條件單，突破了才進場。'],
  ['觸發觀察', 'primary', '已觸發、分數 ≥ 65、最多 1 個紅字', '先看紅字是什麼，例如 R/R 不足或已經追高。'],
  ['略過', 'info', '其他', '—'],
]

const STRUCTURES = [
  ['HH-HL 多頭', '最近兩個高點、兩個低點都墊高', 30, 10],
  ['區間整理', '高點和低點方向不一致', 20, 20],
  ['結構未明', '轉折點不到兩個', 15, 15],
  ['LH-LL 空頭', '最近兩個高點、兩個低點都降低', 10, 30],
]

const PLAN = [
  ['觸發', '多方＝訊號 K 高點，之後漲過它才算確認；空方＝訊號 K 低點，之後跌破才算。'],
  ['進場', '已觸發：觸發價；如果那天跳空開在觸發價之外，改用開盤價。還沒觸發：先用觸發價。'],
  ['停損', '訊號 K 的另一端：多方＝訊號 K 低點、空方＝訊號 K 高點。'],
  ['目標', '多方：進場價上方最近的轉折高點或壓力（下一道壓力）。空方：進場價下方最近的轉折低點或支撐（下一道支撐）。前方沒有結構位時，用進場價加減 2 倍風險（2R推估）。'],
  ['R/R', '多方＝(目標 − 進場) ÷ (進場 − 停損)；空方＝(進場 − 目標) ÷ (停損 − 進場)。'],
  ['現價', '最新一根的收盤。'],
]

const RED = [
  ['遠離有效支撐／壓力，位置不佳', '位置分不到 8 分。'],
  ['訊號K停損距離超過 8%', '訊號 K 太長，停損太遠。'],
  ['到下一結構位的 R/R 未達 2', '到目標的空間不到停損距離的 2 倍。'],
  ['觸發前已先失效／觸發後已碰停損／等待超過有效期限', '對應上面的訊號狀態。'],
  ['觸發後已走超過 3%，避免追價', '已觸發，而且目前收盤比進場價往有利方向多走了 3% 以上。'],
]
const YELLOW = [
  ['前方無明確結構位，目標暫以 2R 推估', '進場價前方找不到轉折點，目標是用 2 倍風險推算的。'],
]

// [名稱, 方向, 定義]；前 15 個是 patterns.py 的經典陰陽線，後 10 個是 price_action.py 的裸 K 延伸型態
const KBARS = [
  ['錘子線', 'bull', '實體小、下影線至少是實體 2 倍、上影線 ≤ 振幅 30%，出現在下跌之後（前 20 根收盤往下）。'],
  ['吊人線', 'bear', '外型同錘子線，但出現在上漲之後。'],
  ['倒錘線', 'bull', '實體小、上影線至少是實體 2 倍、下影線 ≤ 振幅 30%，出現在下跌之後。'],
  ['流星線', 'bear', '外型同倒錘線，但出現在上漲之後。'],
  ['十字星', 'neutral', '開盤和收盤幾乎一樣（實體 ≤ 振幅 10%），多空不明。'],
  ['多頭吞噬', 'bull', '昨天黑 K、今天紅 K，今天的實體完全包住昨天的實體。'],
  ['空頭吞噬', 'bear', '昨天紅 K、今天黑 K，今天的實體完全包住昨天的實體。'],
  ['貫穿線', 'bull', '下跌中，昨天黑 K；今天開在昨天低點之下，收紅並收過昨天實體的一半。'],
  ['烏雲罩頂', 'bear', '上漲中，昨天紅 K；今天開在昨天高點之上，收黑並跌破昨天實體的一半。'],
  ['多頭孕線', 'bull', '昨天長黑、今天小紅，今天的實體在昨天的實體裡面。'],
  ['空頭孕線', 'bear', '昨天長紅、今天小黑，今天的實體在昨天的實體裡面。'],
  ['晨星', 'bull', '下跌中的三根：長黑 → 跳空往下的小實體 → 紅 K 收過第一根實體的一半。'],
  ['夜星', 'bear', '上漲中的三根：長紅 → 跳空往上的小實體 → 黑 K 跌破第一根實體的一半。'],
  ['紅三兵', 'bull', '連三根紅 K，收盤一根比一根高，開盤都落在前一根的實體裡。'],
  ['黑三鴉', 'bear', '連三根黑 K，開盤和收盤一根比一根低。'],
  ['多方 Pin Bar', 'bull', '下影線至少是實體 2 倍、上影線 ≤ 振幅 25%，收在 K 棒上方 35% 以內。比錘子線寬鬆、不看前面的趨勢；已經算成錘子線就不重複列。'],
  ['空方 Pin Bar', 'bear', '上影線至少是實體 2 倍、下影線 ≤ 振幅 25%，收在 K 棒下方 35% 以內；已經算成流星線就不重複列。'],
  ['內包線', 'neutral', '今天的高點和低點都在昨天的範圍內，代表整理、蓄勢。'],
  ['多方外包線', 'bull', '今天的高點比昨天高、低點比昨天低，收在振幅上方 40% 以內。'],
  ['空方外包線', 'bear', '同上，但收盤不在振幅上方 40% 以內。'],
  ['多方 Fakey', 'bull', '母線 → 內包線 → 第三根先跌破母線低點、收盤又站回母線低點以上（見上方「Fakey」）。'],
  ['空方 Fakey', 'bear', '母線 → 內包線 → 第三根先衝過母線高點、收盤又跌回母線高點以下（見上方「Fakey」）。'],
  ['兩棒多方反轉', 'bull', '黑 K 接紅 K，兩根的低點相近（差距 ≤ 較長那根振幅的 25%），紅 K 收過黑 K 實體的中點。'],
  ['兩棒空方反轉', 'bear', '紅 K 接黑 K，兩根的高點相近，黑 K 跌破紅 K 實體的中點。'],
  ['NR7 窄幅整理', 'neutral', '今天的振幅是最近 7 根裡最小的，常出現在大波動之前。'],
]

const FILTERS = [
  ['只看個股／ETF', '證券類別。'],
  ['均額', '近 20 日平均成交金額的門檻。另外只掃「母體」：上市櫃滿 60 個交易日、20 日均額 ≥ 500 萬。'],
  ['近 N 根訊號', '訊號 K 必須是最近 N 根之一（0 根前＝最新一根）。'],
  ['N 根內觸發', '訊號形成後的有效期限，超過還沒觸發就是「訊號過期」。'],
  ['波段型態', '不限＝只看裸 K；任何多方型態＝近期必須有底部反轉或整理突破的多方型態；也可以指定一種。下降三角只判向下跌破，所以不在清單裡。'],
  ['近 N 日突破', '波段型態的突破要發生在最近 N 個交易日內。'],
  ['單季 EPS ≥', '最近一季 EPS 的下限。'],
  ['月營收連增', '最近 N 個月，每個月營收都比上個月高。是跟上個月比、不是跟去年同月比，所以淡旺季會影響。'],
  ['季營收／毛利率連增', '最近 N 季，每季都比上一季高。財報沒有逐月毛利率，所以毛利率只做季。'],
  ['指定個股分析', '只分析這一檔：不管母體、流動性、證券類別和基本面門檻；波段型態只標示、不過濾；裸 K 的觀察窗照舊。'],
  ['結論／狀態／多空', '這三個下拉和「最低分」只篩選目前的結果，不會重新掃描。點上方的統計卡也能依決策篩選。'],
]
const WL_FILTERS = [
  ['近 N 根訊號', '訊號 K 必須是最近 N 根之一（0 根前＝最新一根）。'],
  ['N 根內觸發', '訊號形成後的有效期限，超過還沒觸發就是「訊號過期」。'],
  ['多空', '只看多方或空方訊號。'],
  ['統計標籤', '點一下只看那一種決策，再點一次取消。'],
  ['空方訊號提醒', '只算決策不是「略過」的空方訊號；點「只看這些」列出那幾檔。'],
  ['不套的條件', '跟「指定個股分析」一樣，不管母體、流動性、證券類別和基本面門檻；波段型態只標示、不過濾。'],
]
const filterRows = computed(() => (props.watchlist ? WL_FILTERS : FILTERS))
</script>

<template>
  <el-card shadow="never" class="guide" :body-style="shown ? undefined : { display: 'none' }">
    <template #header>
      <div :class="{ toggle: watchlist }" @click="toggle">
        <span class="guide-title">📖 欄位與規則說明</span>
        <span class="guide-sub">型態＋裸K規則評分 v1・紅漲綠跌・價格都是還原價</span>
        <span v-if="watchlist" class="guide-toggle">{{ shown ? '收起 ▴' : '點開 ▾' }}</span>
      </div>
    </template>
    <el-collapse v-model="open" class="guide-collapse">
      <el-collapse-item name="read" title="怎麼讀這張表">
        <ul>
          <li v-if="watchlist"><b>只看這個分類的股票。</b>跟「指定個股分析」一樣，不套母體、流動性、證券類別和基本面門檻；每檔掃描最近幾根 K 棒的裸 K 訊號，用結構、位置、K 棒、確認、風險五層打分數，最後給出決策。</li>
          <li v-else><b>先過濾，再評分。</b>上方的波段型態和成長條件只負責篩選；留下來的股票再掃描最近幾根 K 棒的裸 K 訊號，用結構、位置、K 棒、確認、風險五層打分數，最後給出決策。</li>
          <li><b>裸 K 分數只看開高低收</b>（還原價），不看成交量、均線、基本面和籌碼。</li>
          <li><b>每檔只列一個訊號。</b>觀察窗內每根 K 棒偵測到的型態都會評分，挑決策最好的；決策一樣再比狀態（已觸發 → 等待突破 → 觸發後停損 → 失效／過期）、分數、日期較新。</li>
          <li v-if="watchlist"><b>排序：</b>決策 → 分數 → 均額；最近幾根沒有訊號的排在最後、灰字（「無訊號」）。</li>
          <li v-else><b>排序：</b>決策 → 分數 → 均額。全市場最多列 300 檔，排在後面的（多半是略過）會被截掉，所以「略過」常常是 0。</li>
          <li><b>分數只用來比較同一批候選</b>，不是買進指令，也不是報酬預測；下單前要看 K 線圖確認。</li>
          <li><b>價格都是還原價：</b>最新一根等於實際股價，除權息以前的舊價格會往下調。</li>
          <li><b>空方訊號（綠字）＝短線可能轉弱。</b>台股大多數人不放空，可以把它當成手上持股的減碼、停利提醒，或是「先別買」；要融券放空才照空方的交易計畫做。波段型態是多方、K 棒訊號卻是空方，代表突破後出現賣壓，先等等看。</li>
        </ul>
      </el-collapse-item>

      <el-collapse-item name="cols" title="表格欄位">
        <dl class="defs">
          <template v-for="[k, v] in COLS" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="state" title="訊號狀態：什麼叫觸發">
        <p>多方訊號的觸發價是訊號 K 的高點、停損是低點；空方反過來，觸發價是低點、停損是高點。訊號 K 收盤之後才開始判斷。</p>
        <dl class="defs">
          <template v-for="[k, v] in STATES" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>

      <el-collapse-item name="decision" title="決策規則">
        <div class="table-wrap">
          <table class="rules">
            <thead><tr><th>決策</th><th>條件</th><th>怎麼用</th></tr></thead>
            <tbody>
              <tr v-for="[name, type, cond, use] in DECISIONS" :key="name">
                <td><el-tag :type="type" effect="dark">{{ name }}</el-tag></td><td>{{ cond }}</td><td>{{ use }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </el-collapse-item>

      <el-collapse-item name="score" title="五層評分怎麼算（滿分 100）">
        <h4>構：市場結構（30）</h4>
        <p>用訊號當時看得到的 K 棒找轉折點（前後各 2 根內的最高點或最低點），比較最近兩個高點和兩個低點。順勢的訊號分數比較高。</p>
        <div class="table-wrap">
          <table class="rules">
            <thead><tr><th>市場結構</th><th>判斷</th><th>多方訊號</th><th>空方訊號</th></tr></thead>
            <tbody>
              <tr v-for="[name, how, bull, bear] in STRUCTURES" :key="name">
                <td>{{ name }}</td><td>{{ how }}</td><td class="num">{{ bull }}</td><td class="num">{{ bear }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h4>位：位置（25）</h4>
        <ul>
          <li><b>多方：</b>訊號 K 低點離支撐 ≤ 1.5% 加 14 分「貼近支撐」、≤ 3% 加 10 分「支撐附近」、≤ 5% 加 6 分「接近支撐」；低點回測到已經突破的前高（1.5% 以內），而且收盤站在前高之上，加 10 分「突破回測」；區間 ≤ 25% 加 8 分「區間低檔」、≤ 40% 加 4 分。</li>
          <li><b>空方（反過來）：</b>訊號 K 高點離壓力 ≤ 1.5% 加 14 分「貼近壓力」、≤ 3% 加 10 分「壓力附近」、≤ 5% 加 6 分「接近壓力」；高點反彈到已經跌破的前低（1.5% 以內），而且收盤在前低之下，加 10 分「跌破回測」；區間 ≥ 75% 加 8 分「區間高檔」、≥ 60% 加 4 分。</li>
          <li>最多 25 分；完全沒有加分就標「區間中段」。</li>
          <li><b>支撐</b>＝收盤下方最近的轉折低點（沒有就用前 60 根最低點）；<b>壓力</b>＝收盤上方最近的轉折高點（沒有就用前 60 根最高點）。</li>
        </ul>

        <h4>K：K 棒品質（20）</h4>
        <ul>
          <li><b>基本分：</b>兩根以上組成的型態 11 分（多頭／空頭吞噬、貫穿線、烏雲罩頂、晨星、夜星、紅三兵、黑三鴉、Fakey、兩棒反轉、外包線）；其他 7 分（單根型態、孕線、內包線、NR7）。</li>
          <li><b>收盤位置：</b>多方收得越接近 K 棒高點越好、空方越接近低點越好，最多加 5 分。</li>
          <li><b>K 棒大小</b>（振幅 ÷ 前 20 根振幅的中位數）：0.6～1.8 倍加 4 分；不到 0.6 倍或 1.8～2.5 倍加 2 分；超過 2.5 倍不加分（K 棒太長，停損會很遠）。</li>
        </ul>

        <h4>確：確認（15）</h4>
        <p>已觸發 15 分；等待突破 8 分；觸發前失效、觸發後停損、訊號過期 0 分。</p>

        <h4>險：風險（10）</h4>
        <ul>
          <li>R/R ≥ 3 加 6 分、≥ 2 加 4 分、≥ 1.5 加 2 分。</li>
          <li>停損距離 ≤ 5% 加 4 分、≤ 8% 加 3 分、≤ 10% 加 1 分。</li>
        </ul>
      </el-collapse-item>

      <el-collapse-item name="plan" title="交易計畫">
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

      <el-collapse-item name="fakey" title="Fakey（假突破）是什麼">
        <div class="fakey">
          <div class="fakey-text">
            <p>Fakey 就是「假突破」，由三根 K 棒組成：</p>
            <ol>
              <li><b>母線</b>：一根範圍比較大的 K 棒。</li>
              <li><b>內包線</b>：整根的高點和低點都在母線範圍內，代表多空在整理。</li>
              <li><b>第三根</b>先刺破母線的高點或低點，看起來像要突破，收盤卻又回到母線裡面。</li>
            </ol>
            <p><b class="bear">空方 Fakey</b>：第三根先衝過母線高點，收盤又跌回母線高點以下。追突破的買盤被套住，短線容易往下走。觸發＝之後跌破第三根的低點；停損＝第三根的高點。</p>
            <p><b class="bull">多方 Fakey</b>：反過來。第三根先跌破母線低點，收盤又站回母線低點以上，殺低的賣盤被軋。觸發＝之後漲過第三根的高點；停損＝第三根的低點。</p>
            <p class="muted">這裡要求第三根刺破「母線」；常見的定義只要刺破內包線，所以這裡的條件比較嚴。</p>
          </div>
          <div class="fakey-figs">
            <figure>
              <svg viewBox="0 0 430 240" width="450" role="img" aria-label="空方 Fakey 示意圖">
                <line x1="35" y1="50" x2="255" y2="50" class="lvl" />
                <line x1="215" y1="25" x2="255" y2="25" class="lvl stop" />
                <line x1="215" y1="120" x2="255" y2="120" class="lvl trig" />
                <line x1="60" y1="50" x2="60" y2="180" class="wick up" />
                <rect x="47" y="70" width="26" height="90" class="body up" />
                <line x1="145" y1="80" x2="145" y2="150" class="wick down" />
                <rect x="132" y="95" width="26" height="40" class="body down" />
                <line x1="230" y1="25" x2="230" y2="120" class="wick down" />
                <rect x="217" y="60" width="26" height="45" class="body down" />
                <text x="262" y="31" class="lbl stop-t">停損：這根高點</text>
                <text x="262" y="56" class="lbl">母線高點</text>
                <text x="262" y="126" class="lbl trig-t">觸發：跌破這根低點</text>
                <text x="60" y="225" class="cap">① 母線</text>
                <text x="145" y="225" class="cap">② 內包線</text>
                <text x="230" y="225" class="cap">③ 假突破</text>
              </svg>
              <figcaption class="bear">空方 Fakey</figcaption>
            </figure>
            <figure>
              <svg viewBox="0 0 430 240" width="450" role="img" aria-label="多方 Fakey 示意圖">
                <line x1="35" y1="170" x2="255" y2="170" class="lvl" />
                <line x1="215" y1="195" x2="255" y2="195" class="lvl stop" />
                <line x1="215" y1="100" x2="255" y2="100" class="lvl trig" />
                <line x1="60" y1="40" x2="60" y2="170" class="wick down" />
                <rect x="47" y="60" width="26" height="90" class="body down" />
                <line x1="145" y1="70" x2="145" y2="140" class="wick up" />
                <rect x="132" y="85" width="26" height="40" class="body up" />
                <line x1="230" y1="100" x2="230" y2="195" class="wick up" />
                <rect x="217" y="115" width="26" height="45" class="body up" />
                <text x="262" y="106" class="lbl trig-t">觸發：漲過這根高點</text>
                <text x="262" y="176" class="lbl">母線低點</text>
                <text x="262" y="201" class="lbl stop-t">停損：這根低點</text>
                <text x="60" y="230" class="cap">① 母線</text>
                <text x="145" y="230" class="cap">② 內包線</text>
                <text x="230" y="230" class="cap">③ 假跌破</text>
              </svg>
              <figcaption class="bull">多方 Fakey</figcaption>
            </figure>
          </div>
        </div>
      </el-collapse-item>

      <el-collapse-item name="kbar" title="K 棒型態辭典">
        <p>紅字＝多方、綠字＝空方、灰字＝本身沒有方向。十字星、內包線、NR7 的方向看位置：訊號 K 低點離支撐 3% 以內、而且比高點離壓力還近，算多方；高點離壓力 3% 以內、而且比較近，算空方；都不是就跟著結構走（多頭算多方、空頭算空方），區間整理或結構未明時不列。</p>
        <div class="kbars">
          <div v-for="[name, dir, desc] in KBARS" :key="name" class="kbar">
            <span class="kname" :class="dir">{{ name }}</span><span class="kdesc">{{ desc }}</span>
          </div>
        </div>
      </el-collapse-item>

      <el-collapse-item name="filters" title="上方篩選條件">
        <dl class="defs">
          <template v-for="[k, v] in filterRows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<style scoped>
.guide { margin-top: 14px; }
.guide-title { font-size: 26px; font-weight: 700; color: #303133; }
.guide-sub { margin-left: 12px; font-size: 18px; color: #909399; }
.toggle { cursor: pointer; }
.guide-toggle { float: right; font-size: 20px; color: #409eff; }
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
ul, ol { margin: 6px 0; padding-left: 1.4em; }
li { margin: 4px 0; }
.muted { color: #909399; }
.defs { display: grid; grid-template-columns: 240px 1fr; gap: 8px 20px; margin: 6px 0; }
.defs dt { font-weight: 600; color: #303133; }
.defs dd { margin: 0; color: #555; }
.table-wrap { overflow-x: auto; }
.rules { border-collapse: collapse; margin: 6px 0 10px; }
.rules th, .rules td { border: 1px solid #dcdfe6; padding: 8px 14px; text-align: left; vertical-align: middle; }
.rules th { background: #f5f7fa; font-weight: 600; }
.rules td.num { text-align: center; }
.rules :deep(.el-tag) { font-size: 20px; height: 36px; padding: 0 12px; }
.checks { list-style: none; padding-left: 0; }
.blocker { color: #f56c6c; font-weight: 600; }
.note { color: #a77700; font-weight: 600; }
.bull { color: #f56c6c; }
.bear { color: #529b2e; }
.fakey { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }
.fakey-text { flex: 1 1 560px; }
.fakey-figs { display: flex; gap: 16px; flex-wrap: wrap; }
.fakey-figs figure { margin: 0; text-align: center; }
.fakey-figs svg { max-width: 100%; height: auto; }
.fakey-figs figcaption { font-size: 22px; font-weight: 650; }
.wick { stroke-width: 2.5; }
.wick.up { stroke: #f56c6c; }
.wick.down { stroke: #529b2e; }
.body.up { fill: #f56c6c; }
.body.down { fill: #529b2e; }
.lvl { stroke: #909399; stroke-width: 1.5; stroke-dasharray: 5 4; }
.lvl.stop { stroke: #f56c6c; }
.lvl.trig { stroke: #e6a23c; }
.lbl { font-size: 17px; fill: #606266; }
.stop-t { fill: #f56c6c; }
.trig-t { fill: #b88230; }
.cap { font-size: 16px; fill: #303133; text-anchor: middle; }
.kbars { display: grid; grid-template-columns: repeat(auto-fill, minmax(640px, 1fr)); gap: 8px 28px; }
.kbar { display: flex; gap: 12px; }
.kname { flex: 0 0 190px; font-weight: 650; }
.kname.neutral { color: #909399; }
.kdesc { flex: 1; color: #555; }
</style>
