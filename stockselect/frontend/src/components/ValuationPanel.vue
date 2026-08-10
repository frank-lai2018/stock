<script setup>
// 本益比河流圖：股價 + 歷史 PER 分位換算的價格帶（貴不貴一眼看）
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { getValuation } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const years = ref(3)
const data = ref({ items: [], bands: {}, per_now: null, per_pctile: null, eps_ttm_now: null })
const el = ref(null)
let chart = null

const BANDS = [                                    // 由貴到便宜（畫堆疊面積用）
  { key: 'p90', name: '貴 (90%)', color: '#F6BDBD' },
  { key: 'p75', name: '偏貴 (75%)', color: '#FBD9B5' },
  { key: 'p50', name: '中位 (50%)', color: '#FDF0C0' },
  { key: 'p25', name: '偏便宜 (25%)', color: '#D3EAD5' },
  { key: 'p10', name: '便宜 (10%)', color: '#BEDCF5' },
]
const pctile = computed(() => data.value.per_pctile)
const level = computed(() => {                     // 目前位階的白話結論
  const p = pctile.value
  if (p == null) return null
  if (p >= 80) return { t: '偏貴', type: 'danger' }
  if (p >= 60) return { t: '略偏貴', type: 'warning' }
  if (p > 40) return { t: '合理', type: 'info' }
  if (p > 20) return { t: '略偏便宜', type: 'success' }
  return { t: '便宜', type: 'success' }
})

function render() {
  if (!chart) chart = echarts.init(el.value)
  const items = data.value.items
  const x = items.map((r) => r.trade_date)
  // 由高到低逐條畫，帶面積填到下一條 → 形成「河流」
  const bandSeries = BANDS.map((b, i) => ({
    name: b.name, type: 'line', showSymbol: false, symbol: 'none',
    data: items.map((r) => r[b.key]),
    lineStyle: { color: b.color, width: 1 },
    itemStyle: { color: b.color },
    areaStyle: { color: b.color, opacity: i === BANDS.length - 1 ? 0.25 : 0.55 },
    z: 1,
    stack: null,
  }))
  chart.setOption({
    grid: { left: 64, right: 24, top: 34, bottom: 40 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : Number(v).toFixed(2)) },
    legend: { data: [...BANDS.map((b) => b.name), '收盤價'], top: 0, type: 'scroll' },
    xAxis: { type: 'category', data: x, boundaryGap: false, axisLabel: { formatter: (v) => String(v).slice(0, 7) } },
    yAxis: { type: 'value', name: '股價', scale: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6 }],
    series: [
      ...bandSeries,
      { name: '收盤價', type: 'line', showSymbol: false, data: items.map((r) => r.close),
        lineStyle: { color: '#303133', width: 2 }, itemStyle: { color: '#303133' }, z: 5 },
    ],
  })
}

async function load() {
  try {
    data.value = await getValuation(props.stockId, years.value)
    render()
  } catch (e) {
    ElMessage.error('載入本益比河流圖失敗：' + (e?.response?.data?.detail || e.message))
  }
}
function onResize() { if (chart) chart.resize() }

onMounted(() => { load(); window.addEventListener('resize', onResize) })
watch([years, () => props.stockId], load)
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); if (chart) chart.dispose() })
</script>

<template>
  <div>
    <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px">
      <el-radio-group v-model="years" size="small">
        <el-radio-button :value="1">近 1 年</el-radio-button>
        <el-radio-button :value="3">近 3 年</el-radio-button>
        <el-radio-button :value="5">近 5 年</el-radio-button>
      </el-radio-group>
      <template v-if="data.per_now">
        <span style="color: #666; font-size: 13px">
          目前 PER <b>{{ data.per_now }}</b>　位階 <b>{{ pctile }}%</b>
          （近{{ years }}年中位 {{ data.bands.p50 }}、隱含近四季 EPS {{ data.eps_ttm_now }}）
        </span>
        <el-tag v-if="level" :type="level.type" effect="dark">{{ level.t }}</el-tag>
      </template>
      <span style="color: #999; font-size: 12px">
        帶＝該股歷史 PER 的 10/25/50/75/90 百分位 × 當日隱含 EPS。股價站上高帶＝相對自己歷史偏貴
      </span>
    </div>

    <el-empty v-if="!data.items.length" description="無估值資料（PER 需為正）" :image-size="60" />
    <div v-show="data.items.length" ref="el" style="width: 100%; height: 340px"></div>
  </div>
</template>
