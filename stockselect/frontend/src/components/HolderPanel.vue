<script setup>
// 集保股權分散：大戶 vs 散戶持股佔比週趨勢（疊股價，看籌碼有沒有往大戶集中）
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getHolders } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const rows = ref([])
const el = ref(null)
let chart = null

const UP = '#EA4C4C'
const DOWN = '#3F9E5A'
const tableRows = computed(() => [...rows.value].reverse())      // 表格新到舊
const dstr = (d) => String(d).slice(5)                            // 07-03
const pct = (v) => (v == null ? '—' : Number(v).toFixed(2) + '%')
const num = (v) => (v == null ? '—' : Number(v).toLocaleString('en-US'))
const chg = (key) => {                                            // 期間變化（最新 − 最舊）
  const a = rows.value[rows.value.length - 1]?.[key]
  const b = rows.value[0]?.[key]
  return (a == null || b == null) ? null : Number(a) - Number(b)
}
const sign = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%')
const clr = (v) => (v == null ? '' : v >= 0 ? UP : DOWN)

function render() {
  if (!chart) chart = echarts.init(el.value)
  const x = rows.value.map((r) => dstr(r.data_date))
  const line = (name, key, color, dashed) => ({
    name, type: 'line', showSymbol: true, symbolSize: 4,
    data: rows.value.map((r) => (r[key] == null ? null : Number(r[key]))),
    lineStyle: { color, width: 2, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color },
  })
  chart.setOption({
    grid: { left: 56, right: 64, top: 30, bottom: 28 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : Number(v).toFixed(2)) },
    legend: { data: ['千張大戶', '400張大戶', '中實戶', '散戶(≤10張)', '股價'], top: 0 },
    xAxis: { type: 'category', data: x, boundaryGap: false },
    yAxis: [
      { type: 'value', name: '佔比%', scale: true, axisLabel: { formatter: '{value}%' } },
      { type: 'value', name: '股價', scale: true },
    ],
    series: [
      line('千張大戶', 'big1000_pct', UP),
      line('400張大戶', 'big400_pct', '#FF7A00', true),
      line('中實戶', 'mid_pct', '#8E44AD', true),
      line('散戶(≤10張)', 'retail_pct', DOWN),
      { name: '股價', type: 'line', yAxisIndex: 1, showSymbol: false,
        data: rows.value.map((r) => (r.close == null ? null : Number(r.close))),
        lineStyle: { color: '#909399', width: 1 }, itemStyle: { color: '#909399' } },
    ],
  })
}

async function load() {
  try {
    rows.value = await getHolders(props.stockId, 104)
    render()
  } catch (e) {
    ElMessage.error('載入集保股權分散失敗：' + (e?.response?.data?.detail || e.message))
  }
}
function onResize() { if (chart) chart.resize() }

onMounted(() => { load(); window.addEventListener('resize', onResize) })
watch(() => props.stockId, load)
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); if (chart) chart.dispose() })
</script>

<template>
  <div>
    <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px">
      <template v-if="rows.length >= 2">
        <span style="color: #666; font-size: 13px">
          期間（{{ dstr(rows[0].data_date) }} → {{ dstr(rows[rows.length - 1].data_date) }}）：
          千張大戶 <b :style="{ color: clr(chg('big1000_pct')) }">{{ sign(chg('big1000_pct')) }}</b>、
          散戶 <b :style="{ color: clr(chg('retail_pct')) }">{{ sign(chg('retail_pct')) }}</b>
        </span>
        <el-tag v-if="chg('big1000_pct') > 0 && chg('retail_pct') < 0" type="danger" effect="dark">籌碼集中（大戶增、散戶減）</el-tag>
        <el-tag v-else-if="chg('big1000_pct') < 0 && chg('retail_pct') > 0" type="success" effect="dark">籌碼分散（大戶減、散戶增）</el-tag>
      </template>
      <span style="color: #999; font-size: 12px">
        集保週資料（每週五更新）。分級：散戶≤10張、中實戶 10~400 張、大戶≥400 張、千張大戶＞1000 張
      </span>
    </div>

    <el-alert v-if="rows.length && rows.length < 12" type="info" :closable="false" show-icon
              style="margin-bottom: 8px"
              :title="`目前只有 ${rows.length} 週歷史（TDCC 每次僅提供最新一期，需逐週累積）；趨勢判讀待資料變長會更可靠`" />
    <el-empty v-if="!rows.length" description="無集保資料" :image-size="60" />

    <div v-show="rows.length" ref="el" style="width: 100%; height: 280px"></div>

    <el-table v-if="rows.length" :data="tableRows" height="260" size="small" stripe style="margin-top: 8px">
      <el-table-column label="週別" width="90"><template #default="{ row }">{{ String(row.data_date).slice(0, 10) }}</template></el-table-column>
      <el-table-column label="千張大戶%" align="right"><template #default="{ row }">{{ pct(row.big1000_pct) }}</template></el-table-column>
      <el-table-column label="400張大戶%" align="right"><template #default="{ row }">{{ pct(row.big400_pct) }}</template></el-table-column>
      <el-table-column label="中實戶%" align="right"><template #default="{ row }">{{ pct(row.mid_pct) }}</template></el-table-column>
      <el-table-column label="散戶%" align="right"><template #default="{ row }">{{ pct(row.retail_pct) }}</template></el-table-column>
      <el-table-column label="總股東人數" align="right"><template #default="{ row }">{{ num(row.holders_total) }}</template></el-table-column>
      <el-table-column label="股價" align="right"><template #default="{ row }">{{ row.close ?? '—' }}</template></el-table-column>
    </el-table>
  </div>
</template>
