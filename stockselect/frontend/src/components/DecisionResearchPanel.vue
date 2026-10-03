<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getDecisionResearch } from '../api'

const loading = ref(false)
const result = ref({ status: 'unvalidated', variants: [] })
const chartEl = ref(null)
let chart, observer
const percent = (v) => v == null ? '—' : `${(Number(v) * 100).toFixed(2)}%`
const num = (v) => v == null ? '—' : Number(v).toFixed(2)
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
    v.name === r.benchmark?.name || v.name === '動能20日・趨勢模板' || v.name.includes('整理突破')]))
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
      <p class="muted">同一成交模型、同一訊號期間：隔日開盤、8% 停損、每邊 0.1% 滑價加 0.6% 成本。連續組合限制現金、重複持股、同產業 2 檔、總持股 10 檔與總停損風險 4%；初始淨值 {{ Number(result.capital).toLocaleString() }} 元，{{ result.variants[0]?.portfolio?.lot_size === 1 ? '允許零股' : '整張交易' }}。原始規則使用自己的停損與目標。</p>
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
      <p v-if="result.benchmark?.total_return != null">0050 同期間含成本總報酬 {{ percent(result.benchmark.total_return) }}，最大回撤 {{ percent(result.benchmark.max_drawdown) }}。基準全額持有，策略可能保留現金，請同時比較曝險與回撤。</p>
      <div ref="chartEl" class="chart" />
      <p class="muted">飆股捕捉率以完成模擬成交的訊號計算，參考隔日開盤後第 60 個市場交易日收盤；前 10% 排名來自當日流動性母體。母體 60 日漲逾 30% 的比例為 {{ percent(result.opportunity?.up30_60d_rate) }}。相鄰訊號可能重複同檔股票，不能把這個比例當成獨立預測機率。分段界線 {{ result.validation?.forward_split_date }}，兩段都屬歷史研究。</p>
      <ul class="muted"><li v-for="text in result.validation?.limitations || []" :key="text">{{ text }}</li></ul>
    </template>
    <el-empty v-else description="沒有適用目前規則的回測；規則變更後會停用舊結果" />
  </div>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
h2 { margin: 0; font-size: 22px; }
.muted { color: #73767a; line-height: 1.7; }
.chart { width: 100%; height: 420px; margin-top: 20px; }
</style>
