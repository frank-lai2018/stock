<script setup>
// 財報成長排行榜：EPS 年增/季增、三率變化排名，可只看「盈餘加速」
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as XLSX from 'xlsx'
import { getGrowthRank, getIndustries } from '../api'
import WatchlistAddButton from '../components/WatchlistAddButton.vue'
import WatchlistBatchAdd from '../components/WatchlistBatchAdd.vue'

const router = useRouter()
const sortKey = ref('eps_yoy')
const accel = ref('')
const secType = ref('stock')
const industry = ref('')
const industries = ref([])
const minAmt = ref(20000000)
const items = ref([])
const sorts = ref({})
const asOf = ref('')
const loading = ref(false)
const tableRef = ref()
const selected = ref([])
function onSel(rows) { selected.value = rows }
function clearSel() { tableRef.value?.clearSelection(); selected.value = [] }

const UP = '#EA4C4C'
const DOWN = '#3F9E5A'
const pctv = (v) => (v == null ? '—' : Number(v).toFixed(2) + '%')
const sign = (v) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(1) + '%')
const clr = (v) => (v == null ? '' : Number(v) >= 0 ? UP : DOWN)
// 本益比位階顏色：低（便宜）綠、高（貴）紅
const perClr = (v) => (v == null ? '' : Number(v) >= 80 ? UP : Number(v) <= 20 ? DOWN : '#909399')

async function run() {
  loading.value = true
  try {
    const res = await getGrowthRank({
      sort: sortKey.value, accel: accel.value, security_type: secType.value,
      industry: industry.value, min_amt: minAmt.value, limit: 150,
    })
    items.value = res.items
    sorts.value = res.sorts
    asOf.value = res.as_of
  } catch (e) {
    ElMessage.error('查詢失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  try { industries.value = await getIndustries() } catch (e) { /* 下拉沒清單不影響查詢 */ }
  run()
})

function go(row, column) {
  if (column && column.type === 'selection') return
  router.push(`/stock/${row.stock_id}`)
}

function downloadXlsx() {
  if (!items.value.length) return ElMessage.warning('目前沒有結果可下載')
  const cols = [
    ['代碼', (r) => r.stock_id], ['名稱', (r) => r.name], ['產業', (r) => r.industry],
    ['股價', (r) => r.close], ['RS', (r) => r.rs_rating],
    ['EPS', (r) => r.eps], ['近四季EPS', (r) => r.eps_ttm],
    ['EPS年增%', (r) => r.eps_yoy], ['EPS季增%', (r) => r.eps_qoq],
    ['毛利率%', (r) => r.gross_margin], ['毛利率季增', (r) => r.gross_margin_chg],
    ['營益率%', (r) => r.op_margin], ['營益率季增', (r) => r.op_margin_chg],
    ['ROE', (r) => r.roe], ['月營收年增%', (r) => r.rev_yoy],
    ['PER', (r) => r.per], ['PER位階%', (r) => r.per_pctile],
    ['盈餘加速', (r) => (r.eps_accel ? 'Y' : '')], ['年增加速', (r) => (r.eps_yoy_accel ? 'Y' : '')],
  ]
  const aoa = [cols.map((c) => c[0])]
  for (const r of items.value) aoa.push(cols.map((c) => { const v = c[1](r); return v == null ? '' : v }))
  const ws = XLSX.utils.aoa_to_sheet(aoa)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '財報成長')
  XLSX.writeFile(wb, `growth_${(asOf.value || '').replace(/-/g, '') || 'result'}.xlsx`)
}
</script>

<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
        <b>財報成長排行榜</b>
        <span style="color: #666; font-size: 13px">排序</span>
        <el-select v-model="sortKey" style="width: 150px" @change="run">
          <el-option v-for="(name, k) in sorts" :key="k" :label="name" :value="k" />
          <el-option v-if="!Object.keys(sorts).length" label="EPS 年增率" value="eps_yoy" />
        </el-select>
        <span style="color: #666; font-size: 13px">盈餘加速</span>
        <el-select v-model="accel" style="width: 170px" @change="run">
          <el-option label="不過濾" value="" />
          <el-option label="連兩季 EPS 季增" value="qoq" />
          <el-option label="年增率逐季擴大" value="yoy" />
          <el-option label="兩者皆是（最嚴）" value="both" />
        </el-select>
        <el-select v-model="secType" style="width: 120px" @change="run">
          <el-option label="只個股" value="stock" />
          <el-option label="含 ETF" value="" />
        </el-select>
        <el-select v-model="industry" style="width: 170px" filterable clearable
                   placeholder="全部產業" @change="run">
          <el-option v-for="n in industries" :key="n" :label="n" :value="n" />
        </el-select>
        <span style="color: #666; font-size: 13px">日均額≥</span>
        <el-select v-model="minAmt" style="width: 120px" @change="run">
          <el-option label="500 萬" :value="5000000" />
          <el-option label="2000 萬" :value="20000000" />
          <el-option label="1 億" :value="100000000" />
        </el-select>
        <el-button type="primary" @click="run">查詢</el-button>
        <el-button size="small" type="success" :disabled="!items.length" @click="downloadXlsx">⬇ 下載 Excel</el-button>
        <el-tag v-if="asOf">資料日 {{ asOf }}</el-tag>
        <el-tag type="danger" effect="dark">{{ items.length }} 檔</el-tag>
        <span style="color: #999; font-size: 12px">
          成長率分母取絕對值（由虧轉盈也算得出來），基期極小時倍數會很誇張，請搭配 EPS 絕對值一起看
        </span>
      </div>
    </el-card>

    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
      <template v-if="selected.length">
        <span>已勾選 <b style="color: #EA4C4C">{{ selected.length }}</b> 檔</span>
        <WatchlistBatchAdd :rows="selected" @done="clearSel" />
        <el-button size="small" text @click="clearSel">清除勾選</el-button>
      </template>
    </div>

    <el-table ref="tableRef" :data="items" v-loading="loading" stripe style="cursor: pointer"
              @row-click="go" @selection-change="onSel">
      <el-table-column type="selection" width="42" fixed />
      <el-table-column prop="stock_id" label="代碼" width="80" fixed />
      <el-table-column prop="name" label="名稱" width="110" fixed />
      <el-table-column prop="industry" label="產業" width="120" show-overflow-tooltip />
      <el-table-column label="股價" width="80" align="right"><template #default="{ row }">{{ row.close }}</template></el-table-column>
      <el-table-column prop="rs_rating" label="RS" width="64" align="right" sortable />
      <el-table-column label="EPS" width="76" align="right"><template #default="{ row }">{{ row.eps ?? '—' }}</template></el-table-column>
      <el-table-column label="近四季EPS" width="100" align="right"><template #default="{ row }">{{ row.eps_ttm ?? '—' }}</template></el-table-column>
      <el-table-column label="EPS年增" width="100" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.eps_yoy) }">{{ sign(row.eps_yoy) }}</span></template>
      </el-table-column>
      <el-table-column label="EPS季增" width="100" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.eps_qoq) }">{{ sign(row.eps_qoq) }}</span></template>
      </el-table-column>
      <el-table-column label="加速" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.eps_accel" size="small" type="danger" effect="dark">季增</el-tag>
          <el-tag v-if="row.eps_yoy_accel" size="small" type="danger" effect="plain" style="margin-left: 3px">年增</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="毛利率" width="90" align="right"><template #default="{ row }">{{ pctv(row.gross_margin) }}</template></el-table-column>
      <el-table-column label="毛利季增" width="96" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.gross_margin_chg) }">{{ sign(row.gross_margin_chg) }}</span></template>
      </el-table-column>
      <el-table-column label="營益率" width="90" align="right"><template #default="{ row }">{{ pctv(row.op_margin) }}</template></el-table-column>
      <el-table-column label="營益季增" width="96" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.op_margin_chg) }">{{ sign(row.op_margin_chg) }}</span></template>
      </el-table-column>
      <el-table-column label="ROE" width="84" align="right"><template #default="{ row }">{{ pctv(row.roe) }}</template></el-table-column>
      <el-table-column label="營收年增" width="96" align="right">
        <template #default="{ row }"><span :style="{ color: clr(row.rev_yoy) }">{{ sign(row.rev_yoy) }}</span></template>
      </el-table-column>
      <el-table-column label="PER" width="76" align="right"><template #default="{ row }">{{ row.per ?? '—' }}</template></el-table-column>
      <el-table-column label="PER位階" width="92" align="right">
        <template #default="{ row }"><span :style="{ color: perClr(row.per_pctile) }">{{ row.per_pctile == null ? '—' : row.per_pctile + '%' }}</span></template>
      </el-table-column>
      <el-table-column label="自選" width="66" fixed="right">
        <template #default="{ row }"><WatchlistAddButton :row="row" /></template>
      </el-table-column>
    </el-table>
  </div>
</template>
