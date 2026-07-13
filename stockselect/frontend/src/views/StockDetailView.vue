<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getStock, getFundamentals } from '../api'
import PriceChart from '../components/PriceChart.vue'
import MarginPanel from '../components/MarginPanel.vue'

const cols = ref(4)
const updateCols = () => {
  cols.value = window.innerWidth < 700 ? 2 : window.innerWidth < 1100 ? 3 : 4
}

const route = useRoute()
const router = useRouter()
const s = ref(null)
const fund = ref(null)

const pct = (v) => (v == null ? '—' : (Number(v) * 100).toFixed(1) + '%')
const p1 = (v) => (v == null ? '—' : Number(v).toFixed(2) + '%')
const num = (v) => (v == null ? '—' : Number(v).toLocaleString('en-US'))
const clr = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')

onMounted(async () => {
  updateCols()
  window.addEventListener('resize', updateCols)
  try {
    s.value = await getStock(route.params.id)
    fund.value = await getFundamentals(route.params.id)
  } catch (e) {
    ElMessage.error('載入失敗：' + (e?.response?.data?.detail || e.message))
  }
})

onBeforeUnmount(() => window.removeEventListener('resize', updateCols))
</script>

<template>
  <el-page-header @back="router.back()" :content="s ? `${s.stock_id} ${s.name}（${s.industry || ''}）` : '載入中…'" />

  <template v-if="s">
    <el-descriptions :column="cols" border style="margin-top: 16px" title="快照">
      <el-descriptions-item label="收盤">{{ s.close }}</el-descriptions-item>
      <el-descriptions-item label="近3月">{{ pct(s.ret_3m) }}</el-descriptions-item>
      <el-descriptions-item label="12-1動能">{{ pct(s.ret_12_1) }}</el-descriptions-item>
      <el-descriptions-item label="站季線">{{ s.above_ma60 ? '是' : '否' }}</el-descriptions-item>
      <el-descriptions-item label="ROE">{{ s.roe ?? '—' }}</el-descriptions-item>
      <el-descriptions-item label="EPS">{{ s.eps ?? '—' }}</el-descriptions-item>
      <el-descriptions-item label="本益比">{{ s.per ?? '—' }}</el-descriptions-item>
      <el-descriptions-item label="殖利率">{{ p1(s.dividend_yield) }}</el-descriptions-item>
      <el-descriptions-item label="營收YoY">{{ p1(s.rev_yoy) }}</el-descriptions-item>
      <el-descriptions-item label="法人20日(股)">{{ num(s.inst_net_20d) }}</el-descriptions-item>
      <el-descriptions-item label="外資持股%">{{ s.foreign_ratio ?? '—' }}</el-descriptions-item>
      <el-descriptions-item label="千張大戶%">{{ s.big1000_pct ?? '—' }}</el-descriptions-item>
    </el-descriptions>

    <el-card shadow="never" style="margin-top: 16px" header="技術線圖">
      <PriceChart :stock-id="String(route.params.id)" />
    </el-card>

    <el-card shadow="never" style="margin-top: 16px" header="融資融券">
      <MarginPanel :stock-id="String(route.params.id)" />
    </el-card>

    <div v-if="fund" style="display: flex; gap: 16px; margin-top: 16px; flex-wrap: wrap">
      <el-card style="flex: 1 1 360px; min-width: 300px" shadow="never" header="近12月營收">
        <el-table :data="fund.monthly_revenue" height="320" size="small">
          <el-table-column label="月份" width="100">
            <template #default="{ row }">{{ String(row.revenue_month).slice(0, 7) }}</template>
          </el-table-column>
          <el-table-column label="營收(元)"><template #default="{ row }">{{ num(row.revenue) }}</template></el-table-column>
          <el-table-column label="MoM" width="80">
            <template #default="{ row }"><span :style="{ color: clr(row.mom_pct) }">{{ p1(row.mom_pct) }}</span></template>
          </el-table-column>
          <el-table-column label="YoY" width="80">
            <template #default="{ row }"><span :style="{ color: clr(row.yoy_pct) }">{{ p1(row.yoy_pct) }}</span></template>
          </el-table-column>
        </el-table>
      </el-card>
      <el-card style="flex: 1 1 360px; min-width: 300px" shadow="never" header="近季財報">
        <el-table :data="fund.quarterly" height="320" size="small">
          <el-table-column label="期別" width="110">
            <template #default="{ row }">{{ String(row.period_date).slice(0, 10) }}</template>
          </el-table-column>
          <el-table-column label="EPS" prop="eps" width="70" />
          <el-table-column label="ROE" prop="roe" width="70" />
          <el-table-column label="毛利%" prop="gross_margin" />
        </el-table>
      </el-card>
    </div>
  </template>
</template>
