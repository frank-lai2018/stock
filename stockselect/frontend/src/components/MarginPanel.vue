<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getMargin } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const tf = ref('D')
const rows = ref([])
const el = ref(null)
let chart = null
const BARS = { D: 60, W: 52, M: 36, Q: 16 }

const tableRows = computed(() => [...rows.value].reverse())   // 表格新到舊
const clr = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
const sign = (v) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + Number(v).toLocaleString('en-US'))
const num = (v) => (v == null ? '—' : Number(v).toLocaleString('en-US'))
const dstr = (d) => String(d).slice(0, 10)

function render() {
  if (!chart) chart = echarts.init(el.value)
  const x = rows.value.map((r) => dstr(r.trade_date))
  chart.setOption({
    grid: { left: 64, right: 60, top: 30, bottom: 30 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['融資餘額', '融券餘額', '融資使用率', '券資比'], top: 0 },
    xAxis: { type: 'category', data: x, boundaryGap: false },
    yAxis: [
      { type: 'value', name: '張', scale: true },
      { type: 'value', name: '%', scale: true, axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      { name: '融資餘額', type: 'line', showSymbol: false, data: rows.value.map((r) => r.margin_balance),
        lineStyle: { color: '#EA4C4C' }, itemStyle: { color: '#EA4C4C' } },
      { name: '融券餘額', type: 'line', showSymbol: false, data: rows.value.map((r) => r.short_balance),
        lineStyle: { color: '#3F9E5A' }, itemStyle: { color: '#3F9E5A' } },
      { name: '融資使用率', type: 'line', yAxisIndex: 1, showSymbol: false,
        data: rows.value.map((r) => (r.margin_util == null ? null : Number(r.margin_util))),
        lineStyle: { color: '#FF7A00', type: 'dashed' }, itemStyle: { color: '#FF7A00' } },
      { name: '券資比', type: 'line', yAxisIndex: 1, showSymbol: false,
        data: rows.value.map((r) => (r.short_margin_ratio == null ? null : Number(r.short_margin_ratio))),
        lineStyle: { color: '#2E7DEE', type: 'dashed' }, itemStyle: { color: '#2E7DEE' } },
    ],
  })
}

// 最新一筆的融資使用率／券資比（給頂部標籤）
const latest = computed(() => rows.value[rows.value.length - 1] || null)
const utilTag = computed(() => {
  const u = latest.value?.margin_util
  if (u == null) return null
  const v = Number(u)
  if (v >= 50) return { t: '融資過熱', type: 'danger' }
  if (v >= 30) return { t: '融資偏高', type: 'warning' }
  return { t: '融資溫和', type: 'success' }
})

async function load() {
  try {
    rows.value = await getMargin(props.stockId, tf.value, BARS[tf.value])
    render()
  } catch (e) {
    ElMessage.error('載入融資融券失敗：' + (e?.response?.data?.detail || e.message))
  }
}
function onResize() { if (chart) chart.resize() }

onMounted(() => { load(); window.addEventListener('resize', onResize) })
watch(tf, load)
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); if (chart) chart.dispose() })
</script>

<template>
  <div>
    <el-radio-group v-model="tf" size="small" style="margin-bottom: 8px">
      <el-radio-button value="D">日</el-radio-button>
      <el-radio-button value="W">週</el-radio-button>
      <el-radio-button value="M">月</el-radio-button>
      <el-radio-button value="Q">季</el-radio-button>
    </el-radio-group>
    <span style="margin-left: 10px; color: #999; font-size: 12px">餘額單位：張</span>
    <template v-if="latest">
      <span style="margin-left: 12px; color: #666; font-size: 13px">
        融資使用率 <b>{{ latest.margin_util ?? '—' }}%</b>　券資比 <b>{{ latest.short_margin_ratio ?? '—' }}%</b>
      </span>
      <el-tag v-if="utilTag" :type="utilTag.type" size="small" effect="dark" style="margin-left: 8px">{{ utilTag.t }}</el-tag>
      <span style="margin-left: 10px; color: #999; font-size: 12px">
        融資使用率＝融資餘額÷融資限額（限額≈發行股數 25%）；越高代表散戶籌碼越重
      </span>
    </template>

    <div ref="el" style="width: 100%; height: 260px"></div>

    <el-table :data="tableRows" height="320" size="small" stripe style="margin-top: 8px">
      <el-table-column label="日期" width="110"><template #default="{ row }">{{ dstr(row.trade_date) }}</template></el-table-column>
      <el-table-column label="融資增減" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.margin_chg) }">{{ sign(row.margin_chg) }}</span></template>
      </el-table-column>
      <el-table-column label="融資餘額" align="right"><template #default="{ row }">{{ num(row.margin_balance) }}</template></el-table-column>
      <el-table-column label="融券增減" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.short_chg) }">{{ sign(row.short_chg) }}</span></template>
      </el-table-column>
      <el-table-column label="融券餘額" align="right"><template #default="{ row }">{{ num(row.short_balance) }}</template></el-table-column>
      <el-table-column label="券資比%" align="right"><template #default="{ row }">{{ row.short_margin_ratio ?? '—' }}</template></el-table-column>
      <el-table-column label="融資使用率%" align="right" width="120"><template #default="{ row }">{{ row.margin_util ?? '—' }}</template></el-table-column>
    </el-table>
  </div>
</template>
