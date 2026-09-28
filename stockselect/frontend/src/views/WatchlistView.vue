<script setup>
// 自選股：自建分類（el-tabs 可增刪），每個分類的表格與「型態突破」同構。
// 來源：其他頁★加入，或此頁手動搜尋加入。價格/RS/近3月為即時，型態為加入當下快照。
// 分類頁籤下有「檢視」切換：清單（原本的表格，完全不動）／裸K決策／突破決策（規則同兩個決策頁的「指定個股分析」）。
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as XLSX from 'xlsx'
import {
  getWatchCategories, addWatchCategory, deleteWatchCategory,
  getWatchItems, addWatchItem, deleteWatchItem, searchStocks,
  getWatchPriceAction, getWatchBreakout,
} from '../api'
import PatternResultTable from '../components/PatternResultTable.vue'
import PriceActionTable, { CONCLUSIONS } from '../components/PriceActionTable.vue'
import BreakoutDecisionTable, { STATUS } from '../components/BreakoutDecisionTable.vue'
import PriceActionGuide from '../components/PriceActionGuide.vue'
import BreakoutDecisionGuide from '../components/BreakoutDecisionGuide.vue'

const cats = ref([])
const active = ref(null)          // 目前分類 id（字串化，配合 el-tabs name）
const items = ref([])
const asOf = ref('')
const loading = ref(false)
const showTarget = ref(true)      // 顯示/隱藏「量測滿足價 / 方向」兩欄
const showTargetTrack = ref(true)  // 顯示/隱藏「達標 / 進度 / 距滿足價」三欄
const showHold = ref(true)        // 顯示/隱藏「進場價 / 持有報酬」兩欄
const rsMin = ref(0)              // RS 過濾（只顯示 RS ≥ 此值；0=不過濾）
const epsQ = ref(4)               // EPS 過濾看幾季（1/2/4）
const epsMin = ref('')            // 近 epsQ 季「每季」EPS 門檻（空=不過濾）

// 前端即時過濾：RS ≥ rsMin，且近 epsQ 季「每季」EPS ≥ epsMin（與型態各頁行為一致）
const shownItems = computed(() => {
  const rs = Number(rsMin.value) || 0
  const em = (epsMin.value === '' || epsMin.value == null) ? null : Number(epsMin.value)
  const nq = Number(epsQ.value) || 4
  let arr = items.value
  if (rs) arr = arr.filter((r) => (r.rs_rating ?? -1) >= rs)
  if (em != null && !Number.isNaN(em)) {
    arr = arr.filter((r) => {
      const e = r.eps_recent || []
      if (e.length < nq) return false               // 近 nq 季資料不足 → 排除
      return e.slice(0, nq).every((v) => v != null && Number(v) >= em)
    })
  }
  return arr
})
const filtering = computed(() => !!Number(rsMin.value) || (epsMin.value !== '' && epsMin.value != null))
// 手動加入
const options = ref([])
const picked = ref(null)
const searching = ref(false)

// ---- 決策檢視：切過去才算，結果依「分類＋參數」快取；清單檢視不受影響 ----
const view = ref('list')                   // list＝原本的清單；pa＝裸K決策；bk＝突破決策
const paLookback = ref(5)
const paExpiry = ref(5)
const paDir = ref('')                      // '' | bull | bear
const paPick = ref('')                     // 點統計標籤：priority / waiting / watch / skip / none
const paBearOnly = ref(false)              // 只看「空方訊號提醒」那幾檔
const pa = ref({ key: '', pending: '', items: [], asOf: '', loading: false })
const bkRecent = ref(20)                   // 自選股很少剛好在近 3 日突破，預設近 1 月（決策頁是近 3 日）
const bkPick = ref('')                     // priority / watch / skip / none
const bk = ref({ key: '', pending: '', items: [], asOf: '', loading: false })

const PA_TAGS = { ...CONCLUSIONS, none: { label: '無訊號', type: 'info' } }
const BK_TAGS = { ...STATUS, none: { label: '無突破', type: 'info' } }
const paKey = () => `${active.value}|${paLookback.value}|${paExpiry.value}`
const bkKey = () => `${active.value}|${bkRecent.value}`

// 同一組參數已載入或正在載入就不重打；回應回來時參數已變（換分類／改參數）就丟掉
async function loadDecision(state, key, fetcher, label) {
  if (!active.value || state.value.key === key || state.value.pending === key) return
  state.value = { ...state.value, pending: key, loading: true }
  try {
    const res = await fetcher()
    if (state.value.pending !== key) return
    state.value = { key, pending: '', items: res.items || [], asOf: res.as_of || '', loading: false }
  } catch (e) {
    if (state.value.pending !== key) return
    state.value = { key: '', pending: '', items: [], asOf: '', loading: false }
    ElMessage.error(`${label}載入失敗：` + (e?.response?.data?.detail || e.message))
  }
}
function loadPa(force = false) {
  if (force) pa.value = { ...pa.value, key: '', pending: '' }
  return loadDecision(pa, paKey(), () => getWatchPriceAction(active.value,
    { lookback: paLookback.value, expiry: paExpiry.value }), '裸 K 決策')
}
function loadBk(force = false) {
  if (force) bk.value = { ...bk.value, key: '', pending: '' }
  return loadDecision(bk, bkKey(), () => getWatchBreakout(active.value, { recent: bkRecent.value }), '突破決策')
}
function loadView() {
  if (view.value === 'pa') loadPa()
  else if (view.value === 'bk') loadBk()
}
watch([view, active], loadView)            // 切檢視、換分類（含新增／刪除分類）
watch([paLookback, paExpiry], () => loadPa())
watch(bkRecent, () => loadBk())

// 空方訊號提醒：只算決策不是「略過」的（持有者的減碼／停利提醒）
const isBearAlert = (r) => r.decision?.direction === 'bear' && r.decision.conclusion !== 'skip'
const paRows = computed(() => pa.value.items.filter((r) => {
  const d = r.decision
  if (paBearOnly.value && !isBearAlert(r)) return false
  if (paPick.value === 'none') return !d
  if (paPick.value && d?.conclusion !== paPick.value) return false
  if (paDir.value && d?.direction !== paDir.value) return false
  return true
}))
const paCount = computed(() => {
  const c = Object.fromEntries(Object.keys(PA_TAGS).map((k) => [k, 0]))
  for (const r of pa.value.items) c[r.decision ? r.decision.conclusion : 'none'] += 1
  return c
})
const paBear = computed(() => {
  const rows = pa.value.items.filter(isBearAlert)
  return { n: rows.length, waiting: rows.filter((r) => r.decision.status === 'waiting').length }
})
const bkRows = computed(() => bk.value.items.filter((r) => (
  !bkPick.value || (bkPick.value === 'none' ? !r.decision : r.decision?.status === bkPick.value))))
const bkCount = computed(() => {
  const c = Object.fromEntries(Object.keys(BK_TAGS).map((k) => [k, 0]))
  for (const r of bk.value.items) c[r.decision ? r.decision.status : 'none'] += 1
  return c
})

function dropFromViews(wid) {               // 清單與兩個決策檢視一起拿掉，不必重算
  items.value = items.value.filter((r) => r.watchlist_id !== wid)
  pa.value = { ...pa.value, items: pa.value.items.filter((r) => r.watchlist_id !== wid) }
  bk.value = { ...bk.value, items: bk.value.items.filter((r) => r.watchlist_id !== wid) }
}
async function removeDecisionRow(row) {
  try {
    await deleteWatchItem(row.watchlist_id)
    dropFromViews(row.watchlist_id)
    loadCats()
  } catch (e) {
    ElMessage.error('移除失敗')
  }
}

async function loadCats() {
  cats.value = await getWatchCategories()
  if (cats.value.length) {
    if (!cats.value.find((c) => String(c.id) === String(active.value)))
      active.value = String(cats.value[0].id)
  } else {
    active.value = null
  }
}
async function loadItems() {
  if (!active.value) { items.value = []; asOf.value = ''; return }
  loading.value = true
  try {
    const res = await getWatchItems(active.value)
    items.value = res.items
    asOf.value = res.as_of
  } catch (e) {
    ElMessage.error('讀取失敗')
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  try { await loadCats() } catch (e) { ElMessage.error('無法連到後端 /api') }
  loadItems()
})

function onTab() { loadItems() }

async function addCat() {
  try {
    const { value } = await ElMessageBox.prompt('分類名稱', '新增分類',
      { inputPattern: /\S/, inputErrorMessage: '不可空白', inputValidator: (v) => (v || '').length <= 60 || '上限 60 字' })
    const c = await addWatchCategory(value.trim())
    await loadCats()
    active.value = String(c.id)
    loadItems()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || '新增失敗')
  }
}
async function removeCat(id) {
  const c = cats.value.find((x) => String(x.id) === String(id))
  try {
    await ElMessageBox.confirm(`刪除分類「${c?.name}」及其所有股票？`, '確認', { type: 'warning' })
    await deleteWatchCategory(id)
    await loadCats()
    loadItems()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('刪除失敗')
  }
}
function tabsEdit(targetName, action) {         // el-tabs editable 回呼
  if (action === 'add') addCat()
  else if (action === 'remove') removeCat(targetName)
}

async function remoteSearch(q) {
  q = (q || '').trim()
  if (!q) { options.value = []; return }
  searching.value = true
  try { options.value = await searchStocks(q) } catch (e) { /* 忽略 */ } finally { searching.value = false }
}
async function addPicked() {
  if (!picked.value || !active.value) return
  try {
    await addWatchItem({ category_id: Number(active.value), stock_id: picked.value })
    ElMessage.success('已加入')
    picked.value = null
    options.value = []
    pa.value = { ...pa.value, key: '' }
    bk.value = { ...bk.value, key: '' }
    await loadItems()
    loadCats()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加入失敗')
  }
}
async function removeItem(row) {
  try {
    await deleteWatchItem(row.watchlist_id)
    dropFromViews(row.watchlist_id)
    loadCats()
  } catch (e) {
    ElMessage.error('移除失敗')
  }
}

function pts(row) {
  return (row.breakout?.points || []).map((p) => `${p.label} ${p.date.slice(5)} @${p.price}`).join('　')
}

// 欄位說明（顯示在頁面供參考）
const legend = [
  ['K棒型態', '最新一根 K 棒的陰陽線型態（紅＝偏多、綠＝偏空、灰＝中性）'],
  ['型態', '加入當下命中的波段型態；滑鼠移上顯示關鍵轉折點（底/頸線等）'],
  ['量測滿足價', '型態學理目標價＝突破線 ± 型態高度（多方加、空方減）'],
  ['方向', '多 ↑＝偏多突破；空 ↓＝偏空跌破'],
  ['突破日', '收盤突破/跌破頸線的那個交易日'],
  ['突破後', '突破當天收盤 → 目前收盤的漲跌%（即時、原始漲跌，未分方向）'],
  ['型態同期均', '該型態回測在「突破後同樣交易日數」的期望報酬（下方小字＝突破後已幾個交易日；↑＝已過 20 日觀察期）'],
  ['相對型態', '實際(順型態方向)報酬 − 型態同期期望；▲優＝跑贏型態歷史常態、▼弱＝落後。空方型態「跌」算順勢為正'],
  ['頸線/突破線', '型態的關鍵壓力/支撐價（突破的那條線）'],
  ['突破收盤', '突破那天的收盤價（＝突破後報酬的計算基準）'],
  ['量比', '突破當天成交量 ÷ 前 50 日均量（越大代表突破越有量）'],
  ['RS評等', '相對強弱評等 0~99，≥70 屬強勢'],
  ['加速', '季增＝連兩季 EPS 一季比一季高；年增＝EPS 年增率逐季擴大（Minervini 盈餘加速）'],
  ['PER位階', '目前本益比在該股近 3 年的百分位；低＝相對自己歷史便宜、高＝偏貴'],
  ['千張大戶%', '集保持股 1000 張以上者的持股佔比；「連N週↑」＝連續 N 週增加'],
  ['股價', '最新收盤價（即時）'],
  ['進場價 / 進場日', '加入本自選股當下的收盤與資料日（已在清單者不覆蓋）'],
  ['持有報酬', '進場價 → 目前收盤的漲跌%（即時），追蹤你加入後的績效'],
  ['近3月', '近 3 個月漲跌%'],
]
function downloadXlsx() {
  if (!shownItems.value.length) return ElMessage.warning('目前沒有結果可下載')
  const cols = [
    ['代碼', (r) => r.stock_id],
    ['名稱', (r) => r.name],
    ['類別', (r) => (r.security_type === 'etf' ? 'ETF' : '個股')],
    ['型態', (r) => r.pattern_name],
    ['方向', (r) => (r.breakout?.dir === 'bear' ? '空' : (r.breakout ? '多' : ''))],
    ['突破日', (r) => r.breakout?.breakout_date],
    ['頸線/突破線', (r) => r.breakout?.neckline],
    ['突破收盤', (r) => r.breakout?.breakout_close],
    ['量比', (r) => r.breakout?.vol_ratio],
    ['量測滿足價', (r) => r.breakout?.target],
    ['RS評等', (r) => r.rs_rating],
    ['股價', (r) => r.close],
    ['進場價', (r) => r.entry_price],
    ['進場日', (r) => r.entry_date],
    ['持有報酬%', (r) => (r.hold_pct == null ? '' : Number((r.hold_pct * 100).toFixed(2)))],
    ['突破後交易日', (r) => r.track?.days],
    ['實際(順勢)%', (r) => (r.track?.actual == null ? '' : Number((r.track.actual * 100).toFixed(2)))],
    ['型態同期均%', (r) => (r.track?.exp_ret == null ? '' : Number((r.track.exp_ret * 100).toFixed(2)))],
    ['相對型態%', (r) => (r.track?.rel == null ? '' : Number((r.track.rel * 100).toFixed(2)))],
    ['滿足價', (r) => r.target_track?.target ?? ''],
    ['是否達標', (r) => (r.target_track ? (r.target_track.hit ? '是' : '否') : '')],
    ['達標天數', (r) => r.target_track?.days_to_hit ?? ''],
    ['達標日', (r) => r.target_track?.hit_date ?? ''],
    ['進度%', (r) => (r.target_track ? Number((r.target_track.progress * 100).toFixed(1)) : '')],
    ['距滿足價%', (r) => (r.target_track?.gap_pct == null ? '' : Number((r.target_track.gap_pct * 100).toFixed(2)))],
    ['近3月%', (r) => (r.ret_3m == null ? '' : Number((r.ret_3m * 100).toFixed(2)))],
    ['產業', (r) => r.industry],
    ['加入時間', (r) => (r.added_at ? r.added_at.slice(0, 10) : '')],
    ['關鍵點', (r) => pts(r)],
  ]
  const aoa = [cols.map((c) => c[0])]
  for (const r of shownItems.value) aoa.push(cols.map((c) => { const v = c[1](r); return v == null ? '' : v }))
  const ws = XLSX.utils.aoa_to_sheet(aoa)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '自選股')
  const cname = (cats.value.find((c) => String(c.id) === String(active.value)) || {}).name || 'watchlist'
  XLSX.writeFile(wb, `watchlist_${cname}.xlsx`)
}
</script>

<template>
  <div>
    <el-tabs v-if="cats.length" v-model="active" type="card" editable
             @edit="tabsEdit" @tab-change="onTab">
      <el-tab-pane v-for="c in cats" :key="c.id" :label="`${c.name} (${c.n})`" :name="String(c.id)" />
    </el-tabs>
    <el-empty v-else description="尚無分類，先建立第一個分類">
      <el-button type="primary" @click="addCat">＋ 新增分類</el-button>
    </el-empty>

    <div v-if="cats.length">
      <div class="view-bar">
        <el-radio-group v-model="view">
          <el-radio-button value="list">清單</el-radio-button>
          <el-radio-button value="pa">裸K決策</el-radio-button>
          <el-radio-button value="bk">突破決策</el-radio-button>
        </el-radio-group>
        <span v-if="view !== 'list'" class="view-hint">規則同決策頁的「指定個股分析」：只看本分類，不套母體、流動性、證券類別和基本面門檻</span>
      </div>

      <div v-show="view === 'list'">
      <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0">
        <el-select v-model="picked" filterable remote :remote-method="remoteSearch" :loading="searching"
                   placeholder="搜尋代碼/名稱手動加入" style="width: 260px" clearable>
          <el-option v-for="s in options" :key="s.stock_id" :label="`${s.stock_id} ${s.name}`" :value="s.stock_id" />
        </el-select>
        <el-button type="primary" :disabled="!picked" @click="addPicked">加入本分類</el-button>
        <el-button type="success" :disabled="!shownItems.length" @click="downloadXlsx">⬇ 下載 Excel</el-button>
        <el-checkbox v-model="showTarget" size="small" label="滿足價/方向" border />
        <el-checkbox v-model="showTargetTrack" size="small" label="達標/進度" border />
        <el-checkbox v-model="showHold" size="small" label="進場價/持有報酬" border />
        <span style="color: #666; font-size: 13px">RS &gt;</span>
        <el-input-number v-model="rsMin" :min="0" :max="99" :step="5" size="small" controls-position="right" style="width: 110px" />
        <span style="color: #666; font-size: 13px">近</span>
        <el-select v-model="epsQ" size="small" style="width: 80px">
          <el-option label="1 季" :value="1" />
          <el-option label="2 季" :value="2" />
          <el-option label="4 季" :value="4" />
        </el-select>
        <span style="color: #666; font-size: 13px">每季EPS &gt;</span>
        <el-input v-model="epsMin" type="number" size="small" placeholder="不限" style="width: 96px" clearable />
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="danger" effect="dark">{{ shownItems.length }} 檔<span v-if="filtering"> / 共 {{ items.length }}</span></el-tag>
        <span style="color: #999; font-size: 12px">即時：價格 / RS / 近3月 / 突破後 / 持有報酬；型態為加入當下快照。點列看 K 線</span>
      </div>

      <el-collapse style="margin-bottom: 8px">
        <el-collapse-item name="legend">
          <template #title>
            <span style="font-weight: 600">📖 欄位說明</span>
            <span style="color: #999; font-size: 12px; margin-left: 8px">（紅漲綠跌；點開參考各欄意義）</span>
          </template>
          <div class="legend-grid">
            <div v-for="[k, v] in legend" :key="k" class="legend-row">
              <span class="legend-k">{{ k }}</span>
              <span class="legend-v">{{ v }}</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>

      <PatternResultTable :items="shownItems" :loading="loading" :show-target="showTarget" :show-dir="true"
                          :show-hold="showHold" :show-track="true" :show-target-track="showTargetTrack">
        <template #action="{ row }">
          <el-button size="small" text bg circle title="移出自選" @click.stop="removeItem(row)">✕</el-button>
        </template>
      </PatternResultTable>
      </div>

      <div v-if="view === 'pa'">
        <div class="dec-bar">
          <el-select v-model="paLookback" size="small" style="width: 125px">
            <el-option label="近 3 根訊號" :value="3" />
            <el-option label="近 5 根訊號" :value="5" />
            <el-option label="近 10 根訊號" :value="10" />
          </el-select>
          <el-select v-model="paExpiry" size="small" style="width: 125px">
            <el-option label="3 根內觸發" :value="3" />
            <el-option label="5 根內觸發" :value="5" />
            <el-option label="10 根內觸發" :value="10" />
          </el-select>
          <el-select v-model="paDir" size="small" clearable placeholder="多空皆看" style="width: 110px">
            <el-option label="多方" value="bull" />
            <el-option label="空方" value="bear" />
          </el-select>
          <el-button size="small" :loading="pa.loading" @click="loadPa(true)">重新計算</el-button>
          <el-tag v-for="(t, k) in PA_TAGS" :key="k" :type="t.type" :effect="paPick === k ? 'dark' : 'plain'"
                  class="pick" @click="paPick = paPick === k ? '' : k">{{ t.label }} {{ paCount[k] }}</el-tag>
          <el-tag v-if="pa.asOf">資料日 {{ pa.asOf }}</el-tag>
          <el-tag type="danger" effect="dark">{{ paRows.length }} 檔<span v-if="paRows.length !== pa.items.length"> / 共 {{ pa.items.length }}</span></el-tag>
        </div>
        <el-alert v-if="paBear.n" type="warning" :closable="false" show-icon class="bear-alert">
          <template #title>
            {{ paBear.n }} 檔出現空方訊號（已觸發 {{ paBear.n - paBear.waiting }}、等待跌破 {{ paBear.waiting }}）
            <el-button size="small" :type="paBearOnly ? 'warning' : ''" class="bear-btn" @click="paBearOnly = !paBearOnly">
              {{ paBearOnly ? '顯示全部' : '只看這些' }}
            </el-button>
          </template>
          只算決策不是「略過」的；持有的話可以當減碼、停利的提醒，還沒買的先別進場。
        </el-alert>
        <PriceActionTable :items="paRows" :loading="pa.loading" empty-text="沒有符合條件的股票"
                          no-chart-text="近 10 日沒有" action-label="操作">
          <template #action="{ row }">
            <el-button size="small" text bg circle title="移出自選" @click.stop="removeDecisionRow(row)">✕</el-button>
          </template>
        </PriceActionTable>
        <PriceActionGuide watchlist />
      </div>

      <div v-if="view === 'bk'">
        <div class="dec-bar">
          <el-select v-model="bkRecent" size="small" style="width: 112px">
            <el-option label="近 3 日" :value="3" />
            <el-option label="近 2 週" :value="10" />
            <el-option label="近 1 月" :value="20" />
          </el-select>
          <el-button size="small" :loading="bk.loading" @click="loadBk(true)">重新計算</el-button>
          <el-tag v-for="(t, k) in BK_TAGS" :key="k" :type="t.type" :effect="bkPick === k ? 'dark' : 'plain'"
                  class="pick" @click="bkPick = bkPick === k ? '' : k">{{ t.label }} {{ bkCount[k] }}</el-tag>
          <el-tag v-if="bk.asOf">資料日 {{ bk.asOf }}</el-tag>
          <el-tag type="danger" effect="dark">{{ bkRows.length }} 檔<span v-if="bkRows.length !== bk.items.length"> / 共 {{ bk.items.length }}</span></el-tag>
        </div>
        <BreakoutDecisionTable :items="bkRows" :loading="bk.loading" empty-text="沒有符合條件的股票" action-label="操作">
          <template #action="{ row }">
            <el-button size="small" text bg circle title="移出自選" @click.stop="removeDecisionRow(row)">✕</el-button>
          </template>
        </BreakoutDecisionTable>
        <BreakoutDecisionGuide watchlist />
      </div>
    </div>
  </div>
</template>

<style scoped>
.view-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 8px 0 4px; }
.view-hint { color: #909399; font-size: 12px; }
.dec-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.bear-alert { margin-bottom: 8px; }
.bear-btn { margin-left: 8px; }
.pick { cursor: pointer; }
.legend-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(560px, 1fr));
  gap: 8px 24px;
}
.legend-row {
  display: flex;
  gap: 8px;
  font-size: 24px;
  line-height: 1.5;
}
.legend-k {
  flex: 0 0 184px;
  font-weight: 600;
  color: #303133;
}
.legend-v {
  flex: 1;
  color: #666;
}
</style>
