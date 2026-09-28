<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getBreakoutPatterns, getPriceActionDecisions } from '../api'
import PriceActionTable, { CONCLUSIONS, STATES } from '../components/PriceActionTable.vue'
import PriceActionGuide from '../components/PriceActionGuide.vue'

const loading = ref(false)
const items = ref([])
const asOf = ref('')
const method = ref('')
const stockIdInput = ref('')
const analysisStockId = ref('')
const analysis = ref(null)
const analysisError = ref('')
const secType = ref('stock')
const minAmt = ref(20000000)
const lookback = ref(5)
const expiry = ref(5)
const chartPattern = ref('any')
const patternRecent = ref(10)
const bottomPatterns = ref([])
const continuationPatterns = ref([])
const epsMin = ref(null)
const revenueMonthStreak = ref(0)
const revenueQuarterStreak = ref(0)
const grossMarginQuarterStreak = ref(0)
const minScore = ref(0)
const conclusion = ref('')
const signalState = ref('')
const direction = ref('')

const baseShown = computed(() => items.value.filter((row) => {
  const d = row.decision || {}
  return d.score >= Number(minScore.value || 0)
    && (!signalState.value || d.status === signalState.value)
    && (!direction.value || d.direction === direction.value)
}))

const shown = computed(() => baseShown.value.filter((row) => (
  !conclusion.value || row.decision?.conclusion === conclusion.value
)))

const summary = computed(() => Object.fromEntries(
  Object.keys(CONCLUSIONS).map((key) => [key, baseShown.value.filter((r) => r.decision?.conclusion === key).length]),
))

const analysisMessage = computed(() => {
  if (analysisError.value) return analysisError.value
  if (analysis.value?.reason) return analysis.value.reason
  const chartName = items.value[0]?.chart_pattern_name
  return chartName ? `已完成分析；同時命中 ${chartName}` : '已完成分析；近期沒有波段突破型態'
})

async function load() {
  loading.value = true
  try {
    const data = await getPriceActionDecisions({
      security_type: secType.value,
      min_amt: minAmt.value,
      lookback: lookback.value,
      expiry: expiry.value,
      chart_pattern: chartPattern.value,
      pattern_recent: patternRecent.value,
      eps_min: epsMin.value == null || epsMin.value === '' ? undefined : epsMin.value,
      revenue_month_streak: revenueMonthStreak.value,
      revenue_quarter_streak: revenueQuarterStreak.value,
      gross_margin_quarter_streak: grossMarginQuarterStreak.value,
      stock_id: analysisStockId.value || undefined,
      limit: 300,
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
    ElMessage.error('裸 K 決策載入失敗：' + analysisError.value)
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
  conclusion.value = ''
  signalState.value = ''
  direction.value = ''
  await load()
}

async function clearStockAnalysis() {
  stockIdInput.value = ''
  analysisStockId.value = ''
  analysis.value = null
  analysisError.value = ''
  await load()
}

function toggleConclusion(key) {
  conclusion.value = conclusion.value === key ? '' : key
}

onMounted(async () => {
  try {
    const [bottom, continuation] = await Promise.all([
      getBreakoutPatterns('bottom'), getBreakoutPatterns('continuation'),
    ])
    // 只列可能多方突破的型態（下降三角只判跌破，選了一定沒結果）
    bottomPatterns.value = (bottom || []).filter((item) => item.bull !== false)
    continuationPatterns.value = (continuation || []).filter((item) => item.bull !== false)
  } catch (e) {
    ElMessage.warning('波段型態清單讀取失敗，仍可使用裸 K 掃描')
    chartPattern.value = ''
  }
  await load()
})
</script>

<template>
  <div class="price-action-page">
    <el-card shadow="never" class="toolbar">
      <div class="toolbar-row">
        <div>
          <h2>型態＋裸 K 決策</h2>
          <div class="subtitle">先找波段型態，再用裸 K 的結構、位置、確認與風險做第二層篩選。</div>
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
          <el-select v-model="lookback" style="width: 125px" @change="load">
            <el-option label="近 3 根訊號" :value="3" />
            <el-option label="近 5 根訊號" :value="5" />
            <el-option label="近 10 根訊號" :value="10" />
          </el-select>
          <el-select v-model="expiry" style="width: 125px" @change="load">
            <el-option label="3 根內觸發" :value="3" />
            <el-option label="5 根內觸發" :value="5" />
            <el-option label="10 根內觸發" :value="10" />
          </el-select>
          <el-button type="primary" :loading="loading" @click="load">重新掃描</el-button>
        </div>
      </div>
      <div class="stock-analyzer">
        <b>指定個股分析</b>
        <el-input v-model="stockIdInput" clearable maxlength="10" placeholder="輸入股票代號，例如 2330"
                  style="width: 235px" @keyup.enter="analyzeStock" />
        <el-button type="primary" :loading="loading" @click="analyzeStock">分析這檔</el-button>
        <el-button v-if="analysisStockId" @click="clearStockAnalysis">回到全市場</el-button>
        <span v-if="analysisStockId" class="muted">個股模式忽略母體、流動性與基本面門檻，但保留裸 K 觀察窗</span>
      </div>
      <div class="server-filters">
        <span class="filter-label">波段型態</span>
        <el-select v-model="chartPattern" :disabled="!!analysisStockId" style="width: 185px">
          <el-option label="不限（只看裸 K）" value="" />
          <el-option label="任何多方型態" value="any" />
          <el-option-group label="底部反轉">
            <el-option v-for="item in bottomPatterns" :key="item.key" :label="item.name" :value="item.key" />
          </el-option-group>
          <el-option-group label="整理突破">
            <el-option v-for="item in continuationPatterns" :key="item.key" :label="item.name" :value="item.key" />
          </el-option-group>
        </el-select>
        <el-select v-model="patternRecent" style="width: 145px">
          <el-option label="近 3 日突破" :value="3" />
          <el-option label="近 10 日突破" :value="10" />
          <el-option label="近 20 日突破" :value="20" />
        </el-select>
        <span class="filter-label">單季 EPS ≥</span>
        <el-input-number v-model="epsMin" :disabled="!!analysisStockId" :step="0.5" :precision="2" controls-position="right" style="width: 125px" />
        <el-select v-model="revenueMonthStreak" :disabled="!!analysisStockId" style="width: 165px">
          <el-option label="月營收不限" :value="0" />
          <el-option v-for="n in [1, 2, 3, 6]" :key="n" :label="`月營收連增 ${n} 月`" :value="n" />
        </el-select>
        <el-select v-model="revenueQuarterStreak" :disabled="!!analysisStockId" style="width: 165px">
          <el-option label="季營收不限" :value="0" />
          <el-option v-for="n in [1, 2, 3, 4]" :key="n" :label="`季營收連增 ${n} 季`" :value="n" />
        </el-select>
        <el-select v-model="grossMarginQuarterStreak" :disabled="!!analysisStockId" style="width: 175px">
          <el-option label="毛利率季增不限" :value="0" />
          <el-option v-for="n in [1, 2, 3, 4]" :key="n" :label="`毛利率連增 ${n} 季`" :value="n" />
        </el-select>
        <el-button type="primary" :loading="loading" @click="load">套用條件</el-button>
      </div>
      <div class="client-filters">
        <el-select v-model="conclusion" clearable placeholder="全部結論" style="width: 130px">
          <el-option v-for="(value, key) in CONCLUSIONS" :key="key" :label="value.label" :value="key" />
        </el-select>
        <el-select v-model="signalState" clearable placeholder="全部狀態" style="width: 140px">
          <el-option v-for="(value, key) in STATES" :key="key" :label="value.label" :value="key" />
        </el-select>
        <el-select v-model="direction" clearable placeholder="多空皆看" style="width: 120px">
          <el-option label="多方" value="bull" />
          <el-option label="空方" value="bear" />
        </el-select>
        <span class="muted">最低分</span>
        <el-input-number v-model="minScore" :min="0" :max="100" :step="5" controls-position="right" style="width: 112px" />
      </div>
    </el-card>

    <div class="summary-grid">
      <el-card shadow="never" class="summary priority" @click="toggleConclusion('priority')">
        <span>優先評估</span><b>{{ summary.priority }}</b>
      </el-card>
      <el-card shadow="never" class="summary waiting" @click="toggleConclusion('waiting')">
        <span>等待確認</span><b>{{ summary.waiting }}</b>
      </el-card>
      <el-card shadow="never" class="summary watch" @click="toggleConclusion('watch')">
        <span>觸發觀察</span><b>{{ summary.watch }}</b>
      </el-card>
      <el-card shadow="never" class="summary skip" @click="toggleConclusion('skip')">
        <span>略過</span><b>{{ summary.skip }}</b>
      </el-card>
      <el-card shadow="never" class="summary meta">
        <span>資料日</span><b>{{ asOf || '—' }}</b>
      </el-card>
    </div>

    <el-alert v-if="analysisStockId" :type="analysisError ? 'error' : items.length ? 'success' : 'warning'" :closable="false" show-icon class="notice">
      <template #title>{{ analysisStockId }} 個股裸 K 分析</template>
      {{ analysisMessage }}
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="notice">
      <template #title>波段型態、基本面是過濾層；裸 K 分數仍只使用還原後 OHLC</template>
      月營收可檢查連續月增，季營收與毛利率可檢查連續季增；財報沒有逐月毛利率資料，因此不做失真的月毛利條件。
    </el-alert>

    <PriceActionTable :items="shown" :loading="loading" height="calc(100vh - 490px)"
                      :empty-text="analysisError || (analysisStockId ? `${analysisStockId} 近 ${lookback} 根 K 棒沒有可評估的裸 K 訊號` : '目前型態、裸 K 與成長條件沒有交集候選')" />
    <div class="method">{{ method }}｜目前顯示 {{ shown.length }}／{{ items.length }} 檔</div>
    <PriceActionGuide />
  </div>
</template>

<style scoped>
.price-action-page { max-width: 1900px; margin: 0 auto; }
.toolbar { margin-bottom: 10px; }
.toolbar-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.subtitle, .muted { color: #909399; }
.filters, .stock-analyzer, .server-filters, .client-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stock-analyzer { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.server-filters { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.client-filters { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.filter-label { color: #606266; font-size: 13px; }
.summary-grid { display: grid; grid-template-columns: repeat(5, minmax(135px, 1fr)); gap: 10px; margin-bottom: 10px; }
.summary { cursor: pointer; }
.summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 12px 16px; }
.summary b { font-size: 24px; }
.summary.priority { border-left: 4px solid #67c23a; }
.summary.waiting { border-left: 4px solid #e6a23c; }
.summary.watch { border-left: 4px solid #409eff; }
.summary.skip { border-left: 4px solid #909399; }
.summary.meta { border-left: 4px solid #606266; cursor: default; }
.summary.meta b { font-size: 18px; }
.notice { margin-bottom: 10px; }
.method { color: #909399; font-size: 12px; margin-top: 7px; text-align: right; }
@media (max-width: 1050px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
