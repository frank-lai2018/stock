<script setup>
// 週線突破（實驗）：先看週線趨勢與突破，再用日線找進場點。規則在 backend/app/weekly_breakout.py。
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getWeeklyBreakout } from '../api'
import WatchlistAddButton from '../components/WatchlistAddButton.vue'
import WeeklyBreakoutGuide from '../components/WeeklyBreakoutGuide.vue'

const STATUS = {
  priority: { label: '可進場', type: 'success' },
  waiting: { label: '等回測', type: 'warning' },
  watch: { label: '接近壓力', type: 'info' },
}
const STAGE = {
  pending_week: { label: '待週收盤', type: 'info' },
  failed: { label: '突破失敗', type: 'danger' },
  none: { label: '不成立', type: 'info' },
}
const statusOf = (w) => (w ? STAGE[w.stage] || STATUS[w.status] : null) || { label: '資料不足', type: 'info' }

const loading = ref(false)
const items = ref([])
const asOf = ref('')
const method = ref('')
const secType = ref('stock')
const minAmt = ref(20000000)
const status = ref('')
const stockIdInput = ref('')
const analysisStockId = ref('')
const analysis = ref(null)
const analysisError = ref('')

const shown = computed(() => items.value.filter((r) => !status.value || r.weekly?.status === status.value))
const count = (k) => items.value.filter((r) => r.weekly?.status === k).length
const summary = computed(() => ({ priority: count('priority'), waiting: count('waiting'), watch: count('watch') }))
const pick = (k) => { status.value = status.value === k ? '' : k }

const num = (v, n = 2) => v == null ? '—' : Number(v).toFixed(n)
const pct = (v, n = 1) => v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(n)}%`
const money = (v) => v == null ? '—' : Number(v).toLocaleString('zh-TW', { maximumFractionDigits: 0 })
const mmdd = (d) => d ? String(d).slice(5).replace('-', '/') : '—'
const fromMa30 = (w) => w?.weekly?.ma30w ? (w.weekly.week_close / w.weekly.ma30w - 1) * 100 : null
// 黃字提醒：「尚待樣本外驗證」每列都有，改在上方說一次
const notesOf = (w) => (w?.notes || []).filter((x) => x !== '尚待樣本外驗證')

let seq = 0
async function load() {
  const id = ++seq                    // 全市場掃描約 10 秒；期間改查個股時，只採用最後一次的結果
  loading.value = true
  try {
    const data = await getWeeklyBreakout({
      security_type: secType.value, min_amt: minAmt.value, stock_id: analysisStockId.value || undefined,
    })
    if (id !== seq) return
    // pattern／pattern_name 給「加入自選股」存成快照
    items.value = (data.items || []).map((r) => ({ ...r, pattern: 'weekly_breakout', pattern_name: r.weekly?.pattern_name }))
    asOf.value = data.as_of || ''
    method.value = data.method || ''
    analysis.value = data.analysis || null
    analysisError.value = ''
  } catch (e) {
    if (id !== seq) return
    items.value = []
    analysis.value = null
    analysisError.value = e?.response?.data?.detail || e.message
    ElMessage.error('週線突破載入失敗：' + analysisError.value)
  } finally {
    if (id === seq) loading.value = false
  }
}

async function analyzeStock() {
  const id = stockIdInput.value.trim().toUpperCase()
  if (!id) {
    ElMessage.warning('請輸入股票代號')
    return
  }
  analysisStockId.value = id
  stockIdInput.value = id
  status.value = ''
  await load()
}

async function clearStockAnalysis() {
  stockIdInput.value = ''
  analysisStockId.value = ''
  analysis.value = null
  analysisError.value = ''
  await load()
}

onMounted(load)
</script>

<template>
  <div class="weekly-page">
    <el-card shadow="never" class="toolbar">
      <div class="toolbar-row">
        <div>
          <h2>週線突破 <el-tag type="warning" effect="plain">實驗</el-tag></h2>
          <div class="subtitle">先看週線：趨勢向上、週收盤突破整理區的壓力線；再看日線：拉回到壓力線附近、轉強才進場。</div>
        </div>
        <div class="filters">
          <el-select v-model="secType" :disabled="!!analysisStockId" style="width: 112px" @change="load">
            <el-option label="只看個股" value="stock" />
            <el-option label="只看 ETF" value="etf" />
          </el-select>
          <el-select v-model="minAmt" :disabled="!!analysisStockId" style="width: 145px" @change="load">
            <el-option label="均額 2千萬+" :value="20000000" />
            <el-option label="均額 5千萬+" :value="50000000" />
            <el-option label="均額 1億+" :value="100000000" />
          </el-select>
          <el-select v-model="status" clearable placeholder="全部結論" style="width: 125px">
            <el-option v-for="(v, k) in STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
          <el-button type="primary" :loading="loading" @click="load">重新掃描</el-button>
        </div>
      </div>
      <div class="stock-analyzer">
        <b>指定個股分析</b>
        <el-input v-model="stockIdInput" clearable maxlength="10" placeholder="輸入股票代號，例如 3105"
                  style="width: 235px" @keyup.enter="analyzeStock" />
        <el-button type="primary" :loading="loading" @click="analyzeStock">分析這檔</el-button>
        <el-button v-if="analysisStockId" @click="clearStockAnalysis">回到全市場</el-button>
        <span v-if="analysisStockId" class="muted">個股模式不管證券類別、流動性和母體，不成立也會列出原因</span>
      </div>
    </el-card>

    <div class="summary-grid">
      <el-card shadow="never" class="summary priority" @click="pick('priority')"><span>可進場</span><b>{{ summary.priority }}</b></el-card>
      <el-card shadow="never" class="summary waiting" @click="pick('waiting')"><span>等回測</span><b>{{ summary.waiting }}</b></el-card>
      <el-card shadow="never" class="summary watch" @click="pick('watch')"><span>接近壓力／待週收盤</span><b>{{ summary.watch }}</b></el-card>
      <el-card shadow="never" class="summary meta"><span>資料日</span><b>{{ asOf || '—' }}</b></el-card>
    </div>

    <el-alert v-if="analysisStockId" :type="analysisError ? 'error' : analysis?.status === 'priority' ? 'success' : 'warning'"
              :closable="false" show-icon class="notice">
      <template #title>{{ analysisStockId }} 週線突破分析：{{ analysisError ? '載入失敗' : statusOf(items[0]?.weekly).label }}</template>
      {{ analysisError || analysis?.reason || '已到日線進場點' }}
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="notice">
      <template #title>實驗策略，尚待樣本外驗證；「可進場」不是買進指令</template>
      要同時套用資金、產業上限和大盤濾網，請到「今日決策中心」選篩選條件「週線突破（實驗）」；歷史回測在同頁的「策略研究」。
    </el-alert>

    <el-table :data="shown" v-loading="loading" stripe border height="calc(100vh - 420px)" row-key="stock_id"
              :empty-text="analysisError || '目前條件沒有週線突破候選'">
      <el-table-column type="index" label="#" width="48" fixed />
      <el-table-column label="結論" width="100" fixed>
        <template #default="{ row }">
          <el-tag :type="statusOf(row.weekly).type" :effect="row.weekly?.status === 'priority' ? 'dark' : 'plain'">
            {{ statusOf(row.weekly).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="股票" width="150" fixed>
        <template #default="{ row }">
          <router-link :to="`/stock/${row.stock_id}`" class="stock-link">{{ row.stock_id }} {{ row.name }}</router-link>
          <div class="muted small">{{ row.industry || '—' }}</div>
          <div class="small">RS <b>{{ row.rs_rating ?? '—' }}</b><el-tag v-if="row.trend_template" size="small" type="danger" effect="plain" class="tt">趨勢模板</el-tag></div>
        </template>
      </el-table-column>
      <el-table-column label="週線趨勢" width="185">
        <template #default="{ row }">
          <template v-if="row.weekly?.weekly?.ma30w">
            <div>週收 {{ num(row.weekly.weekly.week_close) }}</div>
            <div>30週線 {{ num(row.weekly.weekly.ma30w) }}（<span :class="fromMa30(row.weekly) > 0 ? 'up' : 'down'">{{ pct(fromMa30(row.weekly)) }}</span>）</div>
            <div>30週線 4 週 <span :class="row.weekly.weekly.ma30w_slope_pct > 0 ? 'up' : 'down'">{{ pct(row.weekly.weekly.ma30w_slope_pct, 2) }}</span></div>
            <div class="small" :class="row.weekly.weekly.ma10_above_ma30 ? 'up' : 'muted'">10週線{{ row.weekly.weekly.ma10_above_ma30 ? '在' : '還在' }} 30週線{{ row.weekly.weekly.ma10_above_ma30 ? '上' : '下' }}</div>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="週線突破" width="215">
        <template #default="{ row }">
          <template v-if="row.weekly?.pivot">
            <div>壓力線 <b>{{ num(row.weekly.pivot) }}</b></div>
            <div class="small muted">前 52 週最高週收（{{ mmdd(row.weekly.weekly.pivot_week) }} 那週）</div>
            <div v-if="row.weekly.stage === 'entry' || row.weekly.stage === 'pullback' || row.weekly.stage === 'failed'">
              突破週 {{ mmdd(row.weekly.weekly.week_start) }}～{{ mmdd(row.weekly.weekly.week_end) }}
            </div>
            <div>整理 {{ row.weekly.weekly.base_weeks }} 週・最深回檔 {{ num(row.weekly.weekly.base_depth_pct, 0) }}%</div>
            <div v-if="row.weekly.stage !== 'near' && row.weekly.stage !== 'pending_week'">週量比 <b>{{ num(row.weekly.weekly.week_volume_ratio) }}</b></div>
          </template>
          <span v-else class="muted small">{{ row.reason || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="日線進場" min-width="230">
        <template #default="{ row }">
          <template v-if="row.weekly">
            <div>收盤 {{ num(row.close) }}・離壓力線 <b :class="{ danger: row.weekly.extension_pct > 5 }">{{ pct(row.weekly.extension_pct) }}</b></div>
            <div v-if="row.weekly.pivot" class="small">買點區 {{ num(row.weekly.pivot) }}～{{ num(row.weekly.max_entry) }}</div>
            <div v-for="x in row.weekly.blockers" :key="x" class="blocker">• {{ x }}</div>
            <div v-if="row.weekly.status === 'priority'" class="pass">• {{ row.weekly.notes[0] }}</div>
          </template>
          <span v-else class="muted small">{{ row.reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="交易計畫" width="175">
        <template #default="{ row }">
          <template v-if="row.weekly?.stop && ['priority', 'waiting'].includes(row.weekly.status)">
            <div>參考進場 {{ num(row.weekly.entry) }}</div>
            <div>停損 {{ num(row.weekly.stop) }}（{{ row.weekly.risk_pct == null ? '—' : `${num(row.weekly.risk_pct, 1)}%` }}）</div>
            <div>追價上限 {{ num(row.weekly.max_entry) }}</div>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="提醒" min-width="250">
        <template #default="{ row }">
          <div v-for="x in notesOf(row.weekly).slice(row.weekly?.status === 'priority' ? 1 : 0)" :key="x" class="note">• {{ x }}</div>
        </template>
      </el-table-column>
      <el-table-column label="基本面" width="140">
        <template #default="{ row }">
          <div>EPS YoY {{ pct(row.eps_yoy) }}</div>
          <div>營收 YoY {{ pct(row.rev_yoy) }}</div>
          <div class="muted">均額 {{ money(row.amt20) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="追蹤" width="70" fixed="right">
        <template #default="{ row }"><WatchlistAddButton :row="row" /></template>
      </el-table-column>
    </el-table>
    <div class="method">{{ method }}｜目前顯示 {{ shown.length }} 檔</div>
    <WeeklyBreakoutGuide />
  </div>
</template>

<style scoped>
.weekly-page { max-width: 1900px; margin: 0 auto; }
.toolbar { margin-bottom: 10px; }
.toolbar-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.subtitle, .muted { color: #909399; }
.small { font-size: 12px; margin-top: 2px; }
.filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stock-analyzer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-bottom: 10px; }
.summary { cursor: pointer; }
.summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 13px 18px; }
.summary b { font-size: 25px; }
.summary.priority { border-left: 4px solid #67c23a; }
.summary.waiting { border-left: 4px solid #e6a23c; }
.summary.watch { border-left: 4px solid #909399; }
.summary.meta { border-left: 4px solid #409eff; cursor: default; }
.notice { margin-bottom: 10px; }
.stock-link { color: #337ecc; font-weight: 650; text-decoration: none; }
.tt { margin-left: 6px; }
.up { color: #EA4C4C; }
.down { color: #3F9E5A; }
.danger, .blocker { color: #f56c6c; }
.pass { color: #529b2e; font-weight: 600; }
.note { color: #a77700; }
.method { color: #909399; font-size: 12px; margin-top: 7px; text-align: right; }
@media (max-width: 900px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
