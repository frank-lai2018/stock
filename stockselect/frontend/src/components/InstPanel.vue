<script setup>
// 三大法人買賣超（外資／投信／自營，單位：張）：分項長條 + 合計累積線；下方逐日表。
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getInstTrades } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const tf = ref('D')
const rows = ref([])
const el = ref(null)
let chart = null
const BARS = { D: 60, W: 52, M: 36, Q: 16 }

const tableRows = computed(() => [...rows.value].reverse())     // 表格新到舊
const clr = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
const sign = (v) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + Number(v).toLocaleString('en-US'))
const dstr = (d) => String(d).slice(0, 10)

function render() {
  if (!chart) chart = echarts.init(el.value)
  const x = rows.value.map((r) => dstr(r.trade_date))
  let cum = 0
  const cumTotal = rows.value.map((r) => (cum += Number(r.total_lots || 0)))   // 合計累積
  chart.setOption({
    grid: { left: 60, right: 60, top: 30, bottom: 30 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['外資', '投信', '自營', '合計累積'], top: 0 },
    xAxis: { type: 'category', data: x },
    yAxis: [
      { type: 'value', name: '單日(張)', scale: true },
      { type: 'value', name: '累積(張)', scale: true },
    ],
    series: [
      { name: '外資', type: 'bar', data: rows.value.map((r) => r.foreign_lots), itemStyle: { color: '#EA4C4C' } },
      { name: '投信', type: 'bar', data: rows.value.map((r) => r.trust_lots), itemStyle: { color: '#E6A23C' } },
      { name: '自營', type: 'bar', data: rows.value.map((r) => r.dealer_lots), itemStyle: { color: '#409EFF' } },
      { name: '合計累積', type: 'line', yAxisIndex: 1, showSymbol: false, data: cumTotal,
        lineStyle: { color: '#909399', width: 2 }, itemStyle: { color: '#909399' } },
    ],
  })
}

async function load() {
  try {
    rows.value = await getInstTrades(props.stockId, tf.value, BARS[tf.value])
    render()
  } catch (e) {
    ElMessage.error('載入法人買賣失敗：' + (e?.response?.data?.detail || e.message))
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
    <span style="margin-left: 10px; color: #999; font-size: 12px">單位：張（正＝買超、負＝賣超）；外資含外資自營、自營含避險</span>

    <div ref="el" style="width: 100%; height: 260px"></div>

    <el-table :data="tableRows" height="320" size="small" stripe style="margin-top: 8px">
      <el-table-column label="日期" width="110"><template #default="{ row }">{{ dstr(row.trade_date) }}</template></el-table-column>
      <el-table-column label="外資" align="right"><template #default="{ row }"><span :style="{ color: clr(row.foreign_lots) }">{{ sign(row.foreign_lots) }}</span></template></el-table-column>
      <el-table-column label="投信" align="right"><template #default="{ row }"><span :style="{ color: clr(row.trust_lots) }">{{ sign(row.trust_lots) }}</span></template></el-table-column>
      <el-table-column label="自營" align="right"><template #default="{ row }"><span :style="{ color: clr(row.dealer_lots) }">{{ sign(row.dealer_lots) }}</span></template></el-table-column>
      <el-table-column label="合計" align="right"><template #default="{ row }"><b :style="{ color: clr(row.total_lots) }">{{ sign(row.total_lots) }}</b></template></el-table-column>
    </el-table>
  </div>
</template>
