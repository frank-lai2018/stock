<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import * as XLSX from 'xlsx'
import { getBreakoutPatterns, screenBreakout } from '../api'
import PatternResultTable from './PatternResultTable.vue'
import WatchlistAddButton from './WatchlistAddButton.vue'

const props = defineProps({
  group: { type: String, required: true },        // 初始群組 bottom / continuation / top
  title: { type: String, default: '型態突破' },
  showDir: { type: Boolean, default: false },     // 連續/頭部型有多空方向
  mode: { type: String, default: 'breakout' },    // breakout=已突破 / near=接近突破
  groupSelectable: { type: Boolean, default: false }, // 顯示群組下拉（接近突破頁用）
})

const GROUPS = [
  { key: 'bottom', name: '底部反轉' },
  { key: 'continuation', name: '整理突破' },
  { key: 'top', name: '頭部反轉' },
]
const isNear = props.mode === 'near'

const curGroup = ref(props.group)
const cat = ref([])
const pattern = ref('all')
const secType = ref('')
const band = ref(0.05)                             // 接近突破容許帶（距頸線幾 % 內）
const recentSel = ref(3)                           // 突破觀察窗（幾個交易日內的突破才收錄）
const showTarget = ref(true)
const rsMin = ref(0)                                // RS 過濾（只顯示 RS ≥ 此值；0=不過濾）
const items = ref([])
const count = ref(0)
const asOf = ref('')
const loading = ref(false)

// 前端即時過濾：RS ≥ rsMin（RS 從缺者於過濾啟用時排除）
const shownItems = computed(() => {
  const m = Number(rsMin.value) || 0
  return m ? items.value.filter((r) => (r.rs_rating ?? -1) >= m) : items.value
})

async function loadCatalog() {
  try {
    cat.value = await getBreakoutPatterns(curGroup.value)
  } catch (e) {
    ElMessage.error('無法連到後端 /api')
  }
}
onMounted(async () => { await loadCatalog(); run() })

async function changeGroup() {
  pattern.value = 'all'
  await loadCatalog()
  run()
}

async function run() {
  loading.value = true
  try {
    const params = {
      pattern: pattern.value, group: curGroup.value,
      security_type: secType.value, mode: props.mode,
    }
    if (isNear) params.near_band = band.value
    else params.recent = recentSel.value
    const res = await screenBreakout(params)
    items.value = res.items
    count.value = res.count
    asOf.value = res.as_of
  } catch (e) {
    ElMessage.error('掃描失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function pts(row) {
  return (row.breakout?.points || []).map((p) => `${p.label} ${p.date.slice(5)} @${p.price}`).join('　')
}

// 欄位說明（顯示在頁面供參考；依模式切換）
const legend = [
  ['K棒型態', '最新一根 K 棒的陰陽線型態（紅＝偏多、綠＝偏空、灰＝中性）'],
  ['型態', '命中的波段型態；滑鼠移上顯示關鍵轉折點（底/肩/頸線等）'],
  ['量測滿足價', '型態學理目標價＝突破線 ± 型態高度（多方加、空方減）'],
  ['方向', '多 ↑＝偏多突破；空 ↓＝偏空跌破'],
  ...(isNear
    ? [['距突破', '目前收盤距頸線還差幾 %（越小越接近，尚未突破）'],
       ['最新', '最新資料日']]
    : [['突破日', '收盤突破/跌破頸線的那個交易日'],
       ['突破後', '突破當天收盤 → 目前收盤的漲跌%（即時）']]),
  ['頸線/突破線', '型態的關鍵壓力/支撐價（突破的那條線）'],
  ['突破收盤', '突破那天的收盤價（＝突破後報酬的計算基準）'],
  ['量比', '突破當天成交量 ÷ 前 50 日均量（越大代表突破越有量）'],
  ['RS評等', '相對強弱評等 0~99，≥70 屬強勢'],
  ['股價', '最新收盤價（即時）'],
  ['近3月', '近 3 個月漲跌%'],
]

// 匯出目前結果為 Excel（數值欄回傳數字型）
function downloadXlsx() {
  if (!shownItems.value.length) return ElMessage.warning('目前沒有結果可下載')
  const cols = [
    ['代碼', (r) => r.stock_id],
    ['名稱', (r) => r.name],
    ['類別', (r) => (r.security_type === 'etf' ? 'ETF' : '個股')],
    ['型態', (r) => r.pattern_name],
    ['方向', (r) => (r.breakout?.dir === 'bear' ? '空' : '多')],
    ...(isNear ? [['距突破%', (r) => (r.breakout?.near_pct == null ? '' : Number((r.breakout.near_pct * 100).toFixed(2)))]] : []),
    [isNear ? '最新日' : '突破日', (r) => r.breakout?.breakout_date],
    ...(isNear ? [] : [['突破後%', (r) => (r.breakout?.since_pct == null ? '' : Number((r.breakout.since_pct * 100).toFixed(2)))]]),
    ['頸線/突破線', (r) => r.breakout?.neckline],
    ['收盤', (r) => r.breakout?.breakout_close],
    ['量比', (r) => r.breakout?.vol_ratio],
    ['量測滿足價', (r) => r.breakout?.target],
    ['RS評等', (r) => r.rs_rating],
    ['股價', (r) => r.close],
    ['近3月%', (r) => (r.ret_3m == null ? '' : Number((r.ret_3m * 100).toFixed(2)))],
    ['產業', (r) => r.industry],
    ['關鍵點', (r) => pts(r)],
  ]
  const aoa = [cols.map((c) => c[0])]
  for (const r of shownItems.value) aoa.push(cols.map((c) => { const v = c[1](r); return v == null ? '' : v }))
  const ws = XLSX.utils.aoa_to_sheet(aoa)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, isNear ? '接近突破' : '型態突破')
  const stamp = (asOf.value || '').replace(/-/g, '') || 'result'
  XLSX.writeFile(wb, `${isNear ? 'near' : 'pattern'}_${curGroup.value}_${stamp}.xlsx`)
}
</script>

<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
        <b>{{ title }}</b>
        <el-select v-if="groupSelectable" v-model="curGroup" style="width: 130px" @change="changeGroup">
          <el-option v-for="g in GROUPS" :key="g.key" :label="g.name" :value="g.key" />
        </el-select>
        <el-select v-model="pattern" style="width: 150px" @change="run">
          <el-option label="全部型態" value="all" />
          <el-option v-for="p in cat" :key="p.key" :label="p.name" :value="p.key" />
        </el-select>
        <el-select v-model="secType" style="width: 120px" @change="run">
          <el-option label="含 ETF" value="" />
          <el-option label="只個股" value="stock" />
          <el-option label="只 ETF" value="etf" />
        </el-select>
        <template v-if="isNear">
          <span style="color: #666; font-size: 13px">距頸線</span>
          <el-select v-model="band" style="width: 96px" @change="run">
            <el-option label="3% 內" :value="0.03" />
            <el-option label="5% 內" :value="0.05" />
            <el-option label="8% 內" :value="0.08" />
          </el-select>
        </template>
        <template v-else>
          <span style="color: #666; font-size: 13px">突破時間</span>
          <el-select v-model="recentSel" style="width: 100px" @change="run">
            <el-option label="近 3 日" :value="3" />
            <el-option label="近 2 週" :value="10" />
            <el-option label="近 1 月" :value="20" />
          </el-select>
        </template>
        <el-button type="primary" @click="run">掃描</el-button>
        <el-button size="small" type="success" :disabled="!shownItems.length" @click="downloadXlsx">⬇ 下載 Excel</el-button>
        <el-checkbox v-model="showTarget" size="small" label="滿足價/方向" border />
        <span style="color: #666; font-size: 13px">RS &gt;</span>
        <el-input-number v-model="rsMin" :min="0" :max="99" :step="5" size="small" controls-position="right" style="width: 110px" />
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="danger" effect="dark">符合 {{ shownItems.length }} 檔<span v-if="rsMin"> / 共 {{ count }}</span></el-tag>
        <span style="color: #999; font-size: 12px">
          <template v-if="isNear">收盤已逼近頸線但<b>尚未</b>突破（距突破越小越接近）；帶量與否未過濾，請點列看圖確認</template>
          <template v-else>收盤突破/跌破頸線帶量。波段偵測有假訊號，請點列看圖確認</template>
        </span>
      </div>
    </el-card>

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

    <PatternResultTable :items="shownItems" :loading="loading" :show-target="showTarget"
                        :show-dir="showDir" :near="isNear" selectable>
      <template #action="{ row }"><WatchlistAddButton :row="row" /></template>
    </PatternResultTable>
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
