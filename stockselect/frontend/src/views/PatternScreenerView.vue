<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getPatterns, screenPattern } from '../api'

const router = useRouter()
const cat = ref([])
const pattern = ref('')
const items = ref([])
const name = ref('')
const loading = ref(false)

const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }
const dirText = { bull: '偏多', bear: '偏空', neutral: '中性' }
const pct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
const num = (v) => (v == null ? '' : Number(v).toLocaleString('en-US'))

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
function go(row) { router.push(`/stock/${row.stock_id}`) }
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
        <span style="color: #999; font-size: 12px">最新交易日出現該型態、且在母體內；依流動性排序</span>
      </div>
    </el-card>

    <el-tag style="margin-bottom: 8px">「{{ name }}」符合 {{ items.length }} 檔</el-tag>
    <el-table :data="items" v-loading="loading" height="72vh" stripe style="cursor: pointer" @row-click="go">
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
      <el-table-column prop="per" label="PER" width="80" />
      <el-table-column label="法人20日(股)" width="130"><template #default="{ row }">
        <span :style="{ color: row.inst_net_20d >= 0 ? '#EA4C4C' : '#3F9E5A' }">{{ num(row.inst_net_20d) }}</span>
      </template></el-table-column>
      <el-table-column label="千張大戶%" width="100"><template #default="{ row }">{{ row.big1000_pct }}</template></el-table-column>
    </el-table>
  </div>
</template>
