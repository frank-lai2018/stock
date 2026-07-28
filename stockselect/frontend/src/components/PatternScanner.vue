<script setup>
import { ref, onMounted } from 'vue'
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
const showTarget = ref(true)
const items = ref([])
const count = ref(0)
const asOf = ref('')
const loading = ref(false)

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

// 匯出目前結果為 Excel（數值欄回傳數字型）
function downloadXlsx() {
  if (!items.value.length) return ElMessage.warning('目前沒有結果可下載')
  const cols = [
    ['代碼', (r) => r.stock_id],
    ['名稱', (r) => r.name],
    ['類別', (r) => (r.security_type === 'etf' ? 'ETF' : '個股')],
    ['型態', (r) => r.pattern_name],
    ['方向', (r) => (r.breakout?.dir === 'bear' ? '空' : '多')],
    ...(isNear ? [['距突破%', (r) => (r.breakout?.near_pct == null ? '' : Number((r.breakout.near_pct * 100).toFixed(2)))]] : []),
    [isNear ? '最新日' : '突破日', (r) => r.breakout?.breakout_date],
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
  for (const r of items.value) aoa.push(cols.map((c) => { const v = c[1](r); return v == null ? '' : v }))
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
        <el-button type="primary" @click="run">掃描</el-button>
        <el-button size="small" type="success" :disabled="!items.length" @click="downloadXlsx">⬇ 下載 Excel</el-button>
        <el-checkbox v-model="showTarget" size="small" label="滿足價/方向" border />
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="danger" effect="dark">符合 {{ count }} 檔</el-tag>
        <span style="color: #999; font-size: 12px">
          <template v-if="isNear">收盤已逼近頸線但<b>尚未</b>突破（距突破越小越接近）；帶量與否未過濾，請點列看圖確認</template>
          <template v-else>收盤突破/跌破頸線帶量。波段偵測有假訊號，請點列看圖確認</template>
        </span>
      </div>
    </el-card>

    <PatternResultTable :items="items" :loading="loading" :show-target="showTarget"
                        :show-dir="showDir" :near="isNear" selectable>
      <template #action="{ row }"><WatchlistAddButton :row="row" /></template>
    </PatternResultTable>
  </div>
</template>
