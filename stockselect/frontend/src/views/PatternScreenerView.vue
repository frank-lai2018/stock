<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getPatterns, screenPattern } from '../api'
import WatchlistAddButton from '../components/WatchlistAddButton.vue'
import WatchlistBatchAdd from '../components/WatchlistBatchAdd.vue'

const router = useRouter()
const cat = ref([])
const pattern = ref('')
const items = ref([])
const name = ref('')
const loading = ref(false)
const rsMin = ref(0)                                // RS 過濾（只顯示 RS ≥ 此值；0=不過濾）
const epsQ = ref(4)                                 // EPS 過濾看幾季（1/2/4）
const epsMin = ref('')                              // 近 epsQ 季「每季」EPS 門檻（空=不過濾）
const tableRef = ref()
const selected = ref([])
function onSel(rows) { selected.value = rows }
function clearSel() { tableRef.value?.clearSelection(); selected.value = [] }

// 前端即時過濾：RS ≥ rsMin，且近 epsQ 季「每季」EPS ≥ epsMin
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

const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }
const dirText = { bull: '偏多', bear: '偏空', neutral: '中性' }
const pct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
const num = (v) => (v == null ? '' : Number(v).toLocaleString('en-US'))
// 近 4 季 EPS（新到舊），缺料季顯示 -
const eps = (row) => (row.eps_recent || []).map((v) => (v == null ? '-' : Number(v).toFixed(2))).join(' / ')

onMounted(async () => {
  try {
    cat.value = await getPatterns()
    if (cat.value.length) { pattern.value = cat.value[0].key; run() }
  } catch (e) {
    ElMessage.error('無法連到後端 /api')
  }
})

async function run() {
  if (!pattern.value) return
  loading.value = true
  try {
    const res = await screenPattern(pattern.value, 150)
    items.value = res.items
    name.value = res.name
  } catch (e) {
    ElMessage.error('查詢失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
function go(row, column) {
  if (column && column.type === 'selection') return   // 點勾選格不跳頁
  router.push(`/stock/${row.stock_id}`)
}
</script>

<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
        <b>K 棒型態選股</b>
        <el-select v-model="pattern" style="width: 200px" @change="run">
          <el-option v-for="p in cat" :key="p.key" :label="p.name" :value="p.key">
            <span :style="{ color: dirColor[p.dir] }">{{ p.name }}（{{ dirText[p.dir] }}）</span>
          </el-option>
        </el-select>
        <el-button type="primary" @click="run">篩選</el-button>
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
        <span style="color: #999; font-size: 12px">最新交易日出現該型態、且在母體內；依流動性排序</span>
      </div>
    </el-card>

    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
      <el-tag>「{{ name }}」符合 {{ shownItems.length }} 檔<span v-if="filtering"> / 共 {{ items.length }}</span></el-tag>
      <template v-if="selected.length">
        <span>已勾選 <b style="color: #EA4C4C">{{ selected.length }}</b> 檔</span>
        <WatchlistBatchAdd :rows="selected" @done="clearSel" />
        <el-button size="small" text @click="clearSel">清除勾選</el-button>
      </template>
    </div>
    <el-table ref="tableRef" :data="shownItems" v-loading="loading" stripe style="cursor: pointer"
              @row-click="go" @selection-change="onSel">
      <el-table-column type="selection" width="42" fixed />
      <el-table-column prop="stock_id" label="代碼" width="80" />
      <el-table-column prop="name" label="名稱" width="120" />
      <el-table-column prop="industry" label="產業" width="130" show-overflow-tooltip />
      <el-table-column label="收盤" width="90"><template #default="{ row }">{{ row.close }}</template></el-table-column>
      <el-table-column label="近1月" width="90"><template #default="{ row }">
        <span :style="{ color: row.ret_1m >= 0 ? '#EA4C4C' : '#3F9E5A' }">{{ pct(row.ret_1m) }}</span>
      </template></el-table-column>
      <el-table-column label="近3月" width="90"><template #default="{ row }">
        <span :style="{ color: row.ret_3m >= 0 ? '#EA4C4C' : '#3F9E5A' }">{{ pct(row.ret_3m) }}</span>
      </template></el-table-column>
      <el-table-column prop="rs_rating" label="RS" width="70" sortable />
      <el-table-column label="近4季EPS" width="180" show-overflow-tooltip><template #default="{ row }">
        <span style="color: #666">{{ eps(row) }}</span>
      </template></el-table-column>
      <el-table-column prop="per" label="PER" width="80" />
      <el-table-column label="法人20日(股)" width="130"><template #default="{ row }">
        <span :style="{ color: row.inst_net_20d >= 0 ? '#EA4C4C' : '#3F9E5A' }">{{ num(row.inst_net_20d) }}</span>
      </template></el-table-column>
      <el-table-column label="千張大戶%" width="100"><template #default="{ row }">{{ row.big1000_pct }}</template></el-table-column>
      <el-table-column label="自選" width="66" fixed="right">
        <template #default="{ row }"><WatchlistAddButton :row="row" /></template>
      </el-table-column>
    </el-table>
  </div>
</template>
