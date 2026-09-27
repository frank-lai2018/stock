<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getBreakoutRanking } from '../api'
import WatchlistAddButton from '../components/WatchlistAddButton.vue'

const loading = ref(false)
const items = ref([])
const asOf = ref('')
const method = ref('')
const stockIdInput = ref('')
const analysisStockId = ref('')
const analysis = ref(null)
const analysisError = ref('')
const secType = ref('stock')
const recent = ref(3)
const minAmt = ref(20000000)
const minScore = ref(0)
const status = ref('')

const STATUS = {
  priority: { label: '優先評估', type: 'success' },
  watch: { label: '等待／觀察', type: 'warning' },
  skip: { label: '略過', type: 'info' },
}

const shown = computed(() => items.value.filter((r) => {
  const d = r.decision || {}
  return d.score >= Number(minScore.value || 0) && (!status.value || d.status === status.value)
}))

const summary = computed(() => ({
  priority: shown.value.filter((r) => r.decision?.status === 'priority').length,
  watch: shown.value.filter((r) => r.decision?.status === 'watch').length,
  skip: shown.value.filter((r) => r.decision?.status === 'skip').length,
}))

async function load() {
  loading.value = true
  try {
    const data = await getBreakoutRanking({
      security_type: secType.value, recent: recent.value, min_amt: minAmt.value,
      stock_id: analysisStockId.value || undefined, limit: 300,
    })
    items.value = data.items || []
    asOf.value = data.as_of || ''
    method.value = data.method || ''
    analysis.value = data.analysis || null
    analysisError.value = ''
  } catch (e) {
    items.value = []
    analysis.value = null
    analysisError.value = e?.response?.data?.detail || e.message
    ElMessage.error('排行載入失敗：' + analysisError.value)
  } finally {
    loading.value = false
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
  minScore.value = 0
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

const pct = (v) => v == null ? '—' : `${Number(v).toFixed(1)}%`
const num = (v, n = 1) => v == null ? '—' : Number(v).toFixed(n)
const money = (v) => v == null ? '—' : Number(v).toLocaleString('zh-TW', { maximumFractionDigits: 0 })
const statusOf = (row) => STATUS[row.decision?.status] || STATUS.skip
</script>

<template>
  <div class="decision-page">
    <el-card shadow="never" class="toolbar">
      <div class="toolbar-row">
        <div>
          <h2>突破決策</h2>
          <div class="subtitle">把底部反轉與整理突破做第二次排序；先看風險，再決定是否進場。</div>
        </div>
        <div class="filters">
          <el-select v-model="secType" :disabled="!!analysisStockId" style="width: 112px" @change="load">
            <el-option label="只看個股" value="stock" />
            <el-option label="只看 ETF" value="etf" />
          </el-select>
          <el-select v-model="recent" style="width: 112px" @change="load">
            <el-option label="近 3 日" :value="3" />
            <el-option label="近 2 週" :value="10" />
            <el-option label="近 1 月" :value="20" />
          </el-select>
          <el-select v-model="minAmt" :disabled="!!analysisStockId" style="width: 145px" @change="load">
            <el-option label="均額 2千萬+" :value="20000000" />
            <el-option label="均額 5千萬+" :value="50000000" />
            <el-option label="均額 1億+" :value="100000000" />
          </el-select>
          <el-select v-model="status" clearable placeholder="全部結論" style="width: 125px">
            <el-option v-for="(v, k) in STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
          <el-input-number v-model="minScore" :min="0" :max="100" :step="5" controls-position="right" style="width: 112px" />
          <span class="muted">最低分</span>
          <el-button type="primary" :loading="loading" @click="load">重新掃描</el-button>
        </div>
      </div>
      <div class="stock-analyzer">
        <b>指定個股分析</b>
        <el-input v-model="stockIdInput" clearable maxlength="10" placeholder="輸入股票代號，例如 2330"
                  style="width: 235px" @keyup.enter="analyzeStock" />
        <el-button type="primary" :loading="loading" @click="analyzeStock">分析這檔</el-button>
        <el-button v-if="analysisStockId" @click="clearStockAnalysis">回到全市場</el-button>
        <span v-if="analysisStockId" class="muted">個股模式會忽略證券類別、流動性及母體限制</span>
      </div>
    </el-card>

    <div class="summary-grid">
      <el-card shadow="never" class="summary priority" @click="status = status === 'priority' ? '' : 'priority'">
        <span>優先評估</span><b>{{ summary.priority }}</b>
      </el-card>
      <el-card shadow="never" class="summary watch" @click="status = status === 'watch' ? '' : 'watch'">
        <span>等待／觀察</span><b>{{ summary.watch }}</b>
      </el-card>
      <el-card shadow="never" class="summary skip" @click="status = status === 'skip' ? '' : 'skip'">
        <span>略過</span><b>{{ summary.skip }}</b>
      </el-card>
      <el-card shadow="never" class="summary meta">
        <span>資料日</span><b>{{ asOf || '—' }}</b>
      </el-card>
    </div>

    <el-alert v-if="analysisStockId" :type="analysisError ? 'error' : items.length ? 'success' : 'warning'" :closable="false" show-icon class="notice">
      <template #title>{{ analysisStockId }} 個股突破分析</template>
      {{ analysisError || analysis?.reason || `找到 ${items.length} 個近期已確認的突破訊號` }}
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="notice">
      <template #title>分數只用來比較同批候選，不是買進指令或報酬預測</template>
      參考停損統一用「頸線下 1 ATR」以便比較；實際下單前仍應看圖確認結構、隔日跳空與產業持倉。
    </el-alert>

    <el-table :data="shown" v-loading="loading" stripe border height="calc(100vh - 375px)" row-key="stock_id">
      <el-table-column type="index" label="#" width="48" fixed />
      <el-table-column label="結論" width="105" fixed>
        <template #default="{ row }">
          <el-tag :type="statusOf(row).type" effect="dark">{{ statusOf(row).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="總分" width="92" sortable :sort-method="(a, b) => a.decision.score - b.decision.score" fixed>
        <template #default="{ row }"><span class="score">{{ row.decision.score }}</span></template>
      </el-table-column>
      <el-table-column label="股票" width="145" fixed>
        <template #default="{ row }">
          <router-link :to="`/stock/${row.stock_id}`" class="stock-link">{{ row.stock_id }} {{ row.name }}</router-link>
          <div class="muted small">{{ row.industry || '—' }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="pattern_name" label="型態" min-width="120" />
      <el-table-column label="五面向" min-width="255">
        <template #default="{ row }">
          <div class="parts">
            <span>趨 {{ row.decision.parts.trend }}/30</span>
            <span>破 {{ row.decision.parts.breakout }}/25</span>
            <span>盈 {{ row.decision.parts.fundamental }}/20</span>
            <span>籌 {{ row.decision.parts.quality }}/10</span>
            <span>險 {{ row.decision.parts.risk }}/15</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="突破品質" width="145">
        <template #default="{ row }">
          <div>量比 <b>{{ num(row.breakout?.vol_ratio, 2) }}</b></div>
          <div>離頸線 <b :class="{ danger: row.decision.extension_pct > 5 }">{{ pct(row.decision.extension_pct) }}</b></div>
          <div>RS <b>{{ row.rs_rating ?? '—' }}</b></div>
        </template>
      </el-table-column>
      <el-table-column label="風險計畫" width="170">
        <template #default="{ row }">
          <div>參考進場 {{ num(row.decision.entry, 2) }}</div>
          <div>停損 {{ num(row.decision.stop, 2) }}（{{ pct(row.decision.risk_pct) }}）</div>
          <div>目標 {{ num(row.decision.target, 2) }}・R/R <b>{{ num(row.decision.rr, 2) }}</b></div>
        </template>
      </el-table-column>
      <el-table-column label="型態20日回測" width="150">
        <template #default="{ row }">
          <template v-if="row.decision.backtest_20d">
            <div>超額 {{ pct(row.decision.backtest_20d.avg_excess) }}</div>
            <div>勝率 {{ pct(row.decision.backtest_20d.win_rate) }}</div>
            <div class="muted">n={{ row.decision.backtest_20d.n }}</div>
          </template>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="基本面／籌碼" width="175">
        <template #default="{ row }">
          <div>EPS YoY {{ pct(row.eps_yoy) }}</div>
          <div>營收 YoY {{ pct(row.rev_yoy) }}</div>
          <div>PER 位階 {{ pct(row.per_pctile) }}</div>
          <div class="muted">均額 {{ money(row.amt20) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="檢查結果" min-width="250">
        <template #default="{ row }">
          <div v-if="!row.decision.blockers.length" class="pass">硬條件全數通過</div>
          <div v-for="x in row.decision.blockers" :key="x" class="blocker">• {{ x }}</div>
          <div v-for="x in row.decision.notes" :key="x" class="note">• {{ x }}</div>
        </template>
      </el-table-column>
      <el-table-column label="追蹤" width="82" fixed="right">
        <template #default="{ row }"><WatchlistAddButton :row="row" /></template>
      </el-table-column>
      <template #empty>
        <el-empty :description="analysisError || (analysisStockId ? `${analysisStockId} 近 ${recent} 日沒有已確認的多方型態突破` : '目前條件沒有突破候選')" />
      </template>
    </el-table>
    <div class="method">{{ method }}｜目前顯示 {{ shown.length }} 檔</div>
  </div>
</template>

<style scoped>
.decision-page { max-width: 1800px; margin: 0 auto; }
.toolbar { margin-bottom: 10px; }
.toolbar-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.subtitle, .muted { color: #909399; }
.small { font-size: 12px; margin-top: 3px; }
.filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stock-analyzer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-bottom: 10px; }
.summary { cursor: pointer; }
.summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 13px 18px; }
.summary b { font-size: 25px; }
.summary.priority { border-left: 4px solid #67c23a; }
.summary.watch { border-left: 4px solid #e6a23c; }
.summary.skip { border-left: 4px solid #909399; }
.summary.meta { border-left: 4px solid #409eff; cursor: default; }
.notice { margin-bottom: 10px; }
.score { font-size: 22px; font-weight: 750; color: #303133; }
.stock-link { color: #337ecc; font-weight: 650; text-decoration: none; }
.parts { display: flex; gap: 4px; flex-wrap: wrap; }
.parts span { background: #f2f6fc; border-radius: 4px; padding: 2px 5px; font-size: 12px; }
.danger, .blocker { color: #f56c6c; }
.pass { color: #529b2e; font-weight: 600; }
.note { color: #a77700; }
.method { color: #909399; font-size: 12px; margin-top: 7px; text-align: right; }
@media (max-width: 900px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
