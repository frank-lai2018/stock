<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { init, dispose } from 'klinecharts'
import { ElMessage } from 'element-plus'
import { getPrices, getLevels, getStockTrades, getDividends, getFundamentals } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const el = ref(null)
let chart = null
let dataList = []            // 目前圖上的資料
let priceLineId = null       // 點擊後的固定收盤水平線
let levelIds = []            // 壓力/頸線 粗線
let tradeIds = []            // 我的買賣點標記
let eventIds = []            // 除權息/財報 事件標記
const subPanes = {}          // name -> paneId（副圖）

const levels = ref([])       // 壓力/頸線/支撐（供圖例）
const LVCOLORS = { resistance: '#FF7A00', neckline: '#2E7DEE', support: '#8E44AD' }
const UP = '#EA4C4C'         // 漲：紅（台股慣例）
const DOWN = '#3F9E5A'       // 跌：綠

const period = ref('D')      // D / W / M
const adj = ref(true)        // 還原
// A：指標開關（預設精簡，去雜訊）
const MA_ALL = [5, 10, 20, 60, 120, 240]
const maSel = ref([5, 20, 60])
const showVol = ref(true)
const showMacd = ref(false)
const showKdj = ref(false)
// C / D：疊圖開關
const showTrades = ref(true)
const showLevels = ref(true)
const showEvents = ref(false)

const BARS = { D: 5000, W: 1500, M: 500 }   // 抓「全部」歷史（後端上限 5000）
const LVBARS = { D: 130, W: 104, M: 60 }    // 壓力/頸線的回看根數（日~半年、週~2年、月~5年）
// B：區間快捷（以「交易日數」定義，依週期換算成 K 棒數）
const PRESET_DAYS = { '1M': 22, '3M': 66, '6M': 132, '1Y': 252, ALL: null }
const PER_DIV = { D: 1, W: 5, M: 21 }

function safeRemove(id) { try { chart.removeOverlay(id) } catch (e) { /* ignore */ } }

// ---------- B：一鍵縮放到最近 N 根 ----------
function setRange(key) {
  if (!chart || !dataList.length) return
  const per = PER_DIV[period.value] || 1
  const n = PRESET_DAYS[key] == null ? dataList.length : Math.max(5, Math.round(PRESET_DAYS[key] / per))
  const w = el.value?.clientWidth || 900
  try {
    chart.setBarSpace(Math.max(1, w / n))
    chart.scrollToRealTime()
  } catch (e) { /* ignore（API 版本差異） */ }
}

// ---------- A：均線 / 副圖 ----------
function setMA() {
  if (!chart) return
  try { chart.removeIndicator('candle_pane', 'MA') } catch (e) { /* ignore */ }
  if (maSel.value.length) {
    try {
      chart.createIndicator({ name: 'MA', calcParams: [...maSel.value].sort((a, b) => a - b) },
        true, { id: 'candle_pane' })
    } catch (e) { /* ignore */ }
  }
}
function setSub(name, on, calcParams) {
  if (!chart) return
  if (on && !subPanes[name]) {
    try {
      const id = chart.createIndicator(calcParams ? { name, calcParams } : name, false)
      if (id) subPanes[name] = id
    } catch (e) { /* ignore */ }
  } else if (!on && subPanes[name]) {
    try { chart.removeIndicator(subPanes[name]) } catch (e) { /* ignore */ }
    delete subPanes[name]
  }
  try { chart.resize() } catch (e) { /* ignore */ }
}

// ---------- D：壓力/頸線/支撐 ----------
async function drawLevels() {
  if (!chart) return
  levelIds.forEach(safeRemove); levelIds = []
  levels.value = []
  if (!showLevels.value) return
  try {
    const lv = await getLevels(props.stockId, LVBARS[period.value] || 120, period.value)
    levels.value = lv
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

// ---------- C：我的買賣點（trade_log）----------
function barAt(ts) {
  let best = null
  for (const b of dataList) { if (b.timestamp <= ts) best = b; else break }
  return best
}
async function drawTrades() {
  if (!chart) return
  tradeIds.forEach(safeRemove); tradeIds = []
  if (!showTrades.value) return
  let ts
  try { ts = await getStockTrades(props.stockId) } catch (e) { return }
  const agg = {}                                   // 同日同動作彙總，避免多筆重疊
  for (const t of ts || []) {
    const day = String(t.trade_date).slice(0, 10)
    const k = day + t.action
    const a = agg[k] || (agg[k] = { day, action: t.action, shares: 0, amt: 0 })
    a.shares += +t.shares; a.amt += +t.shares * +t.price
  }
  for (const k in agg) {
    const a = agg[k]
    const isBuy = a.action === 'buy'
    const price = a.amt / a.shares
    const label = `${isBuy ? '▲買' : '▼賣'} ${Math.round(a.shares)}股 @${price.toFixed(2)}`
    try {
      const id = chart.createOverlay({
        name: 'simpleAnnotation',
        points: [{ timestamp: new Date(a.day).getTime(), value: price }],
        extendData: label,
        styles: { text: { color: '#ffffff', backgroundColor: isBuy ? UP : DOWN,
                          size: 12, paddingLeft: 4, paddingRight: 4, paddingTop: 2, paddingBottom: 2 } },
      })
      if (id) tradeIds.push(id)
    } catch (e) { /* ignore */ }
  }
}

// ---------- D：除權息 / 財報 事件 ----------
async function drawEvents() {
  if (!chart) return
  eventIds.forEach(safeRemove); eventIds = []
  if (!showEvents.value) return
  const mark = (ts, label, color) => {
    const bar = barAt(ts)
    if (!bar) return
    try {
      const id = chart.createOverlay({
        name: 'simpleAnnotation',
        points: [{ timestamp: ts, value: bar.low }],
        extendData: label,
        styles: { text: { color: '#fff', backgroundColor: color, size: 11,
                          paddingLeft: 3, paddingRight: 3, paddingTop: 1, paddingBottom: 1 } },
      })
      if (id) eventIds.push(id)
    } catch (e) { /* ignore */ }
  }
  try {
    const d = await getDividends(props.stockId)
    for (const it of (d.items || [])) {
      const ex = it.ex_cash_date || it.ex_stock_date
      if (ex) mark(new Date(ex).getTime(), '除息', '#B8860B')
    }
  } catch (e) { /* ignore */ }
  try {
    const f = await getFundamentals(props.stockId)
    for (const q of (f.quarterly || [])) {
      if (q.period_date) mark(new Date(q.period_date).getTime(), '財報', '#2E7DEE')
    }
  } catch (e) { /* ignore */ }
}

function redrawOverlays() {
  drawLevels(); drawTrades(); drawEvents()
}

// ---------- 過捲夾制 ----------
function boundScroll() {
  if (!chart) return
  const safe = (fn, ...a) => { try { if (typeof chart[fn] === 'function') { chart[fn](...a) } } catch (e) { /* ignore */ } }
  safe('setMaxOffsetLeftDistance', 1)
  safe('setMaxOffsetRightDistance', 1)
  safe('setOffsetRightDistance', 1)
  safe('setLeftMinVisibleBarCount', 3)
  safe('setRightMinVisibleBarCount', 3)
  safe('scrollToRealTime')
}
let clamping = false
function onRangeChange(r) {
  if (clamping || !chart || !dataList.length) return
  const n = dataList.length
  try {
    if (r && r.realTo != null && r.realTo > n + 1) {
      clamping = true; chart.scrollToRealTime(); setTimeout(() => { clamping = false }, 30)
    } else if (r && r.realFrom != null && r.realFrom < -1) {
      clamping = true; chart.scrollToDataIndex(0, 0); setTimeout(() => { clamping = false }, 30)
    }
  } catch (e) { clamping = false }
}

// ---------- 點某根 K 棒 → 那天收盤畫水平線 ----------
function onChartClick(ev) {
  try {
    const rect = el.value.getBoundingClientRect()
    const p = chart.convertFromPixel(
      { x: ev.clientX - rect.left, y: ev.clientY - rect.top }, { paneId: 'candle_pane' })
    const di = p && (Array.isArray(p) ? p[0]?.dataIndex : p.dataIndex)
    const bar = di != null ? dataList[di] : null
    if (!bar) return
    if (priceLineId) safeRemove(priceLineId)
    priceLineId = chart.createOverlay({ name: 'priceLine', points: [{ value: bar.close }] })
  } catch (e) { /* ignore */ }
}
function clearClickLine() { if (priceLineId) { safeRemove(priceLineId); priceLineId = null } }

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
    priceLineId = null; levelIds = []; tradeIds = []; eventIds = []
    chart.applyNewData(dataList)
    setRange('6M')            // 預設看近半年，不用手拖
    redrawOverlays()
  } catch (e) {
    ElMessage.error('載入 K 線失敗：' + (e?.response?.data?.detail || e.message))
  }
}

function onResize() { if (chart) chart.resize() }

onMounted(() => {
  chart = init(el.value)
  chart.setStyles({
    candle: {
      bar: { upColor: UP, downColor: DOWN, noChangeColor: '#888888',
             upBorderColor: UP, downBorderColor: DOWN, upWickColor: UP, downWickColor: DOWN },
    },
  })
  setMA()
  setSub('VOL', showVol.value, [5, 20])
  setSub('MACD', showMacd.value)
  setSub('KDJ', showKdj.value)
  boundScroll()
  try { chart.subscribeAction('onVisibleRangeChange', onRangeChange) } catch (e) { /* ignore */ }
  el.value.addEventListener('click', onChartClick)
  load()
  window.addEventListener('resize', onResize)
})

watch([period, adj], load)
watch(maSel, setMA, { deep: true })
watch(showVol, (v) => setSub('VOL', v, [5, 20]))
watch(showMacd, (v) => setSub('MACD', v))
watch(showKdj, (v) => setSub('KDJ', v))
watch(showLevels, drawLevels)
watch(showTrades, drawTrades)
watch(showEvents, drawEvents)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (el.value) el.value.removeEventListener('click', onChartClick)
  if (el.value) dispose(el.value)
  chart = null
})
</script>

<template>
  <div>
    <!-- 工具列 -->
    <div style="display: flex; gap: 10px 14px; align-items: center; flex-wrap: wrap; margin-bottom: 6px">
      <el-radio-group v-model="period" size="small">
        <el-radio-button value="D">日</el-radio-button>
        <el-radio-button value="W">週</el-radio-button>
        <el-radio-button value="M">月</el-radio-button>
      </el-radio-group>
      <el-switch v-model="adj" active-text="還原價" inline-prompt size="small" />

      <!-- B：區間快捷 -->
      <el-button-group>
        <el-button v-for="k in ['1M', '3M', '6M', '1Y', 'ALL']" :key="k" size="small" @click="setRange(k)">
          {{ k === 'ALL' ? '全部' : k === '1M' ? '1月' : k === '3M' ? '3月' : k === '6M' ? '半年' : '1年' }}
        </el-button>
      </el-button-group>

      <!-- A：均線多選 -->
      <el-select v-model="maSel" multiple collapse-tags collapse-tags-tooltip size="small"
                 placeholder="均線" style="width: 200px">
        <el-option v-for="m in MA_ALL" :key="m" :label="`MA${m}`" :value="m" />
      </el-select>

      <!-- A：副圖開關 -->
      <el-checkbox v-model="showVol" size="small" label="量" border />
      <el-checkbox v-model="showMacd" size="small" label="MACD" border />
      <el-checkbox v-model="showKdj" size="small" label="KD" border />

      <!-- C/D：疊圖開關 -->
      <el-checkbox v-model="showTrades" size="small" label="買賣點" border />
      <el-checkbox v-model="showLevels" size="small" label="壓力/支撐" border />
      <el-checkbox v-model="showEvents" size="small" label="除息/財報" border />
      <el-button size="small" @click="clearClickLine">清除點擊線</el-button>
    </div>

    <!-- 壓力/頸線/支撐 圖例 -->
    <div v-if="showLevels && levels.length" style="margin: 2px 0 4px; display: flex; gap: 14px; flex-wrap: wrap; font-size: 13px">
      <span v-for="x in levels" :key="x.type" :style="{ color: LVCOLORS[x.type] }">▬ {{ x.label }} {{ x.price }}</span>
    </div>

    <!-- 圖：高度自適應視窗 -->
    <div ref="el" style="width: 100%; height: clamp(500px, calc(100vh - 210px), 980px)"></div>
  </div>
</template>
