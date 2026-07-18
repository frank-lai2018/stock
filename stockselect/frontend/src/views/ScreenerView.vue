<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getStrategies, runScreen } from '../api'
import FilterPanel from '../components/FilterPanel.vue'
import ResultTable from '../components/ResultTable.vue'

const filters = reactive({ in_universe: true })
const sort = ref('ret_3m')
const limit = ref(50)
const strategies = ref({})
const items = ref([])
const count = ref(0)
const asOf = ref('')
const loading = ref(false)
const appliedTag = ref('')   // 目前套用的策略 tag（手動篩選則清空）

// 策略 tag 顯示名對應（其他未列者顯示策略原名）
const TAG_LABELS = { minervini: 'VCP口袋名單' }

onMounted(async () => {
  try {
    strategies.value = await getStrategies()
  } catch (e) {
    ElMessage.error('無法連到後端 /api（確認 uvicorn 有跑在 :8000）')
  }
})

function cleanFilters() {
  const out = {}
  for (const k in filters) {
    const v = filters[k]
    if (v !== null && v !== undefined && v !== '') out[k] = v
  }
  return out
}

async function search(fromStrategy = false) {
  if (!fromStrategy) appliedTag.value = ''   // 手動篩選 → 非具名策略，清掉 tag
  loading.value = true
  try {
    const res = await runScreen({ filters: cleanFilters(), sort: sort.value, desc: true, limit: limit.value })
    items.value = res.items
    count.value = res.count
    asOf.value = res.as_of
  } catch (e) {
    ElMessage.error('查詢失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function applyStrategy(key) {
  const s = strategies.value[key]
  if (!s) return
  for (const k in filters) delete filters[k]
  Object.assign(filters, s.filters)
  sort.value = s.sort || 'ret_3m'
  if (s.limit) limit.value = s.limit   // 策略可自帶顯示筆數（趨勢範本=100 供比對）
  appliedTag.value = TAG_LABELS[key] || s.name   // 標題列顯示套用的 tag
  search(true)
}
</script>

<template>
  <div style="display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap">
    <div style="flex: 1 1 320px; min-width: 300px; max-width: 400px">
      <FilterPanel :filters="filters" :strategies="strategies"
                   v-model:sort="sort" v-model:limit="limit"
                   @search="search" @apply="applyStrategy" />
    </div>
    <div style="flex: 3 1 480px; min-width: 320px">
      <div style="margin-bottom: 8px">
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="success" style="margin-left: 8px">符合 {{ count }} 檔</el-tag>
        <el-tag v-if="appliedTag" type="danger" effect="dark" size="large"
                style="margin-left: 8px">{{ appliedTag }}</el-tag>
        <span style="margin-left: 12px; color: #999">點任一列看個股 K 線</span>
      </div>
      <ResultTable :items="items" :loading="loading" />
    </div>
  </div>
</template>
