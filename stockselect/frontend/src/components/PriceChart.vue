<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { init, dispose } from 'klinecharts'
import { ElMessage } from 'element-plus'
import { getPrices, getLevels } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const el = ref(null)
let chart = null
let dataList = []           // 目前圖上的資料（供點擊查收盤）
let priceLineId = null      // 點擊後的固定收盤水平線
let levelIds = []           // 壓力/頸線粗線
const levels = ref([])      // 目前的壓力/頸線/支撐（供圖例顯示）
const LVCOLORS = { resistance: '#FF7A00', neckline: '#2E7DEE', support: '#8E44AD' }
const period = ref('D')     // D / W / M
const adj = ref(true)       // 還原
const BARS = { D: 5000, W: 1500, M: 500 }   // 抓「全部」歷史（後端上限 5000）；初始畫面停在最新，往左捲/縮小即可看全部
const LVBARS = { D: 130, W: 130, M: 130 }   // 頸線/壓力只看「近期」~130 日（約半年），避免抓到很久以前的老底

// 自動畫「壓力(橘)」「頸線/支撐(藍)」粗水平線 + 價格標籤
async function drawLevels() {
  if (!chart) return
  try {
    const lv = await getLevels(props.stockId, LVBARS[period.value] || 120)
    levels.value = lv
    levelIds.forEach((id) => { try { chart.removeOverlay(id) } catch (e) { /* ignore */ } })
    levelIds = []
    for (const x of lv) {
      const color = LVCOLORS[x.type] || '#2E7DEE'
      const id = chart.createOverlay({
        name: 'priceLine',
        points: [{ value: x.price }],
        styles: {
          line: { color, size: 3, style: 'solid' },
          text: { color: '#ffffff', backgroundColor: color, size: 12,
                  paddingLeft: 4, paddingRight: 4, paddingTop: 2, paddingBottom: 2 },
        },
      })
      if (id) levelIds.push(id)
    }
  } catch (e) { /* ignore */ }
}

// ① 限制可移動範圍：不要拖曳/捲動到資料以外的空白（KLineChart v9；方法名不符則略過不報錯）
function boundScroll() {
  if (!chart) return
  const safe = (fn, ...a) => {
    try { if (typeof chart[fn] === 'function') { chart[fn](...a); return true } } catch (e) { /* ignore */ }
    return false
  }
  // 用非 0 小值（klinecharts 對 0 可能當未設定而忽略）→ 幾乎不留白、也不能拖進大片空白
  safe('setMaxOffsetLeftDistance', 1)
  safe('setMaxOffsetRightDistance', 1)
  safe('setOffsetRightDistance', 1)
  safe('setLeftMinVisibleBarCount', 3)
  safe('setRightMinVisibleBarCount', 3)
  safe('scrollToRealTime')                 // 把最新資料拉回右邊，消除「資料在左、右邊空白」的狀態
}

// 強制夾制：拖曳/捲動一旦超出資料範圍（右邊或左邊出現空白）就拉回，禁止過捲
let clamping = false
function onRangeChange(r) {
  if (clamping || !chart || !dataList.length) return
  const n = dataList.length
  const realTo = r && r.realTo
  const realFrom = r && r.realFrom
  try {
    if (realTo != null && realTo > n + 1) {          // 右邊空白 → 拉回最新
      clamping = true; chart.scrollToRealTime(); setTimeout(() => { clamping = false }, 30)
    } else if (realFrom != null && realFrom < -1) {  // 左邊空白 → 拉回最舊
      clamping = true; chart.scrollToDataIndex(0, 0); setTimeout(() => { clamping = false }, 30)
    }
  } catch (e) { clamping = false }
}

// ② 點某根 K 棒 → 在「那天收盤價」畫一條固定水平線
function onChartClick(ev) {
  try {
    const rect = el.value.getBoundingClientRect()
    const p = chart.convertFromPixel(
      { x: ev.clientX - rect.left, y: ev.clientY - rect.top },
      { paneId: 'candle_pane' },
    )
    const di = p && (Array.isArray(p) ? p[0]?.dataIndex : p.dataIndex)
    const bar = di != null ? dataList[di] : null
    if (!bar) return
    if (priceLineId) { try { chart.removeOverlay(priceLineId) } catch (e) { /* ignore */ } }
    priceLineId = chart.createOverlay({ name: 'priceLine', points: [{ value: bar.close }] })
  } catch (e) { /* ignore：API 版本差異不影響其他功能 */ }
}

async function load() {
  if (!chart) return
  try {
    const rows = await getPrices(props.stockId, {
      tf: period.value, bars: BARS[period.value], adj: adj.value ? 1 : 0,
    })
    dataList = rows.map((r) => ({
      timestamp: new Date(r.trade_date).getTime(),
      open: +r.open, high: +r.high, low: +r.low, close: +r.close, volume: +r.volume,
    }))
    priceLineId = null
    levelIds = []
    chart.applyNewData(dataList)
    boundScroll()
    drawLevels()
  } catch (e) {
    ElMessage.error('載入 K 線失敗：' + (e?.response?.data?.detail || e.message))
  }
}

const UP = '#EA4C4C'    // 漲：紅（台股慣例）
const DOWN = '#3F9E5A'  // 跌：綠

function onResize() {
  if (chart) chart.resize()
}

onMounted(() => {
  chart = init(el.value)
  // 台股紅漲綠跌
  chart.setStyles({
    candle: {
      bar: {
        upColor: UP, downColor: DOWN, noChangeColor: '#888888',
        upBorderColor: UP, downBorderColor: DOWN,
        upWickColor: UP, downWickColor: DOWN,
      },
    },
  })
  // 主圖疊 6 條均線
  chart.createIndicator({ name: 'MA', calcParams: [5, 10, 20, 60, 120, 240] }, true, { id: 'candle_pane' })
  // 副圖：量(含均量) / MACD / KDJ
  chart.createIndicator({ name: 'VOL', calcParams: [5, 20] })
  chart.createIndicator('MACD')
  chart.createIndicator('KDJ')
  boundScroll()
  try { chart.subscribeAction('onVisibleRangeChange', onRangeChange) } catch (e) { /* ignore */ }
  el.value.addEventListener('click', onChartClick)
  load()
  window.addEventListener('resize', onResize)
})

watch([period, adj], load)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (el.value) el.value.removeEventListener('click', onChartClick)
  if (el.value) dispose(el.value)
  chart = null
})
</script>

<template>
  <div>
    <div style="margin-bottom: 8px; display: flex; gap: 12px; align-items: center">
      <el-radio-group v-model="period" size="small">
        <el-radio-button value="D">日</el-radio-button>
        <el-radio-button value="W">週</el-radio-button>
        <el-radio-button value="M">月</el-radio-button>
      </el-radio-group>
      <el-switch v-model="adj" active-text="還原價" inline-prompt />
      <span style="color: #999; font-size: 12px">
        MA5/10/20/60/120/240｜量+均量｜MACD｜KD（皆由日線計算）
      </span>
    </div>
    <div v-if="levels.length" style="margin: 4px 0; display: flex; gap: 14px; flex-wrap: wrap; font-size: 13px">
      <span v-for="x in levels" :key="x.type" :style="{ color: LVCOLORS[x.type] }">
        ▬ {{ x.label }} {{ x.price }}
      </span>
    </div>
    <div ref="el" style="width: 100%; height: 1120px"></div>
  </div>
</template>
