<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as XLSX from 'xlsx'
import { getDailyDecision, getThemeOptions } from '../api'
import DecisionHistoryPanel from '../components/DecisionHistoryPanel.vue'
import DecisionResearchPanel from '../components/DecisionResearchPanel.vue'
import GrowthResearchPanel from '../components/GrowthResearchPanel.vue'
import LinBojinGuide from '../components/LinBojinGuide.vue'

const initialQuery = new URLSearchParams(window.location.search)
const initialLin = (initialQuery.get('gate') || 'lin_bojin') === 'lin_bojin'
const router = useRouter()
const loading = ref(false)
const result = ref({ items: [], summary: {}, calibration: [], holdings: { items: [], industry_counts: {} } })
const view = ref(initialLin && initialQuery.get('view') === 'support_recovery' ? 'support_recovery' : 'selected')
const activeTab = ref('today')
const listHead = ref(null)
const themeOptions = ref([])
const themesLoading = ref(false)
const themesFailed = ref(false)

// 帳戶淨值、可用現金記在這台瀏覽器，下次打開不用重填；其他條件每次回到預設
const ACCOUNT_KEY = 'dailyDecision.account'
function savedAccount() {
  try {
    const saved = JSON.parse(localStorage.getItem(ACCOUNT_KEY) || 'null') || {}
    const ok = (v, min) => typeof v === 'number' && Number.isFinite(v) && v >= min
    return {
      ...(ok(saved.capital, 10000) ? { capital: saved.capital } : {}),
      ...(ok(saved.available_capital, 0) ? { available_capital: saved.available_capital } : {}),
    }
  } catch (e) {
    return {}
  }
}

const form = reactive({
  // momentum：篩選條件＋大盤站上 60 日線＋RS 排序、8% 停損 20 日到期；classic：原始規則（保留對照）
  mode: 'momentum',
  // 預設動能模式＋強哥；條件在首次 load 前建立，第一次請求就套用基本面下限。
  gate: initialLin ? 'lin_bojin' : 'trend_template',
  theme_codes: [],
  combine: [],
  combine_logic: 'all',
  ranking: 'rs',
  capital: 1000000,
  available_capital: 1000000,
  max_total_positions: 10,
  max_total_risk_pct: 4,
  risk_per_trade_pct: 0.75,
  max_new_positions: 3,
  max_industry_positions: 2,
  max_position_pct: 25,
  lot_size: 1000,
  min_amt: 20000000,
  recent: 3,
  lookback: 5,
  expiry: 5,
  eps_min: 0,
  revenue_yoy_min: 0,
  gross_margin_chg_min: 0,
  limit: 200,
  ...savedAccount(),
})

const shown = computed(() => {
  const items = result.value.items || []
  if (view.value === 'selected') return items.filter((x) => x.selected)
  if (view.value === 'ready') return items.filter((x) => x.state === 'ready')
  if (view.value === 'momentum') return items.filter((x) => x.state === 'ready' && x.momentum_ok)
  if (view.value === 'consensus') return items.filter((x) => x.consensus_count > 1)
  if (view.value === 'waiting') return items.filter((x) => x.state === 'waiting')
  if (view.value === 'observation') return items.filter((x) => isLinObservation(x))
  if (view.value === 'support_recovery') return items.filter((x) => isSupportObservation(x))
  return items
})

const themeName = (theme) => theme.code === 'tpex:L000:L630' ? 'CCL／銅箔基板' : theme.name
const themeGroups = computed(() => [
  { label: '市場題材', items: themeOptions.value.filter((t) => t.layer === 3) },
  { label: '產業鏈節點', items: themeOptions.value.filter((t) => t.layer === 2) },
])
const QUICK_THEMES = ['memory', 'tpex:L000:L630', 'pcb', 'cpo', 'ai_server', 'cooling', 'passive']
const quickThemes = computed(() => QUICK_THEMES.map((code) => themeOptions.value.find((t) => t.code === code)).filter(Boolean))
const themeFilter = computed(() => result.value.theme_filter || null)
const combinationFilter = computed(() => result.value.combination_filter || null)
const combinationExcluded = computed(() => combinationFilter.value?.excluded_items || [])
const growthConfig = computed(() => result.value.growth_quality_config || null)
const linConfig = computed(() => result.value.lin_bojin_config || null)
const linInfo = (row) => row.lin_bojin || (row.combination_checks || []).find((c) => c.code === 'lin_bojin')?.lin_data || {}
const linObservations = (row) => linInfo(row).observations || (linInfo(row).observation ? [linInfo(row).observation] : [])
const supportObservation = (row) => linObservations(row).find((o) => o.type === 'support_recovery')
const isLinPending = (row) => row.state !== 'ready' && linInfo(row).observation?.eligible
const isLinObservation = (row) => isLinPending(row) && Number(row.eps) > 0 && Number(row.amt20) >= 20000000
const isSupportObservation = (row) => isLinObservation(row) && supportObservation(row)?.eligible
const observationCount = computed(() => (result.value.items || []).filter(isLinObservation).length)
const supportObservationCount = computed(() => (result.value.items || []).filter(isSupportObservation).length)
watch(linConfig, (config) => {
  if (!config && form.gate !== 'lin_bojin' && ['observation', 'support_recovery'].includes(view.value)) view.value = 'selected'
})
const RANKINGS = { rs: 'RS 排序（基準）', growth_quality: '營收品質 → RS（實驗）' }
const growthText = (row) => [row.growth_quality?.reason, ...(row.growth_quality?.warnings || [])].filter(Boolean).join('；')
const canExport = computed(() => shown.value.length || combinationExcluded.value.length)
const themeText = (row) => (row.themes || []).map(themeName).join('、')
const displayThemes = (row) => row.display_themes || []
const displayThemeText = (row) => displayThemes(row).map((t) => t.name).join('、') || '族群待補'
const combinationText = (row) => (row.combination_checks || []).map((c) =>
  `${c.label}：${c.passed ? '通過' : '未通過'}（${c.reason}）`).join('；')
const emptyText = computed(() => {
  if (view.value === 'support_recovery') return '目前篩選條件下沒有符合量縮收復支撐的股票；需最新單季EPS>0，尚未確認買點，不配置部位'
  if (view.value === 'observation') return '目前沒有符合週線壓力或量縮收復支撐、趨勢與EPS>0的提前觀察股票；尚未確認買點，不配置部位'
  if (view.value === 'selected') return '目前沒有本次入選，可切換「全部可執行」或「全部候選」查看買點與原因'
  if (combinationFilter.value) return '目前沒有符合此檢視的組合候選；可切換「全部候選」，或查看下方未通過加強條件的原因'
  if (themeFilter.value) return '所選族群目前沒有符合此檢視的訊號，可切換「全部候選」或其他篩選條件'
  return '目前條件沒有候選'
})

function toggleTheme(code) {
  if (!form.theme_codes.includes(code) && form.theme_codes.length >= 30) {
    return ElMessage.warning('一次最多選擇 30 個族群')
  }
  form.theme_codes = form.theme_codes.includes(code)
    ? form.theme_codes.filter((value) => value !== code) : [...form.theme_codes, code]
}

async function loadThemeOptions() {
  themesLoading.value = true
  themesFailed.value = false
  try {
    themeOptions.value = await getThemeOptions()
  } catch (e) {
    themesFailed.value = true
  } finally {
    themesLoading.value = false
  }
}

const GATES = [
  { value: 'trend_template', label: '趨勢模板', short: '趨勢模板成立',
    rule: '可執行訊號＋趨勢模板成立（多頭排列、RS ≥ 70）' },
  { value: 'breakout', label: '型態突破', short: '型態突破可執行',
    rule: '型態突破可執行（量比 ≥ 1.5、離頸線 5% 內、RS ≥ 70）' },
  { value: 'consolidation', label: '整理突破（實驗）', short: '整理突破＋趨勢模板',
    rule: '振幅收斂、量縮後放量突破前 20 日高點，並限制追價' },
  { value: 'weekly', label: '週線突破（實驗）', short: '週線突破＋日線進場點',
    rule: '週線站上往上的 30 週線、週收盤突破前 52 週最高週收盤；日線回到壓力線～+5% 且轉強才進場' },
  { value: 'reclaim', label: '支撐轉強（實驗）', short: '支撐轉強，隔日盤中過觸發價才買',
    rule: '強勢股放量黑 K 回測支撐、隔天紅 K 收復黑 K 半身；再隔一天盤中漲過紅 K 高點一檔才買' },
  { value: 'w_bottom', label: 'W 底測右底（實驗）', short: 'W 底測右底，隔日盤中過觸發價才買',
    rule: '強漲後回檔 8～15%、縮量回測左底；隔天盤中漲過當日高點一檔才買，開盤高於觸發價 2% 放棄' },
  { value: 'lin_bojin', label: '強哥選股條件', short: '強哥圖卡研究版：日線型態／週線前高壓力帶／量縮收復支撐提前觀察',
    rule: '多方型態＋RS ≥70＋往上的20日線；三種買點擇一，隔日含滑價成交價不得高於頸線5%' },
]
const COMBINATION_OPTIONS = [
  { value: 'trend_template', label: '趨勢模板成立' },
  { value: 'weekly_structure', label: '週線結構成立（已確認突破）' },
  { value: 'weekly', label: '週線買點可執行' },
  { value: 'breakout', label: '型態突破可執行' },
  { value: 'consolidation', label: '整理突破可執行' },
  { value: 'price_action', label: '裸 K 可執行' },
  { value: 'holder_concentration', label: '籌碼集中：近 4 期千張增、400 張以上不減' },
  { value: 'growth_quality', label: '營收成長品質通過（實驗）' },
  { value: 'lin_bojin', label: '強哥選股條件可執行（圖卡研究版）' },
]
const combinationOptions = computed(() => COMBINATION_OPTIONS.filter((c) =>
  c.value !== form.gate && !(form.gate === 'weekly' && c.value === 'weekly_structure')))
const COMBINATION_PRESETS = [
  { label: '量縮收復支撐（提前觀察）', gate: 'lin_bojin', codes: [], mode: 'trend_hold', view: 'support_recovery' },
  { label: '強哥＋趨勢持有60日', gate: 'lin_bojin', codes: [], mode: 'trend_hold' },
  { label: '強哥＋籌碼集中', gate: 'lin_bojin', codes: ['holder_concentration'], mode: 'trend_hold' },
  { label: '週線買點＋趨勢模板', gate: 'weekly', codes: ['trend_template'] },
  { label: '週線買點＋趨勢＋籌碼', gate: 'weekly', codes: ['trend_template', 'holder_concentration'] },
  { label: '成長＋週線＋趨勢＋籌碼', gate: 'weekly', codes: ['growth_quality', 'trend_template', 'holder_concentration'] },
  { label: '週線買點＋整理突破', gate: 'weekly', codes: ['consolidation'] },
  { label: '週線結構＋支撐轉強', gate: 'reclaim', codes: ['weekly_structure'] },
  { label: '週線結構＋W 底買點', gate: 'w_bottom', codes: ['weekly_structure'] },
]
const isPresetActive = (preset) => form.gate === preset.gate && (!preset.mode || form.mode === preset.mode)
  && form.combine_logic === 'all' && form.ranking === 'rs'
  && form.combine.length === preset.codes.length && preset.codes.every((code) => form.combine.includes(code))
  && view.value === (preset.view || 'all')
async function applyCombination(preset) {
  // 先讓舊請求失效，避免切換途中收到的舊結果把觀察檢視重設。
  ++seq
  clearTimeout(timer)
  loading.value = true
  if (preset.mode) form.mode = preset.mode
  form.gate = preset.gate
  form.combine = [...preset.codes]
  form.combine_logic = 'all'
  form.ranking = 'rs'
  view.value = preset.view || 'all'
  // 等條件 watch 排定的計時器後，再由 load 清除它；每次點選只送一個新請求。
  await nextTick()
  const refreshed = await load()
  if (refreshed && preset.view === 'support_recovery' && view.value === preset.view && activeTab.value === 'today') {
    await nextTick()
    listHead.value?.scrollIntoView({ behavior: 'auto', block: 'start' })
  }
}
const isMomentum = computed(() => form.mode !== 'classic')
const gateInfo = computed(() => GATES.find((g) => g.value === form.gate) || GATES[0])
// 表格依「這批資料」算出時的篩選條件判斷，避免切換後、資料還沒回來前顯示錯的原因
const activeGate = computed(() => result.value.gate || form.gate)
const gateMiss = (row) => activeGate.value === 'weekly' ? '週線突破尚未到日線進場點'
  : activeGate.value === 'lin_bojin' ? (linInfo(row).setup_label || '強哥圖卡條件尚未確認')
  : activeGate.value === 'reclaim' ? '沒有支撐轉強訊號' : activeGate.value === 'w_bottom' ? '沒有 W 底測右底訊號'
  : activeGate.value === 'consolidation' ? '整理突破或趨勢模板未成立' : activeGate.value === 'breakout'
  ? (row.breakout_ready ? '' : '型態突破未達可執行')
  : (row.trend_template ? '' : '趨勢模板未成立')
// 大盤濾網：動能模式只在加權指數站上 60 日線時開新倉
const market = computed(() => result.value.market || null)
const marketAlert = computed(() => {
  const m = market.value
  if (!m || m.above == null) return { type: 'warning', title: '大盤資料不足或過期，暫停開新倉' }
  if (m.above) return { type: 'success', title: `加權指數站上 ${m.ma_days} 日線：可開新倉` }
  return { type: 'error', title: `加權指數跌破 ${m.ma_days} 日線：動能模式暫停開新倉` }
})

let timer = null
let seq = 0
const loadedParams = ref({ ...form, theme_codes: '', combine: '' })   // 目前這批結果用的條件（下載 Excel 時寫進「條件與摘要」）
async function load() {
  clearTimeout(timer)
  const id = ++seq
  loading.value = true
  try {
    const params = { ...form, theme_codes: form.theme_codes.join(','), combine: form.combine.join(',') }
    // 提前觀察與 LINE 使用同樣的500檔候選上限，避免只載入前200檔漏掉觀察股。
    if (params.mode !== 'classic' && params.gate === 'lin_bojin') params.limit = 500
    if (!params.theme_codes) delete params.theme_codes
    if (!params.combine || form.mode === 'classic') { delete params.combine; delete params.combine_logic }
    for (const key of ['eps_min', 'revenue_yoy_min', 'gross_margin_chg_min']) {
      if (params[key] == null || params[key] === '') delete params[key]
    }
    const data = await getDailyDecision(params)
    // 條件連續改時，較早送出的請求可能比較晚回來；只採用最後一次的結果
    if (id === seq) {
      result.value = data
      loadedParams.value = params
      return true
    }
    return false
  } catch (e) {
    if (id === seq) ElMessage.error('今日決策載入失敗：' + (e?.response?.data?.detail || e.message))
    return false
  } finally {
    if (id === seq) loading.value = false
  }
}

// 任何條件一改就重算：模式、篩選條件立刻算；其他欄位等停手 0.7 秒（打字、連按 +/− 只算最後一次）
watch(() => ({ ...form, theme_codes: [...form.theme_codes], combine: [...form.combine] }), (now, before) => {
  clearTimeout(timer)
  const valid = new Set(combinationOptions.value.map((c) => c.value))
  if (now.mode === 'classic' && now.ranking !== 'rs') { form.ranking = 'rs'; return }
  if (now.combine.some((code) => !valid.has(code))) {
    form.combine = now.combine.filter((code) => valid.has(code))
    return
  }
  const themesChanged = now.theme_codes.join(',') !== before.theme_codes.join(',')
  const combinationsChanged = now.combine.join(',') !== before.combine.join(',') || now.combine_logic !== before.combine_logic
  const immediate = now.mode !== before.mode || now.gate !== before.gate || now.ranking !== before.ranking || themesChanged || combinationsChanged
  // 首次指定族群時展示全部訊號，避免現金／既有持股限制造成「沒買點」的誤解。
  if (themesChanged && now.theme_codes.length && !before.theme_codes.length && view.value === 'selected') view.value = 'all'
  if (combinationsChanged && now.combine.length && view.value === 'selected') view.value = 'all'
  timer = setTimeout(load, immediate ? 0 : 700)
})
watch(() => [form.capital, form.available_capital], ([capital, available]) => {
  try {
    localStorage.setItem(ACCOUNT_KEY, JSON.stringify({ capital, available_capital: available }))
  } catch (e) {
    // 無痕視窗或封鎖網站資料時存不了，頁面照常可用
  }
})
onBeforeUnmount(() => clearTimeout(timer))

const money = (v) => v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US')
const num = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
const pct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`
const strategyType = (key) => ({ breakout: 'danger', weekly: 'warning', reclaim: 'success', w_bottom: 'info' }[key] || 'primary')
// 支撐轉強、W 底測右底：隔日盤中漲到觸發價才買，交易計畫的價位要換個說法
const isBuyStop = (plan) => plan?.entry_type === 'buy_stop'
const stateMeta = {
  ready: { label: '可執行', type: 'success' },
  waiting: { label: '等待確認', type: 'warning' },
  watch: { label: '觀察', type: 'info' },
}
const confidenceLabel = { low: '低', medium: '中', high: '高' }

function historicalText(row) {
  if (linConfig.value) return '強哥圖卡研究版：績效尚待驗證'
  if (row.combination_checks || row.growth_quality) return '此實驗尚無校準樣本'
  const h = row.historical_reference
  if (!h) return '決策校準累積中'
  if (h.source === 'pattern_20d') {
    return `型態先驗 n=${h.n}｜20日正報酬 ${pct(h.positive_rate)}｜超額 ${h.avg_excess_pct >= 0 ? '+' : ''}${pct(h.avg_excess_pct)}`
  }
  return `分數校準 n=${h.n}｜先到目標 ${pct(h.target_hit_rate)}｜期望 ${h.avg_r >= 0 ? '+' : ''}${num(h.avg_r, 2)}R`
}

function calibrationName(key) {
  return { breakout: '型態突破', price_action: '裸 K', consolidation: '整理突破', weekly: '週線突破',
    reclaim: '支撐轉強', w_bottom: 'W 底測右底', lin_bojin: '強哥圖卡研究版' }[key] || key
}

const MODE_LABELS = { momentum: '動能模式', trend_hold: '趨勢持有', classic: '原始規則' }
const VIEW_LABELS = { selected: '本次入選', ready: '全部可執行', momentum: '通過動能篩選', consensus: '雙策略共識',
  waiting: '等待確認', observation: '提前觀察', support_recovery: '量縮收復支撐', all: '全部候選' }
// 進場條件（後端 entry_conditions）：等待確認的訊號要發生什麼事才會變成可執行；隔日盤中買進的觸發價、可執行型態突破的隔日開盤
const firstCond = (x) => (x.entry_conditions || [])[0] || {}
const condText = (x) => (x.entry_conditions || []).map((c) => `${c.label}：${c.lines.join('；')}`).join('｜')
const xnum = (v, d = 2) => (v == null || v === '' || Number.isNaN(Number(v)) ? '' : Number(Number(v).toFixed(d)))

// 下載 Excel：「決策清單」＝目前表格（依上方檢視），「條件與摘要」＝這批結果的條件、大盤與統計，有持股時加「目前持股」
function downloadXlsx() {
  const rows = shown.value
  if (!canExport.value) return ElMessage.warning('目前檢視沒有資料，可以切到「全部候選」再下載')
  const r = result.value
  const p = loadedParams.value
  const momentum = r.mode !== 'classic'
  // 價位：有算出來就列（資金不足也列，和畫面一樣）；股數、金額只在部位有效時列
  const price = (x) => x.position_plan || {}
  const plan = (x) => (x.position_plan?.valid ? x.position_plan : {})
  const yes = (v) => (v ? '是' : '否')
  const PRICE = '0.00'
  const MONEY = '#,##0'
  // [欄名, 取值, 欄寬, 數字格式]
  const cols = [
    ['決策', (x) => (x.selected ? '本次入選' : stateMeta[x.state]?.label), 9],
    ['目前持有', (x) => (x.held ? '是' : ''), 8],
    ['代號', (x) => x.stock_id, 8],
    ['名稱', (x) => x.name, 12],
    ['熱門族群', displayThemeText, 30],
    ['產業（風險分類）', (x) => x.industry, 18],
    ...(r.theme_filter ? [['篩選命中族群', themeText, 30]] : []),
    ...(r.combination_filter ? [['主買點可執行', (x) => yes(x.combination_ready), 12],
      ['組合檢查與原因', combinationText, 70]] : []),
    ['收盤', (x) => xnum(x.close), 9, PRICE],
    ['RS', (x) => xnum(x.rs_rating, 0), 6],
    ...(r.lin_bojin_config ? [
      ['強哥買點', (x) => linInfo(x).setup_label || '', 22],
      ['型態', (x) => linInfo(x).pattern_name || '', 16],
      ['固定頸線', (x) => xnum(linInfo(x).neckline), 12, PRICE],
      ['突破日期', (x) => linInfo(x).breakout_date || '', 12],
      ['距頸線%', (x) => xnum(linInfo(x).extension_pct), 12],
      ['確認量比', (x) => xnum(linInfo(x).volume_ratio), 12],
      ['提前觀察', (x) => isLinObservation(x) ? linInfo(x).observation.label + '（尚未確認買點）' : '', 30],
      ['週壓力下緣', (x) => xnum(linInfo(x).observation?.pressure_band?.low ?? linInfo(x).pressure_band?.low), 12, PRICE],
      ['週壓力上緣', (x) => xnum(linInfo(x).observation?.pressure_band?.high ?? linInfo(x).pressure_band?.high), 12, PRICE],
      ['週壓力測試次數', (x) => xnum(linInfo(x).observation?.pressure_band?.touch_count ?? linInfo(x).pressure_band?.touch_count, 0), 16],
      ['收復支撐', (x) => xnum(supportObservation(x)?.support), 12, PRICE],
      ['支撐收復訊號日', (x) => supportObservation(x)?.signal_date || '', 16],
      ['上影測壓日期', (x) => supportObservation(x)?.test_date || '', 14],
      ['測壓高點（觀察）', (x) => xnum(supportObservation(x)?.test_high), 18, PRICE],
      ['訊號高點（觀察）', (x) => xnum(supportObservation(x)?.signal_high), 18, PRICE],
      ['訊號低點（觀察）', (x) => xnum(supportObservation(x)?.signal_low), 18, PRICE],
      ['支撐後續確認', (x) => supportObservation(x)?.confirmation || '', 60],
      ['量能與交易限制提醒', (x) => supportObservation(x)?.volume_note || '', 60],
      ['強哥條件檢查', (x) => (linInfo(x).checks || []).map((c) => `${c.label}：${c.passed ? '通過' : '未通過'}（${c.reason}）`).join('；'), 80],
    ] : []),
    ...(r.growth_quality_config ? [
      ['營收品質分(0～100)', (x) => xnum(x.growth_quality?.score, 1), 12],
      ['營收月份', (x) => x.growth_quality?.revenue_month || '', 12],
      ['近3月營收年增%', (x) => xnum(x.growth_quality?.yoy_3m), 13],
      ['前3月營收年增%', (x) => xnum(x.growth_quality?.previous_yoy_3m), 13],
      ['年增加速(百分點)', (x) => xnum(x.growth_quality?.acceleration_pp), 14],
      ['年增趨勢斜率(%／月)', (x) => xnum(x.growth_quality?.excess_slope_pct), 16],
      ['年增趨勢R²%', (x) => xnum(x.growth_quality?.linearity_pct), 13],
      ['營收資料可用', (x) => yes(x.growth_quality?.available), 12],
      ['營收品質通過', (x) => yes(x.growth_quality?.passed), 12],
      ['營收檢查說明', growthText, 85],
    ] : []),
    ['趨勢模板', (x) => yes(x.trend_template), 8],
    ['突破可執行', (x) => yes(x.breakout_ready), 9],
    ...(momentum ? [['通過篩選', (x) => yes(x.momentum_ok), 8],
                    ['未通過原因', (x) => (x.momentum_ok ? '' : gateMiss(x)), 22]] : []),
    ['策略', (x) => x.strategies.map((s) => `${s.label} ${xnum(s.score, 1)} ${s.pattern_name || ''}`.trim()).join('；'), 34],
    ['策略提醒', (x) => x.strategies.filter((s) => s.blockers?.length).map((s) => `${s.label}：${s.blockers.join('、')}`).join('；'), 44],
    ['共識數', (x) => x.consensus_count, 7],
    ['決策分', (x) => xnum(x.decision_score, 1), 7],
    ['校準調整', (x) => xnum(x.calibration_adjustment, 1), 8],
    ['歷史依據', (x) => historicalText(x), 36],
    ['單季EPS', (x) => xnum(x.eps), 8],
    ['EPS YoY%', (x) => xnum(x.eps_yoy, 1), 9],
    ['營收YoY%', (x) => xnum(x.rev_yoy, 1), 9],
    ['毛利率季增(百分點)', (x) => xnum(x.gross_margin_chg, 1), 10],
    ['參考進場', (x) => isLinPending(x) ? '' : xnum(price(x).entry), 9, PRICE],
    ['進場方式', (x) => isLinPending(x) ? '等待買點確認' : (isBuyStop(price(x)) ? '隔日盤中過觸發價' : '隔日開盤'), 14],
    ['追價上限', (x) => isLinPending(x) ? '' : xnum(price(x).max_entry), 9, PRICE],
    ['停損', (x) => isLinPending(x) ? '' : xnum(price(x).stop), 9, PRICE],
    ['目標', (x) => xnum(price(x).target), 9, PRICE],
    ['R/R', (x) => xnum(price(x).rr), 6],
    ['出場規則', (x) => price(x).exit_rule || '', 26],
    ['建議股數', (x) => plan(x).suggested_shares ?? '', 9, MONEY],
    ['下單方式', (x) => plan(x).order_mode || '', 8],
    ['部位金額', (x) => plan(x).position_value ?? '', 11, MONEY],
    ['占淨值%', (x) => xnum(plan(x).capital_pct, 1), 8],
    ['停損風險金額', (x) => plan(x).risk_amount ?? '', 11, MONEY],
    ['實際風險%', (x) => xnum(plan(x).actual_risk_pct), 9],
    ['部位說明', (x) => (x.position_plan?.valid ? '' : x.position_plan?.reason || ''), 22],
    ['進場條件', (x) => condText(x), 60],
    ['觸發價', (x) => xnum(firstCond(x).trigger_low), 9, PRICE],
    ['買點上限', (x) => xnum(firstCond(x).trigger_high), 9, PRICE],
    ['作廢價', (x) => xnum(firstCond(x).invalid_below), 9, PRICE],
    ['條件期限', (x) => firstCond(x).deadline || '', 14],
    ['取捨理由', (x) => x.selection_reason, 40],
  ]
  // formats：第幾欄用什麼數字格式；只套用在數字儲存格（標題列與空白不動）
  const sheet = (aoa, widths, formats = {}) => {
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    ws['!cols'] = widths.map((w) => ({ wch: w }))
    for (let r = 1; r < aoa.length; r++) {
      aoa[r].forEach((_, c) => {
        const cell = ws[XLSX.utils.encode_cell({ r, c })]
        const z = typeof formats === 'function' ? formats(cell?.v, r, c) : formats[c]
        if (cell?.t === 'n' && z) cell.z = z
      })
    }
    return ws
  }
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, sheet(
    [cols.map((c) => c[0]), ...rows.map((x) => cols.map((c) => c[1](x) ?? ''))], cols.map((c) => c[2]),
    Object.fromEntries(cols.map((c, i) => [i, c[3]]).filter(([, z]) => z))), '決策清單')

  const s = r.settings || {}
  const m = r.market || {}
  const sm = r.summary || {}
  const gateName = momentum ? (GATES.find((g) => g.value === r.gate)?.label || r.gate_label || '') : '—'
  const above = m.above === true ? '站上' : m.above === false ? '跌破' : '資料不足或過期'
  const info = [
    ['項目', '內容'],
    ['資料日', r.as_of || ''],
    ['下載時間', new Date().toLocaleString('zh-TW', { hour12: false })],
    ['模式', MODE_LABELS[r.mode] || r.mode],
    ['篩選條件', gateName],
    ...(r.lin_bojin_config ? [
      ['強哥規則版本', r.lin_bojin_config.version],
      ['強哥規則來源', r.lin_bojin_config.source],
      ...r.lin_bojin_config.rules.map((rule) => [`強哥・${rule.item}`, rule.rule]),
    ] : []),
    ['排序方式', RANKINGS[s.ranking || p.ranking] || RANKINGS.rs],
    ...(r.growth_quality_config ? [
      ['營收品質版本', r.growth_quality_config.version],
      ['營收品質條件', r.growth_quality_config.rule],
      ['營收可得日口徑', r.growth_quality_config.timing],
      ['營收品質公式', '近3月年增0～60%映射0～40分；正斜率時R²×30分；加速0～30百分點映射0～30分；各項截斷，不加RS／籌碼分。'],
    ] : []),
    ...(r.combination_filter ? [
      ['加強條件組合方式', r.combination_filter.logic_label],
      ['加強條件', r.combination_filter.conditions.map((c) => c.label).join('、')],
      ['主策略候選', r.combination_filter.candidate_count],
      ['通過加強條件', r.combination_filter.matched_count],
      ['未通過加強條件', r.combination_filter.excluded_count],
      ['匯出未通過筆數', combinationExcluded.value.length],
      ['組合驗證狀態', r.combination_filter.validation],
      ...(r.combination_filter.holder_evaluated_at ? [['籌碼確認時點', r.combination_filter.holder_evaluated_at]] : []),
    ] : []),
    ['族群篩選', (r.theme_filter?.themes || []).map(themeName).join('、') || '全部族群'],
    ...(r.theme_filter ? [['族群成分（不重複）', r.theme_filter.member_count]] : []),
    ['檢視', VIEW_LABELS[view.value]],
    ['匯出筆數', rows.length],
    ['全部候選', r.total ?? r.count ?? ''],
    ...((r.total || 0) > (r.count || 0)
      ? [['備註', `候選共 ${r.total} 檔，頁面只載入前 ${r.count} 檔，這份檔案只含頁面上的資料`]] : []),
    [r.theme_filter || r.combination_filter ? '全市場掃描檔數' : '掃描檔數', r.scanned ?? ''],
    ['本次入選', sm.selected ?? 0],
    ['可執行候選', sm.ready ?? 0],
    ...(momentum ? [['通過動能篩選', sm.ready_momentum ?? 0]] : []),
    ['等待確認', sm.waiting ?? 0],
    ['雙策略共識', sm.consensus ?? 0],
    ['剩餘可用資金', sm.remaining_capital ?? ''],
    ['剩餘總風險額度', sm.remaining_risk_budget ?? ''],
    ...(momentum ? [['大盤濾網', m.close == null ? '大盤資料不足或過期'
      : `加權指數 ${m.close}｜${m.ma_days} 日線 ${m.ma ?? '—'}｜${above}（資料日 ${m.date || '—'}）`]] : []),
    ['帳戶淨值', s.capital ?? p.capital],
    ['可用現金', s.available_capital ?? p.available_capital],
    ['每檔風險 %', s.risk_per_trade_pct ?? p.risk_per_trade_pct],
    ['最多新倉', s.max_new_positions ?? p.max_new_positions],
    ['總持股上限', s.max_total_positions ?? p.max_total_positions],
    ['總停損風險 %', s.max_total_risk_pct ?? p.max_total_risk_pct],
    ['同產業上限', s.max_industry_positions ?? p.max_industry_positions],
    ['單檔資金上限 %', s.max_position_pct ?? p.max_position_pct],
    ['交易單位', (s.lot_size ?? p.lot_size) === 1 ? '可用零股' : '整張優先'],
    ['流動性（20 日均額 ≥ 元）', p.min_amt],
    ['單季 EPS ≥', p.eps_min ?? '不限'],
    ['營收 YoY ≥ %', p.revenue_yoy_min ?? '不限'],
    ['毛利率季增 ≥ 百分點', p.gross_margin_chg_min ?? '不限'],
    ['突破觀察', `近 ${p.recent} 日`],
    ['模型版本', s.model_version || ''],
    ['方法', r.method || ''],
    ['提醒', '入選代表通過目前規則與資金限制，不是自動買進指令；隔日成交時須重新核對股數、停損、產業與總風險。'],
  ]
  // 金額、張數這類大整數加千分位；0.75 這種比例不套，免得被四捨五入成 1
  XLSX.utils.book_append_sheet(wb, sheet(info, [22, 100],
    (v) => (Number.isInteger(v) && Math.abs(v) >= 1000 ? MONEY : null)), '條件與摘要')
  if (combinationExcluded.value.length) {
    XLSX.utils.book_append_sheet(wb, sheet([
      ['代號', '名稱', '熱門族群', '篩選命中族群', '收盤', 'RS', '組合檢查與原因'],
      ...combinationExcluded.value.map((x) => [x.stock_id, x.name, displayThemeText(x), themeText(x), xnum(x.close), xnum(x.rs_rating, 0), combinationText(x)]),
    ], [8, 14, 30, 30, 10, 8, 100], { 4: PRICE }), '組合未通過')
  }

  const held = r.holdings?.items || []
  if (held.length) {
    XLSX.utils.book_append_sheet(wb, sheet([
      ['代號', '名稱', '產業', '股數', '收盤', '市值'],
      ...held.map((h) => [h.stock_id, h.name, h.industry, h.shares, xnum(h.close), xnum(h.market_value, 0)]),
    ], [8, 12, 14, 10, 10, 14], { 3: MONEY, 4: PRICE, 5: MONEY }), '目前持股')
  }
  const stamp = (r.as_of || '').replace(/-/g, '') || 'result'
  const gatePart = momentum ? `_${gateName.replace('（實驗）', '')}` : ''
  const themePart = r.theme_filter ? '_族群篩選' : ''
  const combinationPart = r.combination_filter ? `_組合${r.combination_filter.logic_label}` : ''
  XLSX.writeFile(wb, `今日決策_${stamp}_${MODE_LABELS[r.mode] || r.mode}${gatePart}${themePart}${combinationPart}_${VIEW_LABELS[view.value]}.xlsx`)
}

onMounted(() => { load(); loadThemeOptions() })
</script>

<template>
  <div class="decision-center">
    <el-tabs v-model="activeTab" class="decision-tabs">
      <el-tab-pane label="今日決策" name="today">
    <el-card shadow="never" class="toolbar">
      <div class="title-row">
        <div>
          <h2>今日決策中心</h2>
          <div v-if="isMomentum" class="muted">{{ gateInfo.short }}、大盤站上 60 日線才開新倉；{{ RANKINGS[form.ranking] }}。</div>
          <div v-else class="muted">把突破與裸 K 合併成一張可執行清單；同時檢查持股產業重疊與單筆風險。</div>
        </div>
        <div class="title-actions">
          <el-radio-group v-model="form.mode" size="large">
            <el-radio-button value="momentum">動能模式</el-radio-button>
            <el-radio-button value="trend_hold">趨勢持有（實驗）</el-radio-button>
            <el-radio-button value="classic">原始規則</el-radio-button>
          </el-radio-group>
          <template v-if="isMomentum">
            <span class="gate-label">主買點</span>
            <el-radio-group v-model="form.gate" size="large">
              <el-radio-button v-for="g in GATES" :key="g.value" :value="g.value">{{ g.label }}</el-radio-button>
            </el-radio-group>
          </template>
          <el-button type="primary" size="large" :loading="loading" @click="load">重新計算</el-button>
        </div>
      </div>

      <div v-if="isMomentum" class="combination-controls">
        <div class="combination-heading">
          <b>組合篩選</b>
          <el-radio-group v-model="form.combine_logic" size="small" aria-label="加強條件組合方式">
            <el-radio-button value="all">全部符合（較嚴格）</el-radio-button>
            <el-radio-button value="any">任一符合</el-radio-button>
          </el-radio-group>
          <el-button size="small" :plain="!!form.combine.length" :type="!form.combine.length ? 'primary' : ''"
                     @click="form.combine = []">單一策略</el-button>
        </div>
        <el-checkbox-group v-model="form.combine" class="combination-checkboxes" aria-label="加強條件（可複選）">
          <el-checkbox v-for="c in combinationOptions" :key="c.value" :value="c.value">{{ c.label }}</el-checkbox>
        </el-checkbox-group>
        <div class="theme-shortcuts">
          <el-button v-for="preset in COMBINATION_PRESETS" :key="preset.label" size="small"
                     :type="isPresetActive(preset) ? 'primary' : ''" :plain="!isPresetActive(preset)"
                     :aria-pressed="isPresetActive(preset)" :loading="loading && isPresetActive(preset)"
                     @click="applyCombination(preset)">{{ preset.label }}</el-button>
        </div>
        <div class="muted small">主買點必須可執行，再檢查勾選的加強條件；預設全部符合。週線結構只認已收完且有效的突破，接近壓力或本週暫時突破不算。籌碼集中比較最新與四期前比例，需五期完整週資料；近四期淨增加與連續增加不同。支撐轉強、W 底請選為主買點，再搭配週線條件。</div>
      </div>

      <div class="theme-filter">
        <div class="theme-controls">
          <label for="decision-theme-select">熱門族群</label>
          <el-select id="decision-theme-select" v-model="form.theme_codes" class="theme-select"
                     multiple filterable clearable collapse-tags collapse-tags-tooltip :max-collapse-tags="3"
                     :multiple-limit="30" :loading="themesLoading" aria-label="熱門族群（可複選）"
                     placeholder="全部族群，可複選記憶體、CCL 等">
            <el-option-group v-for="group in themeGroups" :key="group.label" :label="group.label">
              <el-option v-for="t in group.items" :key="t.code" :label="`${themeName(t)}（${t.n} 檔）`" :value="t.code" />
            </el-option-group>
          </el-select>
          <el-button v-if="themesFailed" size="small" type="warning" @click="loadThemeOptions">重試載入族群</el-button>
        </div>
        <div class="theme-shortcuts">
          <el-button size="small" :type="!form.theme_codes.length ? 'primary' : ''"
                     :plain="!!form.theme_codes.length" @click="form.theme_codes = []">全部族群</el-button>
          <el-button v-for="t in quickThemes" :key="t.code" size="small"
                     :type="form.theme_codes.includes(t.code) ? 'primary' : ''" :plain="!form.theme_codes.includes(t.code)"
                     :aria-pressed="form.theme_codes.includes(t.code)" @click="toggleTheme(t.code)">{{ themeName(t) }}</el-button>
        </div>
        <div class="muted small">可複選，符合任一族群就列入；僅篩選今日決策，買點沿用上方條件。既有持股仍納入全帳戶風險。</div>
      </div>

      <div class="filters">
        <label>帳戶淨值</label>
        <el-input-number v-model="form.capital" :min="10000" :step="100000" controls-position="right" />
        <label>可用現金</label>
        <el-input-number v-model="form.available_capital" :min="0" :step="100000" controls-position="right" />
        <label>每檔風險</label>
        <el-input-number v-model="form.risk_per_trade_pct" :min="0.1" :max="5" :step="0.1" :precision="2" controls-position="right" style="width: 120px" />
        <span class="unit">%</span>
        <label>最多新倉</label>
        <el-input-number v-model="form.max_new_positions" :min="1" :max="10" controls-position="right" style="width: 100px" />
        <label>總持股上限</label>
        <el-input-number v-model="form.max_total_positions" :min="1" :max="50" controls-position="right" style="width: 100px" />
        <label>總停損風險</label>
        <el-input-number v-model="form.max_total_risk_pct" :min="0.1" :max="20" :step="0.5" controls-position="right" style="width: 105px" />
        <span class="unit">%</span>
        <label>同產業上限</label>
        <el-input-number v-model="form.max_industry_positions" :min="1" :max="10" controls-position="right" style="width: 100px" />
        <label>單檔資金上限</label>
        <el-input-number v-model="form.max_position_pct" :min="5" :max="100" :step="5" controls-position="right" style="width: 105px" />
        <span class="unit">%</span>
        <el-select v-model="form.lot_size" style="width: 105px">
          <el-option label="整張優先" :value="1000" />
          <el-option label="可用零股" :value="1" />
        </el-select>
      </div>

      <div class="filters secondary">
        <template v-if="isMomentum">
          <label>候選排序</label>
          <el-select v-model="form.ranking" aria-label="候選排序" style="width: 220px">
            <el-option v-for="(label, value) in RANKINGS" :key="value" :label="label" :value="value" />
          </el-select>
        </template>
        <label>流動性</label>
        <el-select v-model="form.min_amt" style="width: 145px">
          <el-option label="均額 2千萬+" :value="20000000" />
          <el-option label="均額 5千萬+" :value="50000000" />
          <el-option label="均額 1億+" :value="100000000" />
        </el-select>
        <label>單季 EPS ≥</label>
        <el-input-number v-model="form.eps_min" :step="0.5" :precision="2" controls-position="right" style="width: 120px" />
        <label>營收 YoY ≥</label>
        <el-input-number v-model="form.revenue_yoy_min" :step="5" :precision="1" controls-position="right" style="width: 115px" />
        <span class="unit">%</span>
        <label>毛利率季增 ≥</label>
        <el-input-number v-model="form.gross_margin_chg_min" :step="0.5" :precision="1" controls-position="right" style="width: 115px" />
        <span class="unit">百分點</span>
        <label>突破觀察</label>
        <el-select v-model="form.recent" style="width: 105px">
          <el-option label="近 3 日" :value="3" />
          <el-option label="近 2 週" :value="10" />
        </el-select>
      </div>
    </el-card>

    <div v-if="themeFilter" class="scope-line">
      <b>目前族群：</b>
      <el-tag v-for="t in themeFilter.themes" :key="t.code" effect="plain">{{ themeName(t) }}</el-tag>
      <span class="muted small">成分 {{ themeFilter.member_count }} 檔（不重複）・以下統計與入選在所選族群內計算</span>
    </div>

    <div v-if="combinationFilter" class="combination-scope">
      <b>目前組合：{{ combinationFilter.primary_label }}＋加強條件（{{ combinationFilter.logic_label }}）</b>
      <div class="gate-tags"><el-tag v-for="c in combinationFilter.conditions" :key="c.code" effect="plain">{{ c.label }}</el-tag></div>
      <div class="small">主策略候選 {{ combinationFilter.candidate_count }} 檔 → 通過加強條件 {{ combinationFilter.matched_count }} 檔；未通過 {{ combinationFilter.excluded_count }} 檔。可執行仍須主買點與帳戶限制通過。</div>
      <div class="small muted">{{ combinationFilter.validation }}</div>
      <div v-if="combinationFilter.holder_evaluated_at" class="small muted">籌碼確認時點：{{ new Date(combinationFilter.holder_evaluated_at).toLocaleString('zh-TW', { hour12: false }) }}（使用本次查詢已取得的資料；股價基準另見資料日）</div>
    </div>

    <div v-if="growthConfig" class="combination-scope">
      <b>營收成長品質（實驗）</b>
      <div class="small">{{ growthConfig.rule }}。資料需18個連續月份；{{ RANKINGS[growthConfig.ranking] }}，評分不會改變買點狀態。</div>
      <div class="small muted">{{ growthConfig.timing }}。僅換排序不會剔除不通過者；勾選加強條件才會篩除（依全部／任一符合）。</div>
      <details class="small"><summary>評分與資料說明</summary>
        <p>用近6期「當月營收／去年同月營收」的對數做線性回歸，觀察年增倍率的持續性。斜率顯示年增倍率每月變化，並非一般營收月增率。</p>
        <p>品質分＝近3月年增0～60%映射0～40分＋正斜率時R²×30分＋年增加速0～30百分點映射0～30分；各項截斷。RS、籌碼及技術買點獨立判斷。分數不是勝率；低基期另列提醒。</p>
      </details>
    </div>

    <div class="summary-grid" :class="{ six: isMomentum }">
      <el-card shadow="never" class="summary selected" @click="view = 'selected'">
        <span>本次入選</span><b>{{ result.summary?.selected ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary ready" @click="view = 'ready'">
        <span>可執行候選</span><b>{{ result.summary?.ready ?? 0 }}</b>
      </el-card>
      <el-card v-if="isMomentum" shadow="never" class="summary momentum" @click="view = 'momentum'">
        <span>通過動能篩選</span><b>{{ result.summary?.ready_momentum ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary consensus" @click="view = 'consensus'">
        <span>雙策略共識</span><b>{{ result.summary?.consensus ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary" @click="view = 'all'">
        <span>全部候選</span><b>{{ result.total ?? result.count ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary meta">
        <span>剩餘可用資金</span><b class="money">{{ money(result.summary?.remaining_capital) }}</b>
      </el-card>
    </div>

    <el-alert v-if="isMomentum" :type="marketAlert.type" :closable="false" show-icon class="notice">
      <template #title>{{ marketAlert.title }}</template>
      <template v-if="market?.close != null">
        加權指數 {{ money(market.close) }}｜{{ market.ma_days }} 日線 {{ money(market.ma) }}
        <template v-if="market.gap_pct != null">（{{ market.gap_pct >= 0 ? '+' : '' }}{{ num(market.gap_pct, 2) }}%）</template>
        ・資料日 {{ market.date }}
      </template>
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="notice">
      <template #title>入選代表通過目前規則與資金限制，不是自動買進指令</template>
      {{ result.method }}
      <div class="backtest-note">{{ result.validation?.message }} 比較數據請見「{{ growthConfig ? '營收品質研究' : '策略研究' }}」，實驗尚未證明樣本外有效。</div>
      <div>現有交易帳沒有停損欄位，總風險暫以持股市值的 8% 估計；隔日成交時須重新核對股數與風險。</div>
    </el-alert>
    <el-alert v-if="result.data_health && (!result.data_health.healthy || result.data_health.stale_prices)"
              :type="result.data_health.healthy ? 'info' : 'error'" :closable="false" class="notice">
      {{ result.data_health.reason || `已排除 ${result.data_health.stale_prices} 檔過期行情` }}
    </el-alert>
    <el-alert v-if="result.holdings?.items?.length && result.summary?.remaining_risk_budget === 0"
              type="warning" :closable="false" class="notice">
      現有持股的估計停損風險已用盡總額度，暫不新增部位。帳戶淨值與可用現金請填入實際金額（會記在這台瀏覽器）；現有持股風險暫估市值的 8%。
    </el-alert>

    <div ref="listHead" class="list-head">
      <div class="list-actions">
        <el-radio-group v-model="view" size="small">
          <el-radio-button value="selected">本次入選</el-radio-button>
          <el-radio-button value="ready">全部可執行</el-radio-button>
          <el-radio-button v-if="isMomentum" value="momentum">通過動能篩選</el-radio-button>
          <el-radio-button value="consensus">雙策略共識</el-radio-button>
          <el-radio-button value="waiting">等待確認</el-radio-button>
          <el-radio-button v-if="linConfig" value="observation">提前觀察（{{ observationCount }}）</el-radio-button>
          <el-radio-button v-if="linConfig" value="support_recovery">量縮收復支撐（{{ supportObservationCount }}）</el-radio-button>
          <el-radio-button value="all">全部候選</el-radio-button>
        </el-radio-group>
        <el-button size="small" type="success" :disabled="loading || !canExport"
                   :title="canExport ? '下載目前表格，另附條件、摘要與組合未通過原因' : '目前檢視沒有資料，可以切到「全部候選」'"
                   @click="downloadXlsx">⬇ 下載 Excel</el-button>
      </div>
      <span class="muted">
        資料日 {{ result.as_of || '—' }}｜{{ themeFilter || combinationFilter ? '全市場掃描' : '掃描' }} {{ result.scanned || 0 }} 檔｜顯示 {{ shown.length }} 檔
        <template v-if="result.total > result.count">（候選共 {{ result.total }} 檔，表格只列前 {{ result.count }} 檔）</template>
      </span>
    </div>

    <el-alert v-if="view === 'support_recovery' && linConfig" type="info" :closable="false" show-icon class="notice"
              :title="`量縮收復支撐｜提前觀察 ${supportObservationCount} 檔`">
      上影線測壓後，量縮回檔並收復前五日支撐；需 RS≥70、站上往上的20日線、最新單季EPS&gt;0。
      候選依行情日及目前篩選條件更新；僅列提前觀察，尚未確認買點、不配置部位。完整定義可展開下方「查看條件定義」。
    </el-alert>

    <LinBojinGuide v-if="linConfig" :config="linConfig" />

    <el-table v-loading="loading" :data="shown" stripe border
              :empty-text="emptyText" style="cursor: pointer"
              @row-click="(row) => router.push(`/stock/${row.stock_id}`)">
      <el-table-column label="決策" width="96" fixed>
        <template #default="{ row }">
          <el-tag v-if="row.selected" type="success" effect="dark">本次入選</el-tag>
          <el-tag v-else :type="stateMeta[row.state]?.type" effect="plain">{{ stateMeta[row.state]?.label }}</el-tag>
          <div v-if="row.held" class="held">目前持有</div>
        </template>
      </el-table-column>
      <el-table-column label="股票" width="145" fixed>
        <template #default="{ row }">
          <b>{{ row.stock_id }} {{ row.name }}</b>
          <div v-if="displayThemes(row).length" class="stock-themes">
            <el-tag v-for="t in displayThemes(row).slice(0, 2)" :key="t.code"
                    size="small" type="info" effect="plain" :title="t.full_name || t.name">{{ t.name }}</el-tag>
            <el-tooltip v-if="displayThemes(row).length > 2" :content="displayThemeText(row)" placement="top">
              <el-tag size="small" type="info" effect="plain">+{{ displayThemes(row).length - 2 }}</el-tag>
            </el-tooltip>
          </div>
          <div v-else class="muted small">族群待補</div>
          <div class="small">收 {{ num(row.close, 2) }}・RS {{ num(row.rs_rating, 0) }}</div>
          <div class="gate-tags">
            <el-tag v-if="row.trend_template" size="small" type="danger" effect="plain">趨勢模板</el-tag>
            <el-tag v-if="row.breakout_ready" size="small" type="warning" effect="plain">突破可執行</el-tag>
          </div>
          <div v-if="isMomentum && !row.momentum_ok" class="muted small">{{ gateMiss(row) }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="themeFilter" label="篩選命中族群" min-width="175">
        <template #default="{ row }">
          <div class="gate-tags"><el-tag v-for="t in row.themes || []" :key="t.code" size="small" effect="plain">{{ themeName(t) }}</el-tag></div>
        </template>
      </el-table-column>
      <el-table-column label="策略共識" min-width="190">
        <template #default="{ row }">
          <div v-for="s in row.strategies" :key="s.key" class="strategy-line">
            <el-tag :type="strategyType(s.key)" size="small" effect="plain" :title="s.label">{{ s.label.replace('（實驗）', '') }}</el-tag>
            <b>{{ num(s.score, 1) }}</b>
            <span>{{ s.pattern_name }}</span>
          </div>
          <div v-if="row.consensus_count > 1" class="consensus-text">+{{ row.consensus_bonus }} 分共識</div>
        </template>
      </el-table-column>
      <el-table-column label="決策分" width="100" align="center" sortable prop="decision_score">
        <template #default="{ row }">
          <div class="big-score">{{ num(row.decision_score, 1) }}</div>
          <div v-if="row.calibration_adjustment" class="small" :class="row.calibration_adjustment > 0 ? 'up' : 'down'">
            校準 {{ row.calibration_adjustment > 0 ? '+' : '' }}{{ row.calibration_adjustment }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="基本面確認" width="155">
        <template #default="{ row }">
          <div>單季 EPS {{ num(row.eps, 2) }}</div>
          <div>EPS YoY {{ pct(row.eps_yoy) }}</div>
          <div>營收 YoY {{ pct(row.rev_yoy) }}</div>
          <div>毛利季增 {{ pct(row.gross_margin_chg) }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="linConfig" label="強哥條件檢查" min-width="265">
        <template #default="{ row }">
          <b>{{ linInfo(row).setup_label || '尚無此買點' }}</b>
          <div v-if="isLinObservation(row)" class="small observation-note">
            <div v-for="observed in linObservations(row)" :key="observed.type">
              <el-tag size="small" type="warning" effect="plain">{{ observed.label }}・尚未確認買點</el-tag>
              <template v-if="observed.type === 'support_recovery'">
                <div>收復支撐 {{ num(observed.support, 2) }}・訊號 {{ observed.signal_date }}・追蹤第 {{ observed.age_days }} 日</div>
                <div>上影測壓 {{ observed.test_date }}・高點 {{ num(observed.test_high, 2) }}</div>
                <div>訊號高／低 {{ num(observed.signal_high, 2) }}／{{ num(observed.signal_low, 2) }}・訊號量比 {{ num(observed.volume_ratio, 2) }}</div>
                <div>{{ observed.confirmation }}</div>
                <div class="muted">{{ observed.volume_note }}</div>
              </template>
              <template v-else-if="observed.pressure_band">
                <div>週壓力 {{ num(observed.pressure_band.low, 2) }}～{{ num(observed.pressure_band.high, 2) }}・{{ observed.pressure_band.touch_count }} 次測試</div>
                <div>距壓力上緣 {{ pct(observed.extension_pct, 2) }}・觀察量比 {{ num(observed.volume_ratio, 2) }}；不配置部位</div>
              </template>
            </div>
          </div>
          <div v-else-if="linInfo(row).pressure_band" class="small">週壓力 {{ num(linInfo(row).pressure_band.low, 2) }}～{{ num(linInfo(row).pressure_band.high, 2) }}・{{ linInfo(row).pressure_band.touch_count }} 次測試</div>
          <template v-if="linInfo(row).neckline != null">
            <div class="small">{{ linInfo(row).pattern_name }}・頸線 {{ num(linInfo(row).neckline, 2) }}</div>
            <div class="small">距頸線 {{ pct(linInfo(row).extension_pct, 2) }}・量比 {{ num(linInfo(row).volume_ratio, 2) }}</div>
          </template>
          <div v-if="linInfo(row).breakout_date" class="small muted">突破 {{ linInfo(row).breakout_date }}・第 {{ linInfo(row).age_days }} 個交易日</div>
          <div v-for="c in linInfo(row).checks || []" :key="c.label" class="small" :class="c.passed ? 'down' : 'muted'">
            {{ c.passed ? '✓' : '待確認' }} {{ c.label }}（{{ c.reason }}）
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="combinationFilter" label="組合檢查" min-width="240">
        <template #default="{ row }">
          <div v-for="c in row.combination_checks || []" :key="`${c.role}-${c.code}`" class="combination-check">
            <el-tag :type="c.passed ? 'success' : 'warning'" size="small" effect="plain" :title="c.reason">{{ c.passed ? '✓' : '待確認' }} {{ c.label }}</el-tag>
            <div v-if="!c.passed" class="small muted">{{ c.reason }}</div>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="growthConfig" label="營收品質" min-width="240">
        <template #default="{ row }">
          <b>{{ num(row.growth_quality?.score, 1) }} 分</b>
          <el-tag size="small" :type="row.growth_quality?.passed ? 'success' : 'info'" style="margin-left: 6px">{{ row.growth_quality?.passed ? '品質通過' : '未通過' }}</el-tag>
          <div class="small">{{ row.growth_quality?.revenue_month?.slice(0, 7) || '無可用月份' }}・近3月年增 {{ pct(row.growth_quality?.yoy_3m, 2) }}</div>
          <div class="small">加速 {{ num(row.growth_quality?.acceleration_pp, 2) }} 百分點・R² {{ pct(row.growth_quality?.linearity_pct, 1) }}</div>
          <el-tooltip :content="growthText(row)" placement="top" trigger="click"><el-button link size="small" @click.stop>檢查原因與低基期提醒</el-button></el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="交易計畫" width="170">
        <template #default="{ row }">
          <template v-if="isLinPending(row)">
            <div class="muted">提前觀察，尚未確認買點</div>
            <div class="small muted">確認後再產生進場與停損計畫；目前不配置部位</div>
          </template>
          <!-- 已確認的買點即使資金不足也顯示參考價；原因在「建議部位」。 -->
          <template v-else-if="row.position_plan?.entry != null">
            <div>{{ isBuyStop(row.position_plan) ? '觸發價' : '參考進場' }} {{ num(row.position_plan.entry, 2) }}</div>
            <div v-if="row.position_plan.max_entry" class="small">
              {{ isBuyStop(row.position_plan) ? '開盤上限' : '追價上限' }} {{ num(row.position_plan.max_entry, 2) }}
            </div>
            <div class="small muted">{{ row.position_plan.entry_rule }}</div>
            <div>停損 <b class="down">{{ num(row.position_plan.stop, 2) }}</b></div>
            <template v-if="row.position_plan.target != null">
              <div>目標 <b class="up">{{ num(row.position_plan.target, 2) }}</b></div>
              <div>R/R {{ num(row.position_plan.rr, 2) }}</div>
            </template>
            <div v-else class="small muted">不設目標・{{ row.position_plan.exit_rule }}</div>
          </template>
          <span v-else class="down">{{ row.position_plan?.reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="進場條件" min-width="300">
        <template #default="{ row }">
          <div v-for="c in row.entry_conditions || []" :key="c.strategy" class="cond">
            <div class="cond-head">
              <el-tag size="small" :type="strategyType(c.strategy)" effect="plain">{{ c.label }}</el-tag>
              <span v-if="c.deadline" class="small muted">{{ c.deadline }}</span>
            </div>
            <div v-for="line in c.lines" :key="line" class="small cond-line">• {{ line }}</div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="取捨理由" min-width="220">
        <template #default="{ row }">
          <span :class="row.selected ? 'up' : 'muted'">{{ row.selection_reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="建議部位" width="180">
        <template #default="{ row }">
          <template v-if="row.position_plan?.valid">
            <div><b>{{ money(row.position_plan.suggested_shares) }}</b> 股（{{ row.position_plan.order_mode }}）</div>
            <div>約 {{ money(row.position_plan.position_value) }} 元・{{ pct(row.position_plan.capital_pct) }}</div>
            <div class="small muted">停損風險 {{ money(row.position_plan.risk_amount) }} 元・{{ pct(row.position_plan.actual_risk_pct, 2) }}</div>
          </template>
          <span v-else-if="row.position_plan?.entry != null" class="down">{{ row.position_plan.reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="歷史依據" min-width="240">
        <template #default="{ row }">
          <div>{{ historicalText(row) }}</div>
          <div v-if="row.calibration" class="muted small">
            95%區間 {{ pct(row.calibration.target_hit_ci_low) }}～{{ pct(row.calibration.target_hit_ci_high) }}・信賴度 {{ confidenceLabel[row.calibration.confidence] }}
          </div>
          <div v-else-if="combinationFilter || growthConfig || linConfig" class="muted small">單一策略的校準與績效不適用此實驗</div>
          <div v-else class="muted small">型態先驗與分數校準分開呈現，避免誤把兩者當成同一件事</div>
        </template>
      </el-table-column>
    </el-table>

    <el-collapse v-if="combinationFilter?.excluded_count" class="combination-exclusions">
      <el-collapse-item name="combination-exclusions">
        <template #title><b>未通過加強條件（{{ combinationFilter.excluded_count }} 檔）</b></template>
        <div class="small muted">列出主策略候選未通過組合的原因，最多顯示 100 檔；這些股票不納入本次入選與部位配置。</div>
        <el-table :data="combinationExcluded" border size="small">
          <el-table-column label="股票" width="170"><template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template></el-table-column>
          <el-table-column label="RS" width="70"><template #default="{ row }">{{ num(row.rs_rating, 0) }}</template></el-table-column>
          <el-table-column label="條件檢查與原因" min-width="400">
            <template #default="{ row }">
              <div v-for="c in row.combination_checks" :key="`${c.role}-${c.code}`" class="combination-check">
                <el-tag :type="c.passed ? 'success' : 'warning'" size="small" effect="plain">{{ c.passed ? '✓' : '未通過' }} {{ c.label }}</el-tag>
                <span class="small muted"> {{ c.reason }}</span>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <el-collapse class="calibration-panel">
      <el-collapse-item name="calibration">
        <template #title><b>分數校準明細與目前持股限制</b></template>
        <el-alert type="info" :closable="false" class="notice">
          盤後訊號採隔日開盤（支撐轉強、W 底測右底是隔日盤中漲到觸發價才買），扣 0.6% 來回成本與每邊 0.1% 滑價。跳空停損依開盤價，一價鎖停則跳過買進或延後賣出。
          20 日模式到期收盤；趨勢持有跌破 50 日線後隔日出場，最多 60 日。相近日期訊號互相關聯，樣本數與區間不能直接當成飆股機率。
        </el-alert>
        <el-table :data="result.calibration || []" size="small" border
                  :empty-text="combinationFilter ? '此組合尚無專屬校準；不使用單一策略的歷史樣本' : '尚無已到期的分數校準樣本；系統會從今天開始累積'">
          <el-table-column label="策略" width="100"><template #default="{ row }">{{ calibrationName(row.strategy) }}</template></el-table-column>
          <el-table-column prop="score_bucket" label="分數區間" width="100" />
          <el-table-column prop="n" label="樣本" width="80" />
          <el-table-column label="先到目標" width="110"><template #default="{ row }">{{ pct(row.target_hit_rate) }}</template></el-table-column>
          <el-table-column label="95% 區間" width="150"><template #default="{ row }">{{ pct(row.target_hit_ci_low) }}～{{ pct(row.target_hit_ci_high) }}</template></el-table-column>
          <el-table-column label="正期望比例" width="120"><template #default="{ row }">{{ pct(row.positive_rate) }}</template></el-table-column>
          <el-table-column label="平均 R" width="100"><template #default="{ row }">{{ num(row.avg_r, 2) }}R</template></el-table-column>
          <el-table-column label="信賴度" width="90"><template #default="{ row }">{{ confidenceLabel[row.confidence] }}</template></el-table-column>
          <el-table-column label="觀察期間"><template #default="{ row }">{{ row.first_date }}～{{ row.last_date }}</template></el-table-column>
        </el-table>
        <div class="holdings-line">
          <b>交易帳未平倉：</b>
          <span v-if="!result.holdings?.items?.length" class="muted">目前沒有持股，產業上限從零開始。</span>
          <el-tag v-for="(n, industry) in (result.holdings?.industry_counts || {})" :key="industry" size="small" effect="plain">
            {{ industry }} {{ n }} 檔
          </el-tag>
        </div>
      </el-collapse-item>
    </el-collapse>

    <div class="method">{{ result.method }}｜模型 {{ result.settings?.model_version || '—' }}</div>
      </el-tab-pane>
      <el-tab-pane label="決策追蹤／歷史紀錄" name="history" lazy>
        <el-alert v-if="combinationFilter || growthConfig" type="info" :closable="false" class="notice">以下是單一主策略的歷史，不包含目前的組合篩選或營收排序；實驗查詢不覆寫這些紀錄。</el-alert>
        <DecisionHistoryPanel :mode="form.mode" :gate="form.gate" />
      </el-tab-pane>
      <el-tab-pane label="策略研究" name="research" lazy>
        <el-alert v-if="combinationFilter || growthConfig || linConfig" type="info" :closable="false" class="notice">以下是另行產生的歷史研究，需確認是否包含目前的規則版本；強哥圖卡研究版與額外族群、籌碼交集尚未完成驗證。</el-alert>
        <DecisionResearchPanel />
      </el-tab-pane>
      <el-tab-pane label="營收品質研究" name="growth-research" lazy>
        <GrowthResearchPanel v-if="activeTab === 'growth-research'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.decision-center { max-width: 1900px; margin: 0 auto; }
.decision-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.decision-tabs :deep(.el-tabs__item) { font-size: 16px; font-weight: 700; }
.toolbar { margin-bottom: 10px; }
.title-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 24px; }
.muted { color: #909399; }
.small { font-size: 12px; }
.filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.filters.secondary { border-top: 1px solid #ebeef5; padding-top: 12px; }
.theme-filter { border-top: 1px solid #ebeef5; margin-top: 14px; padding-top: 12px; }
.combination-controls { border-top: 1px solid #ebeef5; margin-top: 14px; padding-top: 12px; }
.combination-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.combination-checkboxes { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 6px; }
.combination-checkboxes :deep(.el-checkbox) { margin-right: 0; }
.combination-scope { border: 1px solid #d9ecff; background: #f5faff; padding: 12px; border-radius: 4px; margin-bottom: 10px; line-height: 1.8; }
.combination-check { margin: 4px 0; }
.combination-exclusions { margin-top: 12px; }
.theme-controls, .theme-shortcuts, .scope-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.theme-controls label { color: #606266; font-size: 13px; font-weight: 600; }
.theme-select { width: 560px; max-width: 100%; }
.theme-shortcuts { margin: 10px 0 8px; }
.theme-shortcuts :deep(.el-button + .el-button) { margin-left: 0; }
.scope-line { margin-bottom: 10px; }
.filters label { color: #606266; font-size: 13px; font-weight: 600; }
.unit { color: #909399; font-size: 12px; margin-left: -5px; }
.summary-grid { display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 10px; margin-bottom: 10px; }
.summary-grid.six { grid-template-columns: repeat(6, minmax(130px, 1fr)); }
.summary.momentum { border-left: 4px solid #f56c6c; }
.title-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.gate-label { color: #606266; font-size: 13px; font-weight: 600; margin-left: 6px; }
.gate-tags { display: flex; gap: 4px; flex-wrap: wrap; }
.stock-themes { display: flex; gap: 4px; flex-wrap: wrap; margin: 3px 0; }
.stock-themes :deep(.el-tag) { max-width: 100%; min-width: 0; }
.stock-themes :deep(.el-tag__content) { overflow: hidden; text-overflow: ellipsis; }
.backtest-note { margin-top: 4px; color: #8a6d3b; }
.summary { cursor: pointer; }
.summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 12px 16px; }
.summary b { font-size: 24px; }
.summary b.money { font-size: 18px; }
.summary.selected { border-left: 4px solid #67c23a; }
.summary.ready { border-left: 4px solid #409eff; }
.summary.consensus { border-left: 4px solid #e6a23c; }
.summary.meta { border-left: 4px solid #606266; cursor: default; }
.notice { margin-bottom: 10px; }
.list-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 10px 0; flex-wrap: wrap; scroll-margin-top: 80px; }
.list-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.strategy-line { display: grid; grid-template-columns: minmax(72px, max-content) 38px 1fr; align-items: center; gap: 5px; margin: 3px 0; }
.consensus-text { color: #e6a23c; font-size: 12px; margin-top: 4px; }
.cond + .cond { margin-top: 8px; }
.cond-head { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.cond-line { color: #606266; line-height: 1.5; }
.big-score { font-size: 21px; font-weight: 750; }
.held { color: #e6a23c; font-size: 11px; margin-top: 4px; }
.up { color: #EA4C4C; }
.down { color: #3F9E5A; }
.calibration-panel { margin-top: 12px; }
.holdings-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.method { color: #909399; font-size: 12px; margin-top: 8px; text-align: right; }
@media (max-width: 1100px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
