<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import * as XLSX from 'xlsx'
import { getBreakoutPatterns, screenBreakout } from '../api'
import PatternResultTable from './PatternResultTable.vue'
import WatchlistAddButton from './WatchlistAddButton.vue'

const props = defineProps({
  group: { type: String, required: true },     // bottom / continuation / top
  title: { type: String, default: '型態突破' },
  showDir: { type: Boolean, default: false },  // 連續/頭部型有多空方向
})

const cat = ref([])
const pattern = ref('all')
const secType = ref('')
const limit = ref(100)
const showTarget = ref(true)   // 顯示「量測滿足價 / 方向」兩欄
const items = ref([])
const count = ref(0)
const asOf = ref('')
const loading = ref(false)

onMounted(async () => {
  try {
    cat.value = await getBreakoutPatterns(props.group)
  } catch (e) {
    ElMessage.error('無法連到後端 /api')
  }
  run()
})

async function run() {
  loading.value = true
  try {
    const res = await screenBreakout({
      pattern: pattern.value, group: props.group,
      limit: limit.value, security_type: secType.value,
    })
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

// 匯出目前結果為 Excel（數值欄回傳數字型，讓 Excel 當數字）
function downloadXlsx() {
  if (!items.value.length) return ElMessage.warning('目前沒有結果可下載')
  const cols = [
    ['代碼', (r) => r.stock_id],
    ['名稱', (r) => r.name],
    ['類別', (r) => (r.security_type === 'etf' ? 'ETF' : '個股')],
    ['型態', (r) => r.pattern_name],
    ['方向', (r) => (r.breakout?.dir === 'bear' ? '空' : '多')],
    ['突破日', (r) => r.breakout?.breakout_date],
    ['頸線/突破線', (r) => r.breakout?.neckline],
    ['突破收盤', (r) => r.breakout?.breakout_close],
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
  XLSX.utils.book_append_sheet(wb, ws, '型態突破')
  const stamp = (asOf.value || '').replace(/-/g, '') || 'result'
  XLSX.writeFile(wb, `pattern_${props.group}_${stamp}.xlsx`)
}
</script>

<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
        <b>{{ title }}</b>
        <el-select v-model="pattern" style="width: 170px" @change="run">
          <el-option label="全部型態" value="all" />
          <el-option v-for="p in cat" :key="p.key" :label="p.name" :value="p.key" />
        </el-select>
        <el-select v-model="secType" style="width: 130px" @change="run">
          <el-option label="含 ETF" value="" />
          <el-option label="只個股" value="stock" />
          <el-option label="只 ETF" value="etf" />
        </el-select>
        <el-button type="primary" @click="run">掃描</el-button>
        <el-button size="small" type="success" :disabled="!items.length" @click="downloadXlsx">⬇ 下載 Excel</el-button>
        <el-checkbox v-model="showTarget" size="small" label="滿足價/方向" border />
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="danger" effect="dark">符合 {{ count }} 檔</el-tag>
        <span style="color: #999; font-size: 12px">
          全市場掃描（母體：流動性≥2千萬）；收盤突破/跌破頸線帶量。波段偵測有假訊號，請點列看圖確認
        </span>
      </div>
    </el-card>

    <PatternResultTable :items="items" :loading="loading" :show-target="showTarget"
                        :show-dir="showDir" selectable>
      <template #action="{ row }"><WatchlistAddButton :row="row" /></template>
    </PatternResultTable>
  </div>
</template>
