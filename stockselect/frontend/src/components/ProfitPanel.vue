<script setup>
// 獲利能力趨勢：三率（毛利/營益/淨利）折線 + EPS 柱；標出「盈餘加速」的季別
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getProfitability } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const quarters = ref(20)
const rows = ref([])
const el = ref(null)
let chart = null

const UP = '#EA4C4C'
const DOWN = '#3F9E5A'
const tableRows = computed(() => [...rows.value].reverse())     // 表格新到舊
const q = (d) => {                                              // 2026-03-31 → 26Q1
  const [y, m] = String(d).split('-')
  return `${y.slice(2)}Q${Math.ceil(Number(m) / 3)}`
}
const pct = (v) => (v == null ? '—' : Number(v).toFixed(2) + '%')
const sign = (v) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(1) + '%')
const clr = (v) => (v == null ? '' : Number(v) >= 0 ? UP : DOWN)

// 最新一季有沒有盈餘加速（給頂部標籤用）
const last = computed(() => rows.value[rows.value.length - 1] || null)

function render() {
  if (!chart) chart = echarts.init(el.value)
  const x = rows.value.map((r) => q(r.period_date))
  const line = (name, key, color) => ({
    name, type: 'line', yAxisIndex: 1, showSymbol: false, smooth: true,
    data: rows.value.map((r) => r[key]), lineStyle: { color, width: 2 }, itemStyle: { color },
  })
  chart.setOption({
    grid: { left: 56, right: 60, top: 34, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '—' : Number(v).toFixed(2)),
    },
    legend: { data: ['EPS', '毛利率', '營益率', '淨利率'], top: 0 },
    xAxis: { type: 'category', data: x },
    yAxis: [
      { type: 'value', name: 'EPS', scale: true },
      { type: 'value', name: '%', axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      {
        name: 'EPS', type: 'bar', barMaxWidth: 26,
        // 盈餘加速的那一季用紅柱標出來（一眼看到動能起點）
        data: rows.value.map((r) => ({
          value: r.eps,
          itemStyle: { color: r.accel_qoq ? UP : (r.eps >= 0 ? '#F0A0A0' : DOWN) },
        })),
      },
      line('毛利率', 'gross_margin', '#2E7DEE'),
      line('營益率', 'op_margin', '#FF7A00'),
      line('淨利率', 'net_margin', '#8E44AD'),
    ],
  })
}

async function load() {
  try {
    rows.value = await getProfitability(props.stockId, quarters.value)
    render()
  } catch (e) {
    ElMessage.error('載入獲利能力失敗：' + (e?.response?.data?.detail || e.message))
  }
}
function onResize() { if (chart) chart.resize() }

onMounted(() => { load(); window.addEventListener('resize', onResize) })
watch([quarters, () => props.stockId], load)
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); if (chart) chart.dispose() })
</script>

<template>
  <div>
    <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px">
      <el-radio-group v-model="quarters" size="small">
        <el-radio-button :value="8">近 8 季</el-radio-button>
        <el-radio-button :value="12">近 12 季</el-radio-button>
        <el-radio-button :value="20">近 20 季</el-radio-button>
      </el-radio-group>
      <template v-if="last">
        <el-tag v-if="last.accel_qoq" type="danger" effect="dark">盈餘加速（連兩季季增）</el-tag>
        <el-tag v-if="last.accel_yoy" type="danger">年增率逐季擴大</el-tag>
        <span style="color: #666; font-size: 13px">
          最新 {{ q(last.period_date) }}：EPS {{ last.eps ?? '—' }}（季增
          <b :style="{ color: clr(last.eps_qoq) }">{{ sign(last.eps_qoq) }}</b>、年增
          <b :style="{ color: clr(last.eps_yoy) }">{{ sign(last.eps_yoy) }}</b>）
        </span>
      </template>
      <span style="color: #999; font-size: 12px">紅柱＝該季 EPS 連兩季走高（盈餘加速）；三率走揚代表獲利品質同步改善</span>
    </div>

    <div ref="el" style="width: 100%; height: 300px"></div>

    <el-table :data="tableRows" height="300" size="small" stripe style="margin-top: 8px">
      <el-table-column label="季別" width="80"><template #default="{ row }">{{ q(row.period_date) }}</template></el-table-column>
      <el-table-column label="EPS" align="right" width="80"><template #default="{ row }">{{ row.eps ?? '—' }}</template></el-table-column>
      <el-table-column label="EPS季增" align="right" width="96">
        <template #default="{ row }"><span :style="{ color: clr(row.eps_qoq) }">{{ sign(row.eps_qoq) }}</span></template>
      </el-table-column>
      <el-table-column label="EPS年增" align="right" width="96">
        <template #default="{ row }"><span :style="{ color: clr(row.eps_yoy) }">{{ sign(row.eps_yoy) }}</span></template>
      </el-table-column>
      <el-table-column label="毛利率" align="right"><template #default="{ row }">{{ pct(row.gross_margin) }}</template></el-table-column>
      <el-table-column label="營益率" align="right"><template #default="{ row }">{{ pct(row.op_margin) }}</template></el-table-column>
      <el-table-column label="淨利率" align="right"><template #default="{ row }">{{ pct(row.net_margin) }}</template></el-table-column>
      <el-table-column label="ROE" align="right"><template #default="{ row }">{{ pct(row.roe) }}</template></el-table-column>
      <el-table-column label="加速" width="76">
        <template #default="{ row }"><el-tag v-if="row.accel_qoq" size="small" type="danger" effect="plain">加速</el-tag></template>
      </el-table-column>
    </el-table>
  </div>
</template>
