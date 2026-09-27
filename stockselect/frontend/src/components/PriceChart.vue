<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { init, dispose, registerOverlay } from 'klinecharts'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPrices, getLevels, getStockTrades, getDividends, getFundamentals,
         getDrawings, addDrawing, updateDrawing, deleteDrawing, clearDrawings } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const el = ref(null)
let chart = null
let dataList = []            // 目前圖上的資料
let priceLineId = null       // 點擊後的固定收盤水平線
let levelIds = []            // 壓力/頸線 粗線
let tradeIds = []            // 我的買賣點標記
let tradeDrawRun = 0         // 非同步繪製版本；關閉/換股後讓舊請求失效
let eventIds = []            // 除權息/財報 事件標記
let drawIds = []             // 手繪圖形（趨勢線等）overlay id
let pendingId = null         // 正在畫、還沒點完點的那一條
const drawDb = {}            // overlay id -> Promise<DB id>（存檔用；剛畫完可能還在寫入）
const subPanes = {}          // name -> paneId（副圖）

const levels = ref([])       // 壓力/頸線/支撐（供圖例）
const LVCOLORS = { resistance: '#FF7A00', neckline: '#2E7DEE', support: '#8E44AD' }
const UP = '#EA4C4C'         // 漲：紅（台股慣例）
const DOWN = '#3F9E5A'       // 跌：綠
const TRADE_OVERLAY_GROUP = 'auto_trade_markers'

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
// 互動開關（縮放 / 移動），關掉即鎖住圖不動
const zoomOn = ref(true)
const scrollOn = ref(true)
// E：手繪工具（趨勢線等），畫完存 DB，依 股票+週期+還原 分組
const DRAW_TOOLS = [
  { key: 'rayLine', name: '趨勢線', hint: '點兩點（低點連低點＝上升趨勢線；高點連高點＝下降趨勢線），往右無限延伸' },
  { key: 'segment', name: '線段', hint: '點兩點，只畫這一段' },
  { key: 'horizontalStraightLine', name: '水平線', hint: '點一點，畫該價位的整條水平線' },
  { key: 'priceChannelLine', name: '平行通道', hint: '點三點：前兩點定主軌（趨勢線），第三點定通道寬度' },
  { key: 'fibonacciLine', name: '費波南希', hint: '點兩點（波段起點→終點），自動畫 0.382/0.5/0.618 等回撤位' },
]
// 磁吸：滑鼠移到 K 棒時，端點自動吸到你指定的那個價（用自訂吸附，非 klinecharts 內建的高低吸附）
const MAGNETS = [
  { key: 'hl', name: '影線高低', desc: '吸到該根 K 棒的最高價/最低價（含上下影線），畫傳統趨勢線用' },
  { key: 'body', name: '實體高低', desc: '吸到實體上下緣＝max(開,收)/min(開,收)，忽略影線的畫法' },
  { key: 'all', name: '四價', desc: '開/高/低/收 四個價中最近的那個' },
  { key: 'close', name: '收盤價', desc: '一律吸到收盤價（收盤價派畫法）' },
  { key: 'off', name: '不吸附', desc: '完全照滑鼠位置，不自動對齊' },
]
const magnet = ref('hl')
const drawTool = ref('')     // 目前選用的工具（''＝不在畫線模式）
const showDraw = ref(true)   // 顯示/隱藏手繪
const drawColor = ref('#2E7DEE')     // 新畫的線顏色（有選取線時同步改它）
const drawWidth = ref(1)             // 線寬
const DRAW_COLORS = ['#2E7DEE', '#EA4C4C', '#3F9E5A', '#FF7A00', '#8E44AD', '#F2C037', '#00A5A5', '#303133']
let selId = null                     // 目前選取的手繪 overlay id（點一下線即選取）
const selected = ref(false)          // 給畫面用（有沒有選取中的線）
const drawHint = () => DRAW_TOOLS.find((t) => t.key === drawTool.value)?.hint || ''

// 線的樣式（顏色/線寬）；費波的水平線與價位文字一起換色
function styleOf(color, width) {
  return { line: { color, size: width }, text: { color: '#ffffff', backgroundColor: color },
           point: { borderColor: color, activeBorderColor: color } }
}

// 吸附：把落點的價位換成該根 K 棒上「最接近的目標價」
function snapValue(dataIndex, value) {
  if (magnet.value === 'off' || dataIndex == null) return value
  const b = dataList[Math.round(dataIndex)]
  if (!b) return value
  const cand = magnet.value === 'hl' ? [b.high, b.low]
    : magnet.value === 'body' ? [Math.max(b.open, b.close), Math.min(b.open, b.close)]
      : magnet.value === 'close' ? [b.close]
        : [b.open, b.high, b.low, b.close]
  return cand.reduce((best, v) => (Math.abs(v - value) < Math.abs(best - value) ? v : best), cand[0])
}
// 直接改寫 overlay 的點（klinecharts 會在本次事件後重繪，所以拖到哪就吸到哪）
function snapPoint(overlay, i) {
  const p = overlay.points?.[i]
  if (p && p.value != null) p.value = snapValue(p.dataIndex, p.value)
}
function snapAll(overlay) { (overlay.points || []).forEach((p, i) => snapPoint(overlay, i)) }

const BARS = { D: 5000, W: 1500, M: 500 }   // 抓「全部」歷史（後端上限 5000）
const LVBARS = { D: 130, W: 104, M: 60 }    // 壓力/頸線的回看根數（日~半年、週~2年、月~5年）
// B：區間快捷（以「交易日數」定義，依週期換算成 K 棒數）
const PRESET_DAYS = { '1M': 22, '3M': 66, '6M': 132, '1Y': 252, ALL: null }
const PER_DIV = { D: 1, W: 5, M: 21 }

function safeRemove(id) { try { chart.removeOverlay(id) } catch (e) { /* ignore */ } }

// 自訂 overlay：整條橫線 + 中文標籤（壓力/頸線/支撐）。只註冊一次。
let levelOverlayReady = false
function ensureLevelOverlay() {
  if (levelOverlayReady) return
  levelOverlayReady = true
  try {
    registerOverlay({
      name: 'levelLine',
      totalStep: 1,
      needDefaultPointFigure: false,
      needDefaultXAxisFigure: false,
      needDefaultYAxisFigure: false,
      createPointFigures: ({ overlay, coordinates, bounding }) => {
        const c = coordinates && coordinates[0]
        if (!c) return []
        const d = overlay.extendData || {}
        const color = d.color || '#2E7DEE'
        return [
          { type: 'line',
            attrs: { coordinates: [{ x: 0, y: c.y }, { x: bounding.width, y: c.y }] },
            styles: { color, size: 2, style: 'solid' } },
          { type: 'text',
            attrs: { x: 6, y: c.y, text: d.text || '', align: 'left', baseline: 'middle' },
            styles: { color: '#ffffff', backgroundColor: color, size: 12,
                      paddingLeft: 5, paddingRight: 5, paddingTop: 2, paddingBottom: 2 } },
        ]
      },
    })
  } catch (e) { /* 已註冊或不支援 → 略過 */ }
}

// 縮放 / 移動 開關（關掉 → 鎖住圖）
function applyInteract() {
  if (!chart) return
  const safe = (fn, v) => { try { if (typeof chart[fn] === 'function') chart[fn](v) } catch (e) { /* ignore */ } }
  safe('setZoomEnabled', zoomOn.value)
  safe('setScrollEnabled', scrollOn.value)
}

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
        name: 'levelLine',
        lock: true,                                   // 自動標線，不可拖動
        points: [{ value: x.price }],
        extendData: { text: `${x.label} ${x.price}`, color },   // 中文標籤 + 價
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
function clearTradeOverlays() {
  if (!chart) return
  // groupId 可移除同組所有標記；逐 id 再清一次，兼容舊版或先前未分組的 overlay。
  try { chart.removeOverlay({ groupId: TRADE_OVERLAY_GROUP }) } catch (e) { /* ignore */ }
  tradeIds.forEach(safeRemove)
  tradeIds = []
}
async function drawTrades() {
  if (!chart) return
  const run = ++tradeDrawRun
  const stockId = props.stockId
  clearTradeOverlays()
  if (!showTrades.value) return
  let ts
  try { ts = await getStockTrades(stockId) } catch (e) { return }
  // 等資料期間若已關閉、換股或又觸發重畫，舊請求不可再把標記畫回來。
  if (!chart || !showTrades.value || stockId !== props.stockId || run !== tradeDrawRun) return
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
        groupId: TRADE_OVERLAY_GROUP,
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

// ---------- E：手繪圖形（趨勢線/水平線/通道/費波）----------
// 只留 timestamp/value 存 DB（dataIndex 會隨資料筆數變動，不存）
function ptsOf(overlay) {
  return (overlay.points || [])
    .filter((p) => p && p.timestamp != null && p.value != null)
    .map((p) => ({ timestamp: p.timestamp, value: p.value }))
}

// 每個手繪 overlay 共用的事件：畫的時候吸附、畫完存檔、拖完回存、右鍵刪除、點選可改色
function drawHooks() {
  return {
    onDrawing: ({ overlay, figureIndex }) => { snapPoint(overlay, figureIndex); return false },
    onDrawEnd: ({ overlay }) => { snapAll(overlay); keepDrawing(overlay); return false },
    onPressedMoveEnd: ({ overlay }) => { snapAll(overlay); saveMoved(overlay); return false },
    onRightClick: ({ overlay }) => { dropDrawing(overlay.id); return false },  // false＝續走預設行為（移除圖形）
    onSelected: ({ overlay }) => { selId = overlay.id; selected.value = true; return false },
    onDeselected: () => { selId = null; selected.value = false; return false },
  }
}
// 畫完 → 寫進 DB；id 用 Promise 存，拖動/刪除若搶在寫入前會等它
function keepDrawing(overlay) {
  pendingId = null
  drawIds.push(overlay.id)
  drawDb[overlay.id] = addDrawing({
    stock_id: props.stockId, period: period.value, adj: adj.value,
    tool: overlay.name, points: ptsOf(overlay), styles: styleOf(drawColor.value, drawWidth.value),
  }).then((r) => r.id).catch((e) => {
    ElMessage.error('手繪存檔失敗：' + (e?.response?.data?.detail || e.message))
    return null
  })
  drawTool.value = ''                    // 畫完自動退出畫線模式，避免連續誤畫
}
async function saveMoved(overlay) {
  const id = await drawDb[overlay.id]
  if (!id) return
  try { await updateDrawing(id, { points: ptsOf(overlay) }) } catch (e) { ElMessage.error('手繪更新失敗') }
}
// 改色/改線寬：有選取的線就套到那條並存檔，否則只當作「下一條要畫的」預設
async function applyStyle() {
  if (!chart || !selId) return
  const styles = styleOf(drawColor.value, drawWidth.value)
  try { chart.overrideOverlay({ id: selId, styles }) } catch (e) { return }
  const id = await drawDb[selId]
  if (id) { try { await updateDrawing(id, { styles }) } catch (e) { ElMessage.error('顏色存檔失敗') } }
}
async function dropDrawing(oid) {
  drawIds = drawIds.filter((x) => x !== oid)
  if (selId === oid) { selId = null; selected.value = false }
  const p = drawDb[oid]
  delete drawDb[oid]
  const id = await p
  if (id) { try { await deleteDrawing(id) } catch (e) { /* ignore */ } }
}
// 進入畫線模式：點兩點（通道三點）即成形，畫完自動退出
function startDraw(tool) {
  if (!chart) return
  cancelDraw()                                     // 換工具：先丟掉上一個沒畫完的
  drawTool.value = tool
  if (!showDraw.value) showDraw.value = true       // 隱藏中就先打開，不然畫了看不到
  try {
    // mode 用 normal：吸附改由 snapPoint 自己做（才能選影線高低 / 實體高低 / 四價 / 收盤）
    pendingId = chart.createOverlay({
      name: tool, mode: 'normal', styles: styleOf(drawColor.value, drawWidth.value), ...drawHooks(),
    })
  } catch (e) { drawTool.value = ''; pendingId = null }
}
function cancelDraw() {
  drawTool.value = ''
  if (pendingId) { safeRemove(pendingId); pendingId = null }   // 只移除那條未完成的
}
// 刪掉目前選取的那條（等同在線上按右鍵）
function delSelected() {
  if (!selId) return ElMessage.info('先點一下要刪的線')
  const oid = selId
  dropDrawing(oid)
  safeRemove(oid)
}
// 從 DB 還原手繪（切換週期/還原價/換股都會重抓；各組各自的線）
async function drawSaved() {
  if (!chart) return
  drawIds.forEach(safeRemove); drawIds = []
  for (const k in drawDb) delete drawDb[k]
  selId = null; selected.value = false
  if (!showDraw.value) return
  let res
  try { res = await getDrawings(props.stockId, period.value, adj.value) } catch (e) { return }
  for (const d of (res.items || [])) {
    try {
      const id = chart.createOverlay({
        name: d.tool, mode: 'normal', points: d.points,
        styles: d.styles || styleOf(drawColor.value, drawWidth.value), ...drawHooks(),
      })
      if (id) { drawIds.push(id); drawDb[id] = Promise.resolve(d.id) }
    } catch (e) { /* 型別不支援 → 略過 */ }
  }
}
// 清空本股本週期的手繪
async function clearDraw() {
  if (!drawIds.length) return ElMessage.info('這個週期沒有手繪')
  try {
    await ElMessageBox.confirm('清除本股此週期／還原設定下的所有手繪？', '確認', { type: 'warning' })
  } catch (e) { return }                 // 按取消
  drawIds.forEach(safeRemove); drawIds = []
  for (const k in drawDb) delete drawDb[k]
  try {
    const r = await clearDrawings(props.stockId, period.value, adj.value)
    ElMessage.success(`已清除 ${r.deleted} 條手繪`)
  } catch (e) { ElMessage.error('清除失敗') }
}

function redrawOverlays() {
  drawLevels(); drawTrades(); drawEvents(); drawSaved()
}

// ---------- 過捲夾制：右邊最多到最新一根、左邊最多到最舊一根 ----------
// klinecharts 內部：呼叫 setMaxOffset*Distance 會切到 Distance 模式，
// maxOffsetDistance.right=0 → 右側偏移夾成 0（資料>可見數時不可能有右白）。
// 注意：applyNewData / setBarSpace 後都要重套，且 maxOffset 要先設（切模式）再設 offset。
function boundScroll() {
  if (!chart) return
  const safe = (fn, ...a) => { try { if (typeof chart[fn] === 'function') { chart[fn](...a) } } catch (e) { /* ignore */ } }
  safe('setMaxOffsetLeftDistance', 0)   // 左邊不留白：最多到最舊一根
  safe('setMaxOffsetRightDistance', 0)  // 右邊不留白：最多到最新一根
  safe('setOffsetRightDistance', 0)     // 最新一根貼齊右緣
}
// 後備硬夾制：萬一某版本 maxOffset 未生效，偵測到露白就同步貼回邊界
let clamping = false
function onRangeChange(r) {
  if (clamping || !chart || !dataList.length) return
  let vr = (r && r.realTo != null) ? r : null
  if (!vr) { try { vr = chart.getVisibleRange() } catch (e) { return } }
  if (!vr || vr.realTo == null) return
  const n = dataList.length
  try {
    if (vr.realTo > n) {                 // 右邊露白 → 貼回最新
      clamping = true; chart.scrollToRealTime(0); clamping = false
    } else if (vr.realFrom < -1) {       // 左邊露白 → 貼回最舊
      clamping = true; chart.scrollToDataIndex(0, 0); clamping = false
    }
  } catch (e) { clamping = false }
}

// ---------- 點某根 K 棒 → 那天收盤畫水平線 ----------
function onChartClick(ev) {
  if (drawTool.value || selId) return  // 畫線模式／點到手繪線：那是下點或選取，不要順手畫收盤線
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
    ++tradeDrawRun              // 讓上一檔／上一週期仍在等待的交易請求失效
    clearTradeOverlays()        // applyNewData 不保證清除 overlay，先主動移除
    const rows = await getPrices(props.stockId, {
      tf: period.value, bars: BARS[period.value], adj: adj.value ? 1 : 0,
    })
    dataList = rows.map((r) => ({
      timestamp: new Date(r.trade_date).getTime(),
      open: +r.open, high: +r.high, low: +r.low, close: +r.close, volume: +r.volume,
    }))
    priceLineId = null; levelIds = []; eventIds = []; drawIds = []; pendingId = null
    chart.applyNewData(dataList)
    setRange('6M')            // 預設看近半年，不用手拖
    boundScroll()            // applyNewData/setBarSpace 會重置限制 → 最後重套過捲邊界
    redrawOverlays()
  } catch (e) {
    ElMessage.error('載入 K 線失敗：' + (e?.response?.data?.detail || e.message))
  }
}

function onResize() { if (chart) chart.resize() }
function onKey(e) { if (e.key === 'Escape' && drawTool.value) cancelDraw() }   // Esc 中止畫線

onMounted(() => {
  ensureLevelOverlay()
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
  applyInteract()
  try { chart.subscribeAction('onVisibleRangeChange', onRangeChange) } catch (e) { /* ignore */ }
  try { chart.subscribeAction('onScroll', onRangeChange) } catch (e) { /* ignore */ }
  el.value.addEventListener('click', onChartClick)
  load()
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKey)
})

watch([period, adj], load)
watch(() => props.stockId, load)      // 換股（同頁換 route 參數）也要重載，手繪才會跟著換
watch(maSel, setMA, { deep: true })
watch(showVol, (v) => setSub('VOL', v, [5, 20]))
watch(showMacd, (v) => setSub('MACD', v))
watch(showKdj, (v) => setSub('KDJ', v))
watch(showLevels, drawLevels)
watch(showTrades, drawTrades)
watch(showEvents, drawEvents)
watch(showDraw, drawSaved)
watch([drawColor, drawWidth], applyStyle)     // 有選取的線 → 立刻改它；沒選 → 只當新線預設
watch([zoomOn, scrollOn], applyInteract)

onBeforeUnmount(() => {
  ++tradeDrawRun
  clearTradeOverlays()
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKey)
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

      <!-- 互動鎖：關掉即固定不動 -->
      <el-checkbox v-model="zoomOn" size="small" label="縮放" border />
      <el-checkbox v-model="scrollOn" size="small" label="移動" border />
    </div>

    <!-- E：手繪工具（畫完自動存檔，依 股票+週期+還原 各自一組） -->
    <div style="display: flex; gap: 10px 12px; align-items: center; flex-wrap: wrap; margin-bottom: 6px">
      <span style="color: #666; font-size: 13px">✏ 畫線</span>
      <el-button-group>
        <el-button v-for="t in DRAW_TOOLS" :key="t.key" size="small"
                   :type="drawTool === t.key ? 'primary' : ''" @click="startDraw(t.key)">{{ t.name }}</el-button>
      </el-button-group>
      <el-button v-if="drawTool" size="small" type="warning" plain @click="cancelDraw">取消</el-button>

      <!-- 磁吸：端點自動對齊到 K 棒的哪個價 -->
      <span style="color: #666; font-size: 13px">吸附</span>
      <el-select v-model="magnet" size="small" style="width: 112px">
        <el-option v-for="m in MAGNETS" :key="m.key" :label="m.name" :value="m.key">
          <span>{{ m.name }}</span>
          <span style="color: #999; font-size: 12px; margin-left: 8px">{{ m.desc }}</span>
        </el-option>
      </el-select>

      <!-- 顏色 / 線寬：有選取的線就改那條，沒選就是下一條的預設 -->
      <el-color-picker v-model="drawColor" size="small" :predefine="DRAW_COLORS" />
      <el-select v-model="drawWidth" size="small" style="width: 88px">
        <el-option label="細 1px" :value="1" />
        <el-option label="中 2px" :value="2" />
        <el-option label="粗 3px" :value="3" />
      </el-select>

      <el-checkbox v-model="showDraw" size="small" label="顯示手繪" border />
      <el-button v-if="selected" size="small" type="danger" plain @click="delSelected">刪除選取</el-button>
      <el-button size="small" @click="clearDraw">清除手繪</el-button>
      <span style="color: #999; font-size: 12px">
        <template v-if="drawTool">{{ drawHint() }}；按「取消」或 Esc 中止</template>
        <template v-else-if="selected"><b style="color: #EA4C4C">已選取一條線</b>：改上面的顏色/線寬會直接套用並存檔；點圖上空白處取消選取</template>
        <template v-else>畫好可拖端點微調（自動存檔）；<b>點一下線＝選取（可改色）</b>、<b>右鍵＝刪除</b>。日/週/月與還原價各自一組</template>
      </span>
    </div>

    <!-- 壓力/頸線/支撐 圖例 -->
    <div v-if="showLevels && levels.length" style="margin: 2px 0 4px; display: flex; gap: 14px; flex-wrap: wrap; font-size: 13px">
      <span v-for="x in levels" :key="x.type" :style="{ color: LVCOLORS[x.type] }">▬ {{ x.label }} {{ x.price }}</span>
    </div>

    <!-- 圖：高度自適應視窗 -->
    <div ref="el" style="width: 100%; height: clamp(500px, calc(100vh - 210px), 980px)"></div>
  </div>
</template>
