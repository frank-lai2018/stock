<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getBreakoutPatterns, screenBreakout } from '../api'

const props = defineProps({
  group: { type: String, required: true },     // bottom / continuation
  title: { type: String, default: '型態突破' },
  showDir: { type: Boolean, default: false },  // 連續型有多空方向
})

const router = useRouter()
const cat = ref([])
const pattern = ref('all')
const secType = ref('')
const limit = ref(100)
const showTarget = ref(true)   // 顯示「量測滿足價 / 方向」兩欄
const items = ref([])
const count = ref(0)
const asOf = ref('')
const loading = ref(false)

const pct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
const upc = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
// 方向上色（紅漲綠跌）；底部型態一律偏多
const dirColor = (row) => (row.breakout?.dir === 'bear' ? '#3F9E5A' : '#EA4C4C')

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
function go(row) { router.push(`/stock/${row.stock_id}`) }
function pts(row) {
  return (row.breakout?.points || []).map((p) => `${p.label} ${p.date.slice(5)} @${p.price}`).join('　')
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
        <el-checkbox v-model="showTarget" size="small" label="滿足價/方向" border />
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="danger" effect="dark">符合 {{ count }} 檔</el-tag>
        <span style="color: #999; font-size: 12px">
          全市場掃描（母體：流動性≥2千萬）；收盤突破/跌破頸線帶量。波段偵測有假訊號，請點列看圖確認
        </span>
      </div>
    </el-card>

    <el-table :data="items" v-loading="loading" height="70vh" stripe
              style="cursor: pointer" @row-click="go">
      <el-table-column prop="stock_id" label="代碼" width="76" fixed />
      <el-table-column label="名稱" width="120" fixed>
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.security_type === 'etf'" size="small" type="warning" effect="plain">ETF</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="型態" width="120">
        <template #default="{ row }">
          <el-tooltip :content="pts(row)" placement="top" :disabled="!pts(row)">
            <el-tag :color="dirColor(row)" style="color: #fff; border: 0">{{ row.pattern_name }}</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column v-if="showTarget" label="量測滿足價" width="102">
        <template #default="{ row }"><b :style="{ color: dirColor(row) }">{{ row.breakout?.target }}</b></template>
      </el-table-column>
      <el-table-column v-if="showTarget && showDir" label="方向" width="72">
        <template #default="{ row }">
          <span :style="{ color: dirColor(row), fontWeight: 700 }">
            {{ row.breakout?.dir === 'bear' ? '空 ↓' : '多 ↑' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="突破日" width="104">
        <template #default="{ row }"><span :style="{ color: dirColor(row) }">{{ row.breakout?.breakout_date?.slice(5) }}</span></template>
      </el-table-column>
      <el-table-column :label="showDir ? '突破線' : '頸線/杯口'" width="96">
        <template #default="{ row }">{{ row.breakout?.neckline }}</template>
      </el-table-column>
      <el-table-column label="突破收盤" width="90">
        <template #default="{ row }">{{ row.breakout?.breakout_close }}</template>
      </el-table-column>
      <el-table-column label="量比" width="72">
        <template #default="{ row }">{{ row.breakout?.vol_ratio }}×</template>
      </el-table-column>
      <el-table-column label="RS評等" width="82" sortable :sort-method="(a, b) => (a.rs_rating ?? -1) - (b.rs_rating ?? -1)">
        <template #default="{ row }">
          <b :style="{ color: row.rs_rating >= 70 ? '#f56c6c' : '#909399' }">{{ row.rs_rating ?? '—' }}</b>
        </template>
      </el-table-column>
      <el-table-column label="股價" width="78">
        <template #default="{ row }">{{ row.close ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="近3月" width="86" sortable :sort-method="(a, b) => (a.ret_3m ?? -9) - (b.ret_3m ?? -9)">
        <template #default="{ row }"><span :style="{ color: upc(row.ret_3m) }">{{ pct(row.ret_3m) }}</span></template>
      </el-table-column>
      <el-table-column prop="industry" label="產業" min-width="120" show-overflow-tooltip />
    </el-table>
  </div>
</template>
