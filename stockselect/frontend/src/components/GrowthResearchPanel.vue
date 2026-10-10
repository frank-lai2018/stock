<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import * as XLSX from 'xlsx'
import { ElMessage } from 'element-plus'
import { getGrowthResearch } from '../api'

const result = ref({ variants: [] })
const loading = ref(false)
const chartEl = ref(null)
let chart, observer, disposed = false
const pct = (v) => v == null ? '—' : `${(Number(v) * 100).toFixed(2)}%`
const num = (v) => v == null ? '—' : Number(v).toFixed(2)
const comparison = computed(() => {
  const variants = result.value.variants || []
  const experiments = variants.filter(v => v.code !== 'rs')
  const aboveRS = experiments.filter(v => {
    const baseline = variants.find(b => b.code === 'rs' && b.gate === v.gate)
    return baseline && v.portfolio.total_return > baseline.portfolio.total_return
  }).length
  const benchmark = result.value.benchmark?.total_return
  const benchmarkText = typeof benchmark === 'number'
    ? `${experiments.filter(v => v.portfolio.total_return > benchmark).length} 組高於0050。` : '沒有可用的0050報酬對照。'
  return `${experiments.length} 組營收實驗中，${aboveRS} 組總報酬高於同買點的RS基準，${benchmarkText}請同時比較回撤與曝險；歷史結果不能當作未來保證。`
})

async function load() {
  loading.value = true
  try {
    const data = await getGrowthResearch()
    if (disposed) return
    result.value = data
    await nextTick()
    observer?.disconnect()
    if (chartEl.value) {
      observer = new ResizeObserver(() => chart ? chart.resize() : draw())
      observer.observe(chartEl.value)
    }
    draw()
  } catch (e) {
    if (!disposed) ElMessage.error('營收品質研究載入失敗：' + (e?.response?.data?.detail || e.message))
  } finally { if (!disposed) loading.value = false }
}
function draw() {
  const el = chartEl.value
  if (!el || !el.clientWidth || !el.clientHeight || !result.value.variants?.length) return
  chart ||= echarts.init(el)
  const r = result.value
  const lines = [...r.variants, ...(r.benchmark?.curve?.length ? [r.benchmark] : [])]
  chart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: v => `${Number(v).toFixed(2)}%` },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 65, right: 25, top: 55, bottom: 65 },
    xAxis: { type: 'time' }, yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 5 }],
    series: lines.map(v => ({ name: v.name, type: 'line', showSymbol: false,
      data: (v.curve || []).map(p => [p.date, (p.equity / r.capital - 1) * 100]) })),
  }, true)
}
function exportExcel() {
  const r = result.value
  const wb = XLSX.utils.book_new()
  const table = [['策略', '總報酬%', '最大回撤%', '年化報酬%', 'Sharpe', '交易數', '平均曝險%', '前段報酬%', '後段報酬%', '訊號淨報酬%', '訊號勝率%', '60日漲幅前10%', '60日漲逾30%']]
  const percent = v => v == null ? '' : Number((v * 100).toFixed(4))
  for (const v of r.variants) table.push([v.name, percent(v.portfolio.total_return), percent(v.portfolio.max_drawdown),
    percent(v.portfolio.cagr), v.portfolio.sharpe, v.portfolio.trades, percent(v.portfolio.mean_exposure),
    percent(v.periods?.[0]?.total_return), percent(v.periods?.[1]?.total_return), percent(v.event.mean_net_return),
    percent(v.event.win_rate), percent(v.event.top10_60d_rate), percent(v.event.up30_60d_rate)])
  if (r.benchmark) table.push([r.benchmark.name, percent(r.benchmark.total_return), percent(r.benchmark.max_drawdown), percent(r.benchmark.cagr), r.benchmark.sharpe])
  const ws = XLSX.utils.aoa_to_sheet(table)
  ws['!cols'] = table[0].map((_, i) => ({ wch: i ? 16 : 36 }))
  XLSX.utils.book_append_sheet(wb, ws, '研究比較')
  const notes = [['項目', '內容'], ['狀態', r.message], ['訊號起日', r.signal_start], ['訊號迄日', r.signal_end],
    ['結算至', r.data_end], ['分段界線', r.validation?.split_date], ['初始淨值', r.capital],
    ['條件', r.rule], ['可得日', r.timing], ['規則版本', r.version], ['計算時間', r.computed_at],
    ['末段處理', '為取得完整60日觀察，最後60個交易日只結算、策略不新增訊號；0050持續持有'],
    ...(r.validation?.limitations || []).map(text => ['限制', text])]
  const ns = XLSX.utils.aoa_to_sheet(notes)
  ns['!cols'] = [{ wch: 18 }, { wch: 105 }]
  XLSX.utils.book_append_sheet(wb, ns, '條件與限制')
  XLSX.writeFile(wb, `營收品質研究_${r.signal_start}_${r.signal_end}.xlsx`)
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; observer?.disconnect(); chart?.dispose() })
</script>

<template>
  <div v-loading="loading">
    <div class="title-row">
      <h2>營收成長品質研究</h2>
      <div><el-button :disabled="!result.variants?.length" @click="exportExcel">匯出研究 Excel</el-button><el-button :loading="loading" @click="load">重新讀取</el-button></div>
    </div>
    <el-alert type="warning" :closable="false" show-icon>{{ result.message || '等待歷史研究結果' }}</el-alert>
    <template v-if="result.variants?.length">
      <el-alert type="info" :closable="false" show-icon style="margin-top: 12px">{{ comparison }}</el-alert>
      <p>訊號 {{ result.signal_start }}～{{ result.signal_end }}；結算至 {{ result.data_end }}。同一主買點分別比較「RS基準」「只換營收排序」「營收篩選＋RS」「營收篩選＋品質排序」，觀察篩選與排序各自的影響。</p>
      <p class="muted">動能20日、8%停損、隔日開盤、每邊0.1%滑價及0.6%來回成本。初始淨值 {{ Number(result.capital).toLocaleString() }} 元；{{ result.variants[0]?.portfolio?.lot_size === 1 ? '允許零股' : '整張交易' }}。保留現金、同產業2檔、總持股10檔、總停損風險4%。RS仍是預設排序。</p>
      <p class="muted">為取得完整60日觀察，最後60個交易日只供結算，策略不新增訊號；0050持續持有。比較總報酬時，請一併看平均曝險與回撤。</p>
      <el-table :data="result.variants" border stripe>
        <el-table-column prop="name" label="主買點／實驗" min-width="260" fixed />
        <el-table-column label="組合總報酬" width="125"><template #default="{ row }">{{ pct(row.portfolio.total_return) }}</template></el-table-column>
        <el-table-column label="最大回撤" width="120"><template #default="{ row }">{{ pct(row.portfolio.max_drawdown) }}</template></el-table-column>
        <el-table-column label="年化報酬" width="120"><template #default="{ row }">{{ pct(row.portfolio.cagr) }}</template></el-table-column>
        <el-table-column label="Sharpe" width="90"><template #default="{ row }">{{ num(row.portfolio.sharpe) }}</template></el-table-column>
        <el-table-column prop="portfolio.trades" label="組合交易數" width="110" />
        <el-table-column label="平均曝險" width="115"><template #default="{ row }">{{ pct(row.portfolio.mean_exposure) }}</template></el-table-column>
        <el-table-column label="前段報酬" width="115"><template #default="{ row }">{{ pct(row.periods?.[0]?.total_return) }}</template></el-table-column>
        <el-table-column label="後段報酬" width="115"><template #default="{ row }">{{ pct(row.periods?.[1]?.total_return) }}</template></el-table-column>
        <el-table-column label="訊號淨報酬" width="120"><template #default="{ row }">{{ pct(row.event.mean_net_return) }}</template></el-table-column>
        <el-table-column label="訊號勝率" width="110"><template #default="{ row }">{{ pct(row.event.win_rate) }}</template></el-table-column>
        <el-table-column label="60日漲幅前10%" width="145"><template #default="{ row }">{{ pct(row.event.top10_60d_rate) }}</template></el-table-column>
        <el-table-column label="60日漲逾30%" width="135"><template #default="{ row }">{{ pct(row.event.up30_60d_rate) }}</template></el-table-column>
      </el-table>
      <p>0050 同期間含成本總報酬 {{ pct(result.benchmark?.total_return) }}、最大回撤 {{ pct(result.benchmark?.max_drawdown) }}。基準全額持有，策略可能保留現金，需同時比較曝險與回撤。</p>
      <div ref="chartEl" class="chart" />
      <p class="muted">營收資料可評分 {{ result.coverage?.available?.toLocaleString() }}／{{ result.coverage?.candidates?.toLocaleString() }} 檔次；通過品質 {{ result.coverage?.passed?.toLocaleString() }} 檔次。分段界線 {{ result.validation?.split_date }}；兩段都屬歷史研究。60日捕捉率以完成成交的重複訊號計算，不是獨立預測機率。</p>
      <details><summary>本次條件與限制</summary><p>{{ result.rule }}</p><ul class="muted"><li v-for="text in result.validation?.limitations || []" :key="text">{{ text }}</li></ul></details>
    </template>
    <el-empty v-else description="尚無適用目前營收品質規則的研究結果" />
  </div>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
h2 { margin: 0; font-size: 22px; }
p, li { line-height: 1.7; }
.muted { color: #73767a; }
.chart { width: 100%; height: 420px; margin: 20px 0; }
</style>
