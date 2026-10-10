<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getDecisionResearch } from '../api'

const loading = ref(false)
const result = ref({ status: 'unvalidated', variants: [] })
const chartEl = ref(null)
let chart, observer
const percent = (v) => v == null ? '—' : `${(Number(v) * 100).toFixed(2)}%`
const num = (v) => v == null ? '—' : Number(v).toFixed(2)
const metricDefinitions = computed(() => {
  const split = result.value.validation?.forward_split_date || '分段界線'
  const start = result.value.signal_start || '研究起日'
  const end = result.value.data_end || '結算日'
  return [
    { field: '策略版本', meaning: '比較的選股與出場規則。動能20日以20個市場交易日為持有期限；趨勢持有60日以60日為期限，或收盤跌破50日均線後安排隔日開盤出場。', reading: '名稱後半部是進場條件；「實驗」表示尚待驗證。原始規則使用自己的停損與目標；其他版本使用8%停損。到期於當日收盤出場，停損可能提前出場，鎖跌停可能延後成交。' },
    { field: '組合總報酬', meaning: '整個帳戶從研究開始到結算的累積報酬：期末淨值 ÷ 初始淨值 − 1。淨值包含現金與尚未賣出的持股市值，並反映交易成本與滑價。', reading: '例如100萬元變110萬元，就是+10%。這是帳戶的整體結果，判讀時搭配最大回撤與0050比較。' },
    { field: '最大回撤', meaning: '帳戶淨值從之前的最高點往下跌，期間最深的一次跌幅。', reading: '例如淨值曾到120萬元，之後跌到96萬元，就是−20%。負值越接近0，歷史跌幅越小；它不是單筆交易的停損幅度。' },
    { field: '年化報酬', meaning: '將整段組合報酬換算成每年的複利成長率（CAGR）；本研究以每年252個交易日換算。', reading: '例如年化+10%，表示這段歷史相當於每年複利成長10%的速度，不代表每個年度都賺10%。' },
    { field: 'Sharpe', meaning: '每日帳戶報酬的平均值 ÷ 每日報酬的波動程度，再換算成年化；此處以無風險利率0計算。', reading: '不是百分比；同一期間與計算方式下，通常越高表示每單位波動換到的報酬越多。負值表示平均日報酬為負；也要看交易數與回撤。' },
    { field: '組合交易數', meaning: '連續帳戶模擬中，已完成買進並賣出的交易筆數。', reading: '同一檔股票分次進出會分別計數；未成交與期末仍持有的部位不計入。樣本很少時，績效容易受少數交易影響。' },
    { field: '平均曝險', meaning: '每天「持股市值 ÷ 帳戶淨值」的平均值，包含最後只結算、不開新倉的期間。', reading: '例如30%表示平均約三成淨值放在股票，其餘主要是現金。它不是虧損機率或停損風險；曝險低可能讓報酬與回撤都較小。' },
    { field: '前段組合報酬', meaning: `研究起日 ${start} 到分段界線 ${split}（含當日）的帳戶報酬。`, reading: '用來看策略在較早一段歷史的表現；前後兩段都屬於已研究過的歷史資料。' },
    { field: '後段組合報酬', meaning: `分段界線 ${split} 之後到結算日 ${end}，以前段期末淨值為起點計算的帳戶報酬。`, reading: '帳戶與持股延續前段。合併報酬＝（1＋前段報酬）×（1＋後段報酬）−1，不能直接相加；後段也不是獨立的樣本外驗證。' },
    { field: '訊號平均淨報酬', meaning: '每天以空持股帳戶挑出訊號，各自模擬成交與出場後，將已完成交易的淨報酬等權平均。每筆已扣成本與滑價。', reading: '例如兩筆分別+10%、−4%，平均就是+3%。訊號不延續跨日持股與現金限制，且每筆權重相同，結果會與連續帳戶的組合總報酬不同。' },
    { field: '60日漲幅前10%', meaning: '已完成模擬成交的訊號中，60日參考漲幅位於訊號當日流動性母體前10%的比例；母體是當天符合基本交易與流動性條件的股票。', reading: '例如100筆訊號有15筆進入前10%，顯示15%。用來看是否選到後續相對強勢的股票，不是交易勝率，也不表示策略實際抱滿60日。' },
    { field: '60日漲逾30%', meaning: '已完成模擬成交的訊號中，60日參考漲幅達30%以上的比例。', reading: '例如100筆訊號有20筆達標，顯示20%。即使股票後來大漲，策略也可能已提前停損或出場；同檔重複訊號亦會分別計數。' },
  ]
})
async function load() {
  loading.value = true
  try {
    result.value = await getDecisionResearch()
    await nextTick()
    draw()
  } catch (e) {
    ElMessage.error('研究結果載入失敗：' + (e?.response?.data?.detail || e.message))
  } finally { loading.value = false }
}
function draw() {
  if (!chartEl.value) { observer?.disconnect(); chart?.dispose(); chart = null; return }
  if (!chart) {
    chart = echarts.init(chartEl.value)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartEl.value)
  }
  const r = result.value
  const lines = (r.variants || []).map(v => ({ name: v.name, curve: v.curve }))
  if (r.benchmark?.curve?.length) lines.push(r.benchmark)
  const selected = Object.fromEntries(lines.map(v => [v.name,
    // 預設只開基準、預設規則與各實驗的動能 20 日版；其餘點圖例再打開
    v.name === r.benchmark?.name || v.name === '動能20日・趨勢模板' || (v.name.startsWith('動能20日') && v.name.includes('（實驗）'))]))
  chart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: v => `${Number(v).toFixed(2)}%` },
    legend: { type: 'scroll', top: 0, selected },
    grid: { left: 65, right: 25, top: 55, bottom: 65 },
    xAxis: { type: 'time' }, yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 5 }],
    series: lines.map(v => ({ name: v.name, type: 'line', showSymbol: false,
      data: (v.curve || []).map(p => [p.date, (p.equity / r.capital - 1) * 100]) })),
  }, true)
}
onMounted(load)
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>

<template>
  <div v-loading="loading">
    <div class="title-row"><h2>同版本策略研究</h2><el-button :loading="loading" @click="load">重新讀取</el-button></div>
    <el-alert :type="result.status === 'in_sample' ? 'warning' : 'info'" :closable="false" show-icon>
      {{ result.message || '等待研究結果' }}
      <div v-if="result.signal_start">訊號 {{ result.signal_start }}～{{ result.signal_end }}；結算至 {{ result.data_end }}。最後 60 日保留給結算，不開新倉。</div>
    </el-alert>
    <template v-if="result.variants?.length">
      <p class="muted">同一成交模型、同一訊號期間：隔日開盤（支撐轉強、W 底測右底為隔日盤中漲到觸發價才買）、8% 停損、每邊 0.1% 滑價加 0.6% 成本。連續組合限制現金、重複持股、同產業 2 檔、總持股 10 檔與總停損風險 4%；初始淨值 {{ Number(result.capital).toLocaleString() }} 元，{{ result.variants[0]?.portfolio?.lot_size === 1 ? '允許零股' : '整張交易' }}。原始規則使用自己的停損與目標。</p>
      <el-table :data="result.variants" border stripe>
        <el-table-column prop="name" label="策略版本" min-width="225" fixed />
        <el-table-column label="組合總報酬" width="120"><template #default="{ row }">{{ percent(row.portfolio.total_return) }}</template></el-table-column>
        <el-table-column label="最大回撤" width="115"><template #default="{ row }">{{ percent(row.portfolio.max_drawdown) }}</template></el-table-column>
        <el-table-column label="年化報酬" width="115"><template #default="{ row }">{{ percent(row.portfolio.cagr) }}</template></el-table-column>
        <el-table-column label="Sharpe" width="95"><template #default="{ row }">{{ num(row.portfolio.sharpe) }}</template></el-table-column>
        <el-table-column prop="portfolio.trades" label="組合交易數" width="115" />
        <el-table-column label="平均曝險" width="110"><template #default="{ row }">{{ percent(row.portfolio.mean_exposure) }}</template></el-table-column>
        <el-table-column label="前段組合報酬" width="130"><template #default="{ row }">{{ percent(row.periods?.[0]?.total_return) }}</template></el-table-column>
        <el-table-column label="後段組合報酬" width="130"><template #default="{ row }">{{ percent(row.periods?.[1]?.total_return) }}</template></el-table-column>
        <el-table-column label="訊號平均淨報酬" width="140"><template #default="{ row }">{{ percent(row.event.mean_net_return) }}</template></el-table-column>
        <el-table-column label="60日漲幅前10%" width="145"><template #default="{ row }">{{ percent(row.event.top10_60d_rate) }}</template></el-table-column>
        <el-table-column label="60日漲逾30%" width="135"><template #default="{ row }">{{ percent(row.event.up30_60d_rate) }}</template></el-table-column>
      </el-table>
      <section class="metric-guide" aria-labelledby="research-metric-guide-title">
        <h3 id="research-metric-guide-title">表格欄位說明</h3>
        <p class="muted">「組合」是持續運作的整個帳戶；「訊號」則是逐筆獨立模擬。建議先看組合總報酬、最大回撤與平均曝險，再看交易數和最後兩欄的強勢股比例。</p>
        <el-table :data="metricDefinitions" border stripe class="metric-definition-table" aria-label="策略研究欄位說明">
          <el-table-column prop="field" label="欄位" width="170" fixed />
          <el-table-column prop="meaning" label="代表什麼" min-width="330" />
          <el-table-column prop="reading" label="如何判讀／例子" min-width="330" />
        </el-table>
        <p class="muted metric-note">最後兩欄的60日參考漲幅，統一用「訊號隔日開盤」到「訊號日後第60個市場交易日收盤」的還原價計算，未扣交易成本，與各策略的實際成交價、出場日及淨報酬不同。表中的「—」表示無法計算或缺資料，不代表0。</p>
      </section>
      <p v-if="result.benchmark?.total_return != null">0050 同期間含成本總報酬 {{ percent(result.benchmark.total_return) }}，最大回撤 {{ percent(result.benchmark.max_drawdown) }}。基準全額持有，策略可能保留現金，請同時比較曝險與回撤。</p>
      <div ref="chartEl" class="chart" />
      <p class="muted">飆股捕捉率以完成模擬成交的訊號計算，參考訊號日後第 60 個市場交易日收盤；前 10% 排名來自當日流動性母體。母體 60 日漲逾 30% 的比例為 {{ percent(result.opportunity?.up30_60d_rate) }}。相鄰訊號可能重複同檔股票，不能把這個比例當成獨立預測機率。分段界線 {{ result.validation?.forward_split_date }}，兩段都屬歷史研究。</p>
      <ul class="muted"><li v-for="text in result.validation?.limitations || []" :key="text">{{ text }}</li></ul>
    </template>
    <el-empty v-else description="沒有適用目前規則的回測；規則變更後會停用舊結果" />
  </div>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
h2 { margin: 0; font-size: 22px; }
.muted { color: #73767a; line-height: 1.7; }
.metric-guide { margin-top: 24px; }
.metric-guide h3 { margin: 0 0 8px; font-size: 18px; }
.metric-guide > p { margin: 8px 0 12px; }
.metric-definition-table :deep(.cell) { line-height: 1.7; padding-top: 5px; padding-bottom: 5px; }
.metric-guide .metric-note { font-size: 13px; }
.chart { width: 100%; height: 420px; margin-top: 20px; }
</style>
