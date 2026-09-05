<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPortfolio, getTrades, addTrade, deleteTrade, searchStocks } from '../api'

const router = useRouter()
const curYear = new Date().getFullYear()
const year = ref(curYear)
const years = ref([curYear])
const data = ref({ items: [], summary: null, realized: [], as_of: null })
const trades = ref([])
const loading = ref(false)
const tab = ref('holdings')
const form = ref({ stock_id: '', label: '', action: 'buy', trade_date: '', shares: null, price: null, fee: null, tax: null, trade_type: '現股', note: '' })
const TRADE_TYPES = ['現股', '現股當沖', '融資', '融券']

// 評級 → 台股慣例上色（strong=紅偏多、reduce=綠偏空）
const LEVEL = {
  strong: { label: '續抱', color: '#EA4C4C' },
  watch: { label: '觀察', color: '#E6A23C' },
  reduce: { label: '減碼', color: '#3F9E5A' },
}
const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }

// 停利／停損警示（來自復盤發現：賠錢單中途平均曾浮盈 +3.94%，17 筆曾賺逾 10% 最後收黑）
const ALERT = {
  trim: { label: '該停利', type: 'warning', hint: '曾賺逾 10%，已回吐一半以上獲利' },
  watch: { label: '留意', type: 'info', hint: '曾賺逾 10%，開始回吐獲利' },
  stop: { label: '該停損', type: 'danger', hint: '未實現虧損已逾 10%' },
}

const money = (v) => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'))
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(2) + '%')
const up = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')

async function load() {
  loading.value = true
  try {
    data.value = await getPortfolio(year.value)
    trades.value = await getTrades(year.value)
    if (data.value.years?.length) years.value = data.value.years
    if (data.value.year) year.value = data.value.year
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
  if (!form.value.shares) return ElMessage.warning('請填股數')
  if (form.value.price === null || form.value.price === undefined || form.value.price < 0)
    return ElMessage.warning('請填價格（配股填 0）')
  try {
    await addTrade({
      stock_id: form.value.stock_id, action: form.value.action, trade_date: form.value.trade_date,
      shares: form.value.shares, price: form.value.price,
      fee: form.value.fee, tax: form.value.tax,
      trade_type: form.value.trade_type || null, note: form.value.note || null,
    })
    ElMessage.success('已記錄')
    form.value = { stock_id: '', label: '', action: form.value.action, trade_date: form.value.trade_date,
                   shares: null, price: null, fee: null, tax: null, trade_type: form.value.trade_type, note: '' }
    await load()
  } catch (e) {
    ElMessage.error('儲存失敗：' + (e?.response?.data?.detail || e.message))
  }
}

async function delTrade(row) {
  try {
    await ElMessageBox.confirm(
      `刪除這筆交易？${row.stock_id} ${row.action === 'buy' ? '買' : '賣'} ${row.shares}股 @${row.price}`,
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
    <!-- 年度切換 -->
    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px">
      <span style="color: #999; font-size: 13px">年度</span>
      <el-select v-model="year" style="width: 120px" @change="load">
        <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="y" />
      </el-select>
      <el-tag v-if="year === curYear" type="danger" effect="plain" size="small">當年度</el-tag>
      <el-tag v-else type="info" effect="plain" size="small">歷史交易</el-tag>
      <span style="color: #bbb; font-size: 12px">
        指標為 {{ year }} 年度；未平倉／未實現／診斷分佈為目前持股當下快照
      </span>
    </div>

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
        <div style="color: #999">已實現損益（{{ year }}）</div>
        <div style="font-size: 22px; font-weight: 700" :style="{ color: up(data.summary.realized_total) }">
          {{ money(data.summary.realized_total) }}
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 4px">
          平倉 {{ data.summary.closed_n }} 筆｜勝率 {{ data.summary.win_rate ?? '—' }}%
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 200px">
        <div style="color: #999">未實現損益</div>
        <div style="font-size: 22px; font-weight: 700" :style="{ color: up(data.summary.unrealized) }">
          {{ money(data.summary.unrealized) }}
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 4px">
          報酬率 <b :style="{ color: up(data.summary.unrealized_pct) }">{{ pct(data.summary.unrealized_pct) }}</b>
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 200px">
        <div style="color: #999">總損益（{{ year }}已實現＋未實現）</div>
        <div style="font-size: 22px; font-weight: 700" :style="{ color: up(data.summary.total_pnl) }">
          {{ money(data.summary.total_pnl) }}
        </div>
      </el-card>
      <el-card shadow="never" style="flex: 1 1 240px">
        <div style="color: #999">停利／停損監控</div>
        <div style="font-size: 16px; margin-top: 6px" v-if="data.summary.alerts">
          <span style="color: #E6A23C">該停利 {{ data.summary.alerts.trim }}</span>
          <span style="color: #909399; margin: 0 8px">留意 {{ data.summary.alerts.watch }}</span>
          <span style="color: #EA4C4C">該停損 {{ data.summary.alerts.stop }}</span>
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 6px">
          以「最後一次加碼日」起算最大浮盈，回吐過半即提醒
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
          <el-table-column label="股數" width="90"><template #default="{ row }">{{ money(row.shares) }}</template></el-table-column>
          <el-table-column label="均成本" width="84"><template #default="{ row }">{{ row.cost ?? '—' }}</template></el-table-column>
          <el-table-column label="現價" width="76"><template #default="{ row }">{{ row.close ?? '—' }}</template></el-table-column>
          <el-table-column label="未實現" width="150">
            <template #default="{ row }">
              <span :style="{ color: up(row.unrealized_pct) }">{{ pct(row.unrealized_pct) }}</span>
              <span v-if="row.unrealized != null" style="color: #999; font-size: 12px"> {{ money(row.unrealized) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="曾賺" width="120">
            <template #default="{ row }">
              <span :style="{ color: up(row.peak_gain) }">{{ pct(row.peak_gain) }}</span>
              <div v-if="row.peak_date" style="color: #bbb; font-size: 11px; line-height: 1">
                高點 {{ row.peak_date.slice(5) }} @{{ row.peak_price }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="回吐" width="110">
            <template #default="{ row }">
              <span v-if="row.giveback != null" style="color: #3F9E5A">
                −{{ (row.giveback * 100).toFixed(1) }}pp
              </span>
              <span v-else>—</span>
              <div v-if="row.giveback_ratio != null" style="color: #bbb; font-size: 11px; line-height: 1">
                吐回 {{ (row.giveback_ratio * 100).toFixed(0) }}%
              </div>
            </template>
          </el-table-column>
          <el-table-column label="警示" width="92">
            <template #default="{ row }">
              <el-tooltip v-if="row.alert" :content="ALERT[row.alert].hint" placement="top">
                <el-tag :type="ALERT[row.alert].type" size="small" effect="dark">{{ ALERT[row.alert].label }}</el-tag>
              </el-tooltip>
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
      <el-tab-pane :label="`交易紀錄（${year}）`" name="trades">
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
            <el-select v-model="form.trade_type" placeholder="交易類別" allow-create filterable
                       default-first-option style="width: 130px">
              <el-option v-for="t in TRADE_TYPES" :key="t" :label="t" :value="t" />
            </el-select>
            <el-date-picker v-model="form.trade_date" type="date" value-format="YYYY-MM-DD"
                            placeholder="交易日" style="width: 150px" />
            <el-input-number v-model="form.shares" :min="0" :step="1000" controls-position="right"
                             placeholder="股數" style="width: 130px" /><span style="color: #999">股</span>
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
            手續費/證交稅可留空（視為 0）；賣出以 FIFO 配對買進，自動算已實現損益。<b>配股（股票股利）以「買進」記錄、價格填 0</b>，可攤低平均成本。日後補歷史紀錄照樣填即可。
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
          <el-table-column label="類別" width="90">
            <template #default="{ row }">{{ row.trade_type || '—' }}</template>
          </el-table-column>
          <el-table-column label="股數" width="100"><template #default="{ row }">{{ money(row.shares) }}</template></el-table-column>
          <el-table-column label="價格" width="90" prop="price" />
          <el-table-column label="手續費" width="80"><template #default="{ row }">{{ row.fee ?? '—' }}</template></el-table-column>
          <el-table-column label="證交稅" width="80"><template #default="{ row }">{{ row.tax ?? '—' }}</template></el-table-column>
          <el-table-column label="損益(賣)" width="110">
            <template #default="{ row }">
              <b v-if="row.pnl != null" :style="{ color: up(row.pnl) }">{{ money(row.pnl) }}</b>
              <span v-else style="color: #ccc">—</span>
            </template>
          </el-table-column>
          <el-table-column label="備註" min-width="110"><template #default="{ row }">{{ row.note }}</template></el-table-column>
          <el-table-column label="操作" width="70" fixed="right">
            <template #default="{ row }">
              <el-button link type="danger" size="small" @click="delTrade(row)">刪除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !trades.length" :description="`${year} 年尚無交易紀錄`" />
      </el-tab-pane>

      <!-- 已實現績效 -->
      <el-tab-pane :label="`已實現績效（${year}）`" name="realized">
        <el-table :data="data.realized" v-loading="loading" stripe height="62vh">
          <el-table-column label="股票" width="140">
            <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
          </el-table-column>
          <el-table-column label="買進日" width="120" prop="buy_date" />
          <el-table-column label="賣出日" width="120" prop="sell_date" />
          <el-table-column label="持有天" width="80"><template #default="{ row }">{{ row.days }}</template></el-table-column>
          <el-table-column label="股數" width="100"><template #default="{ row }">{{ money(row.shares) }}</template></el-table-column>
          <el-table-column label="買價" width="90" prop="buy_price" />
          <el-table-column label="賣價" width="90" prop="sell_price" />
          <el-table-column label="報酬率" width="100">
            <template #default="{ row }"><span :style="{ color: up(row.ret_pct) }">{{ pct(row.ret_pct) }}</span></template>
          </el-table-column>
          <el-table-column label="損益" min-width="110">
            <template #default="{ row }"><b :style="{ color: up(row.pnl) }">{{ money(row.pnl) }}</b></template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !data.realized.length" :description="`${year} 年尚無已實現（平倉）交易`" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
