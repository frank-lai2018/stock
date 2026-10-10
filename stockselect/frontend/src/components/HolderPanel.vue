<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed, nextTick } from 'vue'
import * as echarts from 'echarts'
import * as XLSX from 'xlsx'
import { ElMessage } from 'element-plus'
import { getHoldersReport } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })
const rows = ref([])
const report = ref({ changes: [], big1000_up_periods: 0 })
const loading = ref(false)
const el = ref(null)
let chart = null
let resizeObserver = null
let seq = 0
let disposed = false
const UP = '#d84949'
const DOWN = '#2f8a55'
const tableRows = computed(() => [...rows.value].reverse())
const latest = computed(() => rows.value.at(-1))
const finite = (v) => v != null && Number.isFinite(Number(v))
const fmt = (v, digits = 0) => finite(v) ? Number(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—'
const pct = (v) => finite(v) ? `${fmt(v, 2)}%` : '—'
const change = (v, digits = 2) => finite(v) ? `${Number(v) > 0 ? '+' : ''}${fmt(v, digits)}` : '—'
const clr = (v) => finite(v) && Number(v) !== 0 ? (Number(v) > 0 ? UP : DOWN) : ''
const changedColor = (row, key) => {
  const index = rows.value.indexOf(row)
  const prev = rows.value[index - 1]
  return prev && finite(prev[key]) && finite(row[key]) ? clr(Number(row[key]) - Number(prev[key])) : ''
}
const columns = [
  { key: 'total_lots', label: '集保總張數', width: 125, digits: 3 },
  { key: 'holders_total', label: '總股東人數', width: 115 },
  { key: 'avg_lots_per_holder', label: '平均張數／人', width: 115, digits: 2 },
  { key: 'big400_lots', label: '>400 張持有張數', width: 145, digits: 3 },
  { key: 'big400_pct', label: '>400 張持有比例', width: 140, percent: true },
  { key: 'big400_holders', label: '>400 張人數', width: 115 },
  { key: 'holders_400_600', label: '400～600 張人數', width: 145 },
  { key: 'holders_600_800', label: '600～800 張人數', width: 145 },
  { key: 'holders_800_1000', label: '800～1000 張人數', width: 150 },
  { key: 'big1000_holders', label: '>1000 張人數', width: 125 },
  { key: 'big1000_lots', label: '>1000 張持有張數', width: 150, digits: 3 },
  { key: 'big1000_pct', label: '>1000 張持有比例', width: 150, percent: true },
  { key: 'retail_pct', label: '散戶 ≤10 張比例', width: 140, percent: true },
  { key: 'close', label: '收盤價', width: 100, digits: 2 },
]
function render() {
  if (!el.value || !rows.value.length || !el.value.clientWidth || !el.value.clientHeight) return
  if (!chart) chart = echarts.init(el.value)
  const line = (name, key, color, dashed) => ({
    name, type: 'line', showSymbol: true, symbolSize: 4,
    data: rows.value.map((r) => finite(r[key]) ? Number(r[key]) : null),
    lineStyle: { color, width: 2, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color },
  })
  chart.setOption({
    grid: { left: 55, right: 55, top: 55, bottom: 35 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => finite(v) ? fmt(v, 2) : '—' },
    legend: { data: ['千張大戶', '400張以上', '中實戶', '散戶(≤10張)', '股價'], top: 0 },
    xAxis: { type: 'category', data: rows.value.map((r) => r.data_date), boundaryGap: false },
    yAxis: [{ type: 'value', name: '持有比例 %', scale: true, axisLabel: { formatter: '{value}%' } },
      { type: 'value', name: '股價', scale: true }],
    series: [line('千張大戶', 'big1000_pct', UP), line('400張以上', 'big400_pct', '#ed8500', true),
      line('中實戶', 'mid_pct', '#8e44ad', true), line('散戶(≤10張)', 'retail_pct', DOWN),
      { name: '股價', type: 'line', yAxisIndex: 1, showSymbol: false,
        data: rows.value.map((r) => finite(r.close) ? Number(r.close) : null),
        lineStyle: { color: '#909399', width: 1 }, itemStyle: { color: '#909399' } }],
  }, true)
  chart.resize()
}
async function load() {
  const id = ++seq
  loading.value = true
  rows.value = []
  report.value = { changes: [], big1000_up_periods: 0 }
  chart?.clear()
  try {
    const data = await getHoldersReport(props.stockId, 104)
    if (id !== seq || disposed) return
    rows.value = data.items || []
    report.value = data
    await nextTick()
    if (id === seq && !disposed) render()
  } catch (e) {
    if (id === seq && !disposed) ElMessage.error('載入集保股權分散失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    if (id === seq && !disposed) loading.value = false
  }
}
function exportExcel() {
  const wb = XLSX.utils.book_new()
  const detail = tableRows.value.map((r) => ({
    資料日期: r.data_date,
    ...Object.fromEntries(columns.map((c) => [c.label + (c.percent ? '（%）' : ''), finite(r[c.key]) ? Number(r[c.key]) : null])),
    收盤價日期: r.price_date, 總數來源: r.total_source, 資料檢查: r.quality_reason,
    差異股數: r.adjustment_shares == null ? null : Number(r.adjustment_shares),
    首次確認取得時間: r.first_seen_at, 本版本確認取得時間: r.values_seen_at,
  }))
  const sheet = XLSX.utils.json_to_sheet(detail)
  sheet['!cols'] = Object.keys(detail[0]).map((_, i) => ({ wch: i >= 16 ? 28 : 20 }))
  XLSX.utils.book_append_sheet(wb, sheet, '集保歷史')
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(report.value.changes.map((r) => ({
    比較期數: r.periods, 起日: r.from_date, 迄日: r.to_date,
    千張變化百分點: r.big1000_pct, 四百張以上變化百分點: r.big400_pct,
    散戶變化百分點: r.retail_pct, 股東人數變化: r.holders_total, 資料不足原因: r.reason,
  }))), '籌碼變化')
  const notes = [
    ['股票代號', props.stockId], ['資料期數', rows.value.length], ['千張連續增加期數', report.value.big1000_up_periods],
    ['來源', 'TDCC 集保戶股權分散表 https://www.tdcc.com.tw/portal/zh/smWeb/qryStock'],
    ['單位', '1 張＝1,000 股；張數保留小數；比例變化使用百分點。'],
    ['分級', '400～600 表示 >400、≤600 張，其餘區間同理；千張大戶 >1000 張。'],
    ['合計', '優先官方合計；舊資料缺合計時使用 1～15 級加總，不含 16 差異及 17 合計的重複計數。'],
    ['時間', '未知官方發布時間時使用本機確認取得時間，不將資料日期當成發布時間。'],
    ['解讀', '集保庫存並非發行總股數；比例變動不能直接認定特定主力買賣。'],
  ]
  const noteSheet = XLSX.utils.aoa_to_sheet(notes)
  noteSheet['!cols'] = [{ wch: 24 }, { wch: 105 }]
  XLSX.utils.book_append_sheet(wb, noteSheet, '資料說明')
  XLSX.writeFile(wb, `集保股權分散_${props.stockId}_${latest.value.data_date.replaceAll('-', '')}.xlsx`)
}
function onResize() { if (chart) chart.resize(); else render() }
onMounted(() => {
  resizeObserver = new ResizeObserver(onResize)
  resizeObserver.observe(el.value)
  load()
  window.addEventListener('resize', onResize)
})
watch(() => props.stockId, load)
onBeforeUnmount(() => { disposed = true; seq++; resizeObserver?.disconnect(); window.removeEventListener('resize', onResize); chart?.dispose() })
</script>

<template>
  <div v-loading="loading" class="holder-panel">
    <div class="holder-heading">
      <div><b>集保週資料</b><span v-if="latest" class="muted">截至 {{ latest.data_date }} · {{ rows.length }} 期</span></div>
      <el-button size="small" :disabled="!rows.length || loading" @click="exportExcel">匯出集保 Excel</el-button>
    </div>
    <p class="holder-note">依每週最後營業日的集保餘額統計。400～600 張等區間含上限、不含下限；張數保留零股小數。紅色表示比前一期增加，綠色表示減少。</p>
    <el-empty v-if="!loading && !rows.length" description="無集保資料" :image-size="60" />
    <template v-if="latest">
      <div class="holder-stats">
        <div><span>集保總張數</span><b>{{ fmt(latest.total_lots, 3) }}</b><small>{{ latest.total_source }}</small></div>
        <div><span>總股東人數</span><b>{{ fmt(latest.holders_total) }}</b><small>平均 {{ fmt(latest.avg_lots_per_holder, 2) }} 張／人</small></div>
        <div><span>400 張以上持有比例</span><b>{{ pct(latest.big400_pct) }}</b><small>{{ fmt(latest.big400_holders) }} 人 · {{ fmt(latest.big400_lots, 3) }} 張</small></div>
        <div><span>千張大戶持有比例</span><b>{{ pct(latest.big1000_pct) }}</b><small>連續增加 {{ report.big1000_up_periods }} 期</small></div>
      </div>
      <el-alert v-if="!latest.quality_ok" :title="latest.quality_reason" type="warning" :closable="false" show-icon />
      <el-table :data="report.changes" size="small" class="holder-changes" aria-label="籌碼變化摘要">
        <el-table-column label="期間" width="105"><template #default="{ row }">近 {{ row.periods }} 期</template></el-table-column>
        <el-table-column label="千張大戶" min-width="155"><template #default="{ row }"><span :style="{ color: clr(row.big1000_pct) }">{{ change(row.big1000_pct) }}<template v-if="finite(row.big1000_pct)"> 百分點</template></span></template></el-table-column>
        <el-table-column label="400 張以上" min-width="155"><template #default="{ row }"><span :style="{ color: clr(row.big400_pct) }">{{ change(row.big400_pct) }}<template v-if="finite(row.big400_pct)"> 百分點</template></span></template></el-table-column>
        <el-table-column label="散戶 ≤10 張" min-width="155"><template #default="{ row }"><span :style="{ color: clr(row.retail_pct) }">{{ change(row.retail_pct) }}<template v-if="finite(row.retail_pct)"> 百分點</template></span></template></el-table-column>
        <el-table-column label="股東人數" min-width="125"><template #default="{ row }">{{ change(row.holders_total, 0) }}</template></el-table-column>
        <el-table-column label="比較日期／缺資料原因" min-width="230"><template #default="{ row }">{{ row.reason || `${row.from_date} → ${row.to_date}` }}</template></el-table-column>
      </el-table>
    </template>
    <div v-show="rows.length" ref="el" class="holder-chart"></div>
    <el-table v-if="rows.length" :data="tableRows" height="420" size="small" stripe class="holder-history" aria-label="完整集保歷史表">
      <el-table-column label="資料日期" width="115" fixed><template #default="{ row }">{{ row.data_date }}</template></el-table-column>
      <el-table-column v-for="c in columns" :key="c.key" :label="c.label" :width="c.width" align="right">
        <template #default="{ row }"><span :style="{ color: changedColor(row, c.key) }">{{ c.percent ? pct(row[c.key]) : fmt(row[c.key], c.digits || 0) }}</span></template>
      </el-table-column>
      <el-table-column label="資料檢查／總數來源" width="250" show-overflow-tooltip><template #default="{ row }">{{ row.total_source }}；{{ row.quality_reason }}</template></el-table-column>
    </el-table>
    <p v-if="rows.length" class="holder-note">總張數為集保庫存。歷史缺官方合計的期數會標示「分級加總」；比例採官方值。缺期或數值無效時，變化摘要顯示原因。籌碼集中代表持股結構變化，仍須搭配價格買點判斷。</p>
  </div>
</template>

<style scoped>
.holder-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.muted { margin-left: 12px; color: #7b8491; font-size: 13px; }
.holder-note { color: #7b8491; font-size: 12px; line-height: 1.7; margin: 10px 0; }
.holder-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }
.holder-stats > div { padding: 14px; background: var(--el-fill-color-light); border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.holder-stats span, .holder-stats small { display: block; font-size: 12px; color: #7b8491; }
.holder-stats b { display: block; font-size: 21px; margin: 6px 0; font-variant-numeric: tabular-nums; }
.holder-changes { margin-top: 12px; }
.holder-chart { width: 100%; height: 300px; margin-top: 18px; }
.holder-history { margin-top: 12px; font-variant-numeric: tabular-nums; }
@media (max-width: 800px) { .holder-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } .holder-stats b { font-size: 18px; } }
@media (max-width: 420px) { .holder-stats { grid-template-columns: 1fr; } }
</style>
