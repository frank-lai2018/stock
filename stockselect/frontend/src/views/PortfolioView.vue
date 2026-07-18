<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPortfolio, saveHolding, deleteHolding, searchStocks } from '../api'

const router = useRouter()
const data = ref({ items: [], summary: null, as_of: null })
const loading = ref(false)
const form = ref({ stock_id: '', label: '', lots: null, cost: null, note: '' })

// 評級 → 台股慣例上色（strong=紅偏多、reduce=綠偏空）
const LEVEL = {
  strong: { label: '續抱', color: '#EA4C4C' },
  watch: { label: '觀察', color: '#E6A23C' },
  reduce: { label: '減碼', color: '#3F9E5A' },
}
const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }

const money = (v) => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'))
const yi = (v) => (v == null ? '—' : (Number(v) / 1e8).toFixed(2) + ' 億')
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(2) + '%')
const up = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')

async function load() {
  loading.value = true
  try {
    data.value = await getPortfolio()
  } catch (e) {
    ElMessage.error('載入持股失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function querySuggest(qs, cb) {
  const s = (qs || '').trim()
  if (!s) return cb([])
  try {
    const rows = await searchStocks(s)
    cb(rows.map((r) => ({ ...r, value: `${r.stock_id} ${r.name}` })))
  } catch { cb([]) }
}
function onPick(item) {
  form.value.stock_id = item.stock_id
  form.value.label = `${item.stock_id} ${item.name}`
}

async function add() {
  if (!form.value.stock_id) return ElMessage.warning('請先搜尋並選擇股票')
  try {
    await saveHolding({
      stock_id: form.value.stock_id,
      lots: form.value.lots, cost: form.value.cost, note: form.value.note || null,
    })
    ElMessage.success('已加入 / 更新')
    form.value = { stock_id: '', label: '', lots: null, cost: null, note: '' }
    await load()
  } catch (e) {
    ElMessage.error('儲存失敗：' + (e?.response?.data?.detail || e.message))
  }
}

async function del(row) {
  try {
    await ElMessageBox.confirm(`確定移除 ${row.stock_id} ${row.name}？`, '移除持股', { type: 'warning' })
  } catch { return }
  await deleteHolding(row.stock_id)
  await load()
}

function go(row) { router.push(`/stock/${row.stock_id}`) }

onMounted(load)
</script>

<template>
  <div>
    <!-- 新增持股 -->
    <el-card shadow="never" header="加入持股">
      <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center">
        <el-autocomplete v-model="form.label" :fetch-suggestions="querySuggest" :debounce="250"
                         :trigger-on-focus="false" clearable placeholder="搜尋代碼 / 名稱"
                         style="width: 240px" @select="onPick">
          <template #default="{ item }">
            <b style="color: #ea4c4c">{{ item.stock_id }}</b>&nbsp;{{ item.name }}
          </template>
        </el-autocomplete>
        <el-input-number v-model="form.lots" :min="0" :step="1" :precision="3"
                         controls-position="right" placeholder="張數" style="width: 130px" />
        <span style="color: #999">張</span>
        <el-input-number v-model="form.cost" :min="0" :step="0.5" :precision="2"
                         controls-position="right" placeholder="成本" style="width: 130px" />
        <span style="color: #999">元/股</span>
        <el-input v-model="form.note" placeholder="備註（選填）" style="width: 180px" />
        <el-button type="primary" @click="add">加入 / 更新</el-button>
      </div>
      <div style="color: #999; font-size: 12px; margin-top: 6px">
        張數 / 成本可留空（僅做技術籌碼診斷、不算損益）；同一檔再加入會覆蓋。
      </div>
    </el-card>

    <!-- 組合總覽 -->
    <div v-if="data.summary" style="display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px">
      <el-card shadow="never" style="flex: 1 1 220px">
        <div style="color: #999">總市值</div>
        <div style="font-size: 24px; font-weight: 700">{{ money(data.summary.total_value) }}</div>
        <div style="color: #999; font-size: 12px; margin-top: 4px">
          總損益
          <b :style="{ color: up(data.summary.total_pnl) }">
            {{ money(data.summary.total_pnl) }}（{{ pct(data.summary.total_pnl_pct) }}）
          </b>
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 260px">
        <div style="color: #999">診斷分佈（{{ data.summary.n }} 檔）</div>
        <div style="font-size: 18px; margin-top: 6px">
          <span style="color: #EA4C4C">續抱 {{ data.summary.levels.strong }}</span>
          <span style="color: #E6A23C; margin: 0 10px">觀察 {{ data.summary.levels.watch }}</span>
          <span style="color: #3F9E5A">減碼 {{ data.summary.levels.reduce }}</span>
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 6px">
          主力承接 {{ data.summary.accum_n }}｜出貨 {{ data.summary.distrib_n }}
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 220px">
        <div style="color: #999">平均 RS 評等</div>
        <div style="font-size: 24px; font-weight: 700"
             :style="{ color: (data.summary.avg_rs || 0) >= 70 ? '#EA4C4C' : '#909399' }">
          {{ data.summary.avg_rs ?? '—' }}
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 4px">
          最大產業：{{ data.summary.top_industry || '—' }}
          <span v-if="data.summary.top_industry_share">（{{ data.summary.top_industry_share }}%）</span>
        </div>
      </el-card>
    </div>

    <!-- 逐檔診斷 -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        持股診斷
        <el-tag v-if="data.as_of" size="small" style="margin-left: 8px">資料日 {{ data.as_of }}</el-tag>
        <span style="margin-left: 12px; color: #999; font-size: 12px">點列看個股 K 線</span>
      </template>
      <el-table :data="data.items" v-loading="loading" stripe height="66vh"
                style="cursor: pointer" @row-click="go">
        <el-table-column prop="stock_id" label="代碼" width="72" fixed />
        <el-table-column prop="name" label="名稱" width="96" fixed />
        <el-table-column label="診斷" width="132">
          <template #default="{ row }">
            <el-tag :color="LEVEL[row.level]?.color" style="color: #fff; border: 0"
                    effect="dark" size="small">{{ LEVEL[row.level]?.label }}</el-tag>
            <b v-if="row.score != null" style="margin-left: 6px">{{ row.score }}</b>
            <span v-if="row.facets?.tech != null" style="color: #999; font-size: 11px">
              （技{{ row.facets.tech }}/籌{{ row.facets.chip }}/基{{ row.facets.fund }}）
            </span>
          </template>
        </el-table-column>
        <el-table-column label="關鍵訊號" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="(r, i) in row.reasons" :key="i" size="small"
                    :color="dirColor[r.dir]" style="color: #fff; border: 0; margin: 1px 2px">
              {{ r.text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="張數" width="80">
          <template #default="{ row }">{{ row.lots ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="成本" width="80">
          <template #default="{ row }">{{ row.cost ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="現價" width="80">
          <template #default="{ row }">{{ row.close ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="損益" width="150">
          <template #default="{ row }">
            <span :style="{ color: up(row.pnl_pct) }">
              {{ row.pnl_pct == null ? '—' : pct(row.pnl_pct) }}
            </span>
            <span v-if="row.pnl != null" style="color: #999; font-size: 12px"> {{ money(row.pnl) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="支撐/壓力" width="130">
          <template #default="{ row }">
            <span style="color: #8E44AD">{{ row.support ?? '—' }}</span>
            <span style="color: #999"> / </span>
            <span style="color: #FF7A00">{{ row.resistance ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="RS" width="64">
          <template #default="{ row }">
            <b :style="{ color: (row.rs_rating || 0) >= 70 ? '#EA4C4C' : '#909399' }">{{ row.rs_rating ?? '—' }}</b>
          </template>
        </el-table-column>
        <el-table-column label="K棒型態" width="120">
          <template #default="{ row }">
            <el-tag v-for="(p, i) in (row.last_patterns || [])" :key="i" size="small"
                    :color="dirColor[p.dir]" style="color: #fff; border: 0; margin: 1px 2px">{{ p.name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click.stop="del(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !data.items.length" description="尚無持股，請於上方加入" />
    </el-card>
  </div>
</template>
