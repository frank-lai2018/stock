<script setup>
// 自選股：自建分類（el-tabs 可增刪），每個分類的表格與「型態突破」同構。
// 來源：其他頁★加入，或此頁手動搜尋加入。價格/RS/近3月為即時，型態為加入當下快照。
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as XLSX from 'xlsx'
import {
  getWatchCategories, addWatchCategory, deleteWatchCategory,
  getWatchItems, addWatchItem, deleteWatchItem, searchStocks,
} from '../api'
import PatternResultTable from '../components/PatternResultTable.vue'

const cats = ref([])
const active = ref(null)          // 目前分類 id（字串化，配合 el-tabs name）
const items = ref([])
const asOf = ref('')
const loading = ref(false)
const showTarget = ref(true)      // 顯示/隱藏「量測滿足價 / 方向」兩欄
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
    await loadItems()
    loadCats()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加入失敗')
  }
}
async function removeItem(row) {
  try {
    await deleteWatchItem(row.watchlist_id)
    items.value = items.value.filter((r) => r.watchlist_id !== row.watchlist_id)
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
      <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0">
        <el-select v-model="picked" filterable remote :remote-method="remoteSearch" :loading="searching"
                   placeholder="搜尋代碼/名稱手動加入" style="width: 260px" clearable>
          <el-option v-for="s in options" :key="s.stock_id" :label="`${s.stock_id} ${s.name}`" :value="s.stock_id" />
        </el-select>
        <el-button type="primary" :disabled="!picked" @click="addPicked">加入本分類</el-button>
        <el-button type="success" :disabled="!shownItems.length" @click="downloadXlsx">⬇ 下載 Excel</el-button>
        <el-checkbox v-model="showTarget" size="small" label="滿足價/方向" border />
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
                          :show-hold="showHold" :show-track="true">
        <template #action="{ row }">
          <el-button size="small" text bg circle title="移出自選" @click.stop="removeItem(row)">✕</el-button>
        </template>
      </PatternResultTable>
    </div>
  </div>
</template>

<style scoped>
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
