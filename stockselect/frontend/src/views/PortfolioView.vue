<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPortfolio, getTrades, addTrade, deleteTrade, searchStocks } from '../api'

const router = useRouter()
const data = ref({ items: [], summary: null, realized: [], as_of: null })
const trades = ref([])
const loading = ref(false)
const tab = ref('holdings')
const form = ref({ stock_id: '', label: '', action: 'buy', trade_date: '', lots: null, price: null, fee: null, tax: null, note: '' })

// 評級 → 台股慣例上色（strong=紅偏多、reduce=綠偏空）
const LEVEL = {
  strong: { label: '續抱', color: '#EA4C4C' },
  watch: { label: '觀察', color: '#E6A23C' },
  reduce: { label: '減碼', color: '#3F9E5A' },
}
const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }

const money = (v) => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'))
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(2) + '%')
const up = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')

async function load() {
  loading.value = true
  try {
    data.value = await getPortfolio()
    trades.value = await getTrades()
  } catch (e) {
    ElMessage.error('載入失敗：' + (e?.response?.data?.detail || e.message))
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
  if (!form.value.trade_date) return ElMessage.warning('請選擇交易日期')
  if (!form.value.lots || !form.value.price) return ElMessage.warning('請填張數與價格')
  try {
    await addTrade({
      stock_id: form.value.stock_id, action: form.value.action, trade_date: form.value.trade_date,
      lots: form.value.lots, price: form.value.price,
      fee: form.value.fee, tax: form.value.tax, note: form.value.note || null,
    })
    ElMessage.success('已記錄')
    form.value = { stock_id: '', label: '', action: form.value.action, trade_date: form.value.trade_date,
                   lots: null, price: null, fee: null, tax: null, note: '' }
    await load()
  } catch (e) {
    ElMessage.error('儲存失敗：' + (e?.response?.data?.detail || e.message))
  }
}

async function delTrade(row) {
  try {
    await ElMessageBox.confirm(
      `刪除這筆交易？${row.stock_id} ${row.action === 'buy' ? '買' : '賣'} ${row.lots}張 @${row.price}`,
      '刪除交易', { type: 'warning' })
  } catch { return }
  await deleteTrade(row.id)
  await load()
}

function go(row) { router.push(`/stock/${row.stock_id}`) }

onMounted(load)
</script>

<template>
  <div>
    <!-- 組合總覽 -->
    <div v-if="data.summary" style="display: flex; gap: 12px; flex-wrap: wrap">
      <el-card shadow="never" style="flex: 1 1 200px">
        <div style="color: #999">總市值（未平倉）</div>
        <div style="font-size: 22px; font-weight: 700">{{ money(data.summary.total_value) }}</div>
        <div style="font-size: 12px; margin-top: 4px">
          未實現 <b :style="{ color: up(data.summary.unrealized) }">{{ money(data.summary.unrealized) }}（{{ pct(data.summary.unrealized_pct) }}）</b>
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 200px">
        <div style="color: #999">已實現損益</div>
        <div style="font-size: 22px; font-weight: 700" :style="{ color: up(data.summary.realized_total) }">
          {{ money(data.summary.realized_total) }}
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 4px">
          平倉 {{ data.summary.closed_n }} 筆｜勝率 {{ data.summary.win_rate ?? '—' }}%
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 200px">
        <div style="color: #999">總損益（已+未實現）</div>
        <div style="font-size: 22px; font-weight: 700" :style="{ color: up(data.summary.total_pnl) }">
          {{ money(data.summary.total_pnl) }}
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 240px">
        <div style="color: #999">診斷分佈（{{ data.summary.n }} 檔）</div>
        <div style="font-size: 16px; margin-top: 6px">
          <span style="color: #EA4C4C">續抱 {{ data.summary.levels.strong }}</span>
          <span style="color: #E6A23C; margin: 0 8px">觀察 {{ data.summary.levels.watch }}</span>
          <span style="color: #3F9E5A">減碼 {{ data.summary.levels.reduce }}</span>
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 6px">
          平均RS {{ data.summary.avg_rs ?? '—' }}｜承接 {{ data.summary.accum_n }}／出貨 {{ data.summary.distrib_n }}
        </div>
      </el-card>
    </div>

    <el-tabs v-model="tab" style="margin-top: 12px">
      <!-- 持股診斷 -->
      <el-tab-pane label="持股診斷" name="holdings">
        <el-tag v-if="data.as_of" size="small" style="margin-bottom: 8px">資料日 {{ data.as_of }}｜點列看個股 K 線</el-tag>
        <el-table :data="data.items" v-loading="loading" stripe height="60vh"
                  style="cursor: pointer" @row-click="go">
          <el-table-column prop="stock_id" label="代碼" width="72" fixed />
          <el-table-column prop="name" label="名稱" width="96" fixed />
          <el-table-column label="診斷" width="128">
            <template #default="{ row }">
              <el-tag :color="LEVEL[row.level]?.color" style="color: #fff; border: 0" effect="dark" size="small">
                {{ LEVEL[row.level]?.label }}</el-tag>
              <b v-if="row.score != null" style="margin-left: 6px">{{ row.score }}</b>
            </template>
          </el-table-column>
          <el-table-column label="關鍵訊號" min-width="200">
            <template #default="{ row }">
              <el-tag v-for="(r, i) in row.reasons" :key="i" size="small"
                      :color="dirColor[r.dir]" style="color: #fff; border: 0; margin: 1px 2px">{{ r.text }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="張數" width="76"><template #default="{ row }">{{ row.lots }}</template></el-table-column>
          <el-table-column label="均成本" width="84"><template #default="{ row }">{{ row.cost ?? '—' }}</template></el-table-column>
          <el-table-column label="現價" width="76"><template #default="{ row }">{{ row.close ?? '—' }}</template></el-table-column>
          <el-table-column label="未實現" width="150">
            <template #default="{ row }">
              <span :style="{ color: up(row.unrealized_pct) }">{{ pct(row.unrealized_pct) }}</span>
              <span v-if="row.unrealized != null" style="color: #999; font-size: 12px"> {{ money(row.unrealized) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="支撐/壓力" width="126">
            <template #default="{ row }">
              <span style="color: #8E44AD">{{ row.support ?? '—' }}</span>
              <span style="color: #999"> / </span>
              <span style="color: #FF7A00">{{ row.resistance ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="RS" width="60">
            <template #default="{ row }">
              <b :style="{ color: (row.rs_rating || 0) >= 70 ? '#EA4C4C' : '#909399' }">{{ row.rs_rating ?? '—' }}</b>
            </template>
          </el-table-column>
          <el-table-column label="K棒型態" width="116">
            <template #default="{ row }">
              <el-tag v-for="(p, i) in (row.last_patterns || [])" :key="i" size="small"
                      :color="dirColor[p.dir]" style="color: #fff; border: 0; margin: 1px 2px">{{ p.name }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !data.items.length" description="尚無未平倉持股，請於「交易紀錄」加入買進" />
      </el-tab-pane>

      <!-- 交易紀錄 -->
      <el-tab-pane label="交易紀錄" name="trades">
        <el-card shadow="never" style="margin-bottom: 12px">
          <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
            <el-radio-group v-model="form.action">
              <el-radio-button value="buy">買進</el-radio-button>
              <el-radio-button value="sell">賣出</el-radio-button>
            </el-radio-group>
            <el-autocomplete v-model="form.label" :fetch-suggestions="querySuggest" :debounce="250"
                             :trigger-on-focus="false" clearable placeholder="搜尋代碼 / 名稱"
                             style="width: 200px" @select="onPick">
              <template #default="{ item }"><b style="color: #ea4c4c">{{ item.stock_id }}</b>&nbsp;{{ item.name }}</template>
            </el-autocomplete>
            <el-date-picker v-model="form.trade_date" type="date" value-format="YYYY-MM-DD"
                            placeholder="交易日" style="width: 150px" />
            <el-input-number v-model="form.lots" :min="0" :step="1" :precision="3" controls-position="right"
                             placeholder="張數" style="width: 120px" /><span style="color: #999">張</span>
            <el-input-number v-model="form.price" :min="0" :step="0.5" :precision="2" controls-position="right"
                             placeholder="價格" style="width: 120px" /><span style="color: #999">元</span>
            <el-input-number v-model="form.fee" :min="0" :step="1" controls-position="right"
                             placeholder="手續費" style="width: 120px" />
            <el-input-number v-model="form.tax" :min="0" :step="1" controls-position="right"
                             placeholder="證交稅" style="width: 120px" />
            <el-input v-model="form.note" placeholder="備註" style="width: 140px" />
            <el-button type="primary" @click="add">記錄</el-button>
          </div>
          <div style="color: #999; font-size: 12px; margin-top: 6px">
            手續費/證交稅可留空（視為 0）；賣出以 FIFO 配對買進，自動算已實現損益。日後補歷史紀錄照樣填即可。
          </div>
        </el-card>
        <el-table :data="trades" v-loading="loading" stripe height="52vh">
          <el-table-column label="日期" width="120"><template #default="{ row }">{{ String(row.trade_date).slice(0, 10) }}</template></el-table-column>
          <el-table-column label="動作" width="70">
            <template #default="{ row }">
              <el-tag :type="row.action === 'buy' ? 'danger' : 'success'" size="small" effect="dark">
                {{ row.action === 'buy' ? '買' : '賣' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="股票" width="140">
            <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
          </el-table-column>
          <el-table-column label="張數" width="90" prop="lots" />
          <el-table-column label="價格" width="90" prop="price" />
          <el-table-column label="手續費" width="90"><template #default="{ row }">{{ row.fee ?? '—' }}</template></el-table-column>
          <el-table-column label="證交稅" width="90"><template #default="{ row }">{{ row.tax ?? '—' }}</template></el-table-column>
          <el-table-column label="備註" min-width="120"><template #default="{ row }">{{ row.note }}</template></el-table-column>
          <el-table-column label="操作" width="70" fixed="right">
            <template #default="{ row }">
              <el-button link type="danger" size="small" @click="delTrade(row)">刪除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !trades.length" description="尚無交易紀錄" />
      </el-tab-pane>

      <!-- 已實現績效 -->
      <el-tab-pane label="已實現績效" name="realized">
        <el-table :data="data.realized" v-loading="loading" stripe height="62vh">
          <el-table-column label="股票" width="140">
            <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
          </el-table-column>
          <el-table-column label="買進日" width="120" prop="buy_date" />
          <el-table-column label="賣出日" width="120" prop="sell_date" />
          <el-table-column label="持有天" width="80"><template #default="{ row }">{{ row.days }}</template></el-table-column>
          <el-table-column label="張數" width="80" prop="lots" />
          <el-table-column label="買價" width="90" prop="buy_price" />
          <el-table-column label="賣價" width="90" prop="sell_price" />
          <el-table-column label="報酬率" width="100">
            <template #default="{ row }"><span :style="{ color: up(row.ret_pct) }">{{ pct(row.ret_pct) }}</span></template>
          </el-table-column>
          <el-table-column label="損益" min-width="110">
            <template #default="{ row }"><b :style="{ color: up(row.pnl) }">{{ money(row.pnl) }}</b></template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !data.realized.length" description="尚無已實現（平倉）交易" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
