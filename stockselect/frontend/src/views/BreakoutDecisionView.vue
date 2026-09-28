<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getBreakoutPatterns, getBreakoutRanking } from '../api'
import BreakoutDecisionTable, { STATUS } from '../components/BreakoutDecisionTable.vue'
import BreakoutDecisionGuide from '../components/BreakoutDecisionGuide.vue'

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
// 跟裸K決策頁一樣的過濾（按「套用條件」才重掃）：型態只找指定的一種；成長條件只篩選、不改分數
const chartPattern = ref('')               // 空＝全部多方型態（依優先序取第一個命中）
const bottomPatterns = ref([])
const continuationPatterns = ref([])
const epsMin = ref(null)
const revenueMonthStreak = ref(0)
const revenueQuarterStreak = ref(0)
const grossMarginQuarterStreak = ref(0)

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
      pattern: chartPattern.value || undefined,
      eps_min: epsMin.value == null || epsMin.value === '' ? undefined : epsMin.value,
      revenue_month_streak: revenueMonthStreak.value,
      revenue_quarter_streak: revenueQuarterStreak.value,
      gross_margin_quarter_streak: grossMarginQuarterStreak.value,
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

onMounted(async () => {
  try {
    const [bottom, continuation] = await Promise.all([
      getBreakoutPatterns('bottom'), getBreakoutPatterns('continuation'),
    ])
    // 只列可能多方突破的型態（下降三角只判跌破，選了一定沒結果）
    bottomPatterns.value = (bottom || []).filter((item) => item.bull !== false)
    continuationPatterns.value = (continuation || []).filter((item) => item.bull !== false)
  } catch (e) {
    ElMessage.warning('波段型態清單讀取失敗，仍可掃描全部多方型態')
  }
  await load()
})
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
        <span v-if="analysisStockId" class="muted">個股模式會忽略證券類別、流動性、母體、型態與成長條件</span>
      </div>
      <div class="server-filters">
        <span class="filter-label">波段型態</span>
        <el-select v-model="chartPattern" :disabled="!!analysisStockId" clearable placeholder="全部多方型態" style="width: 185px">
          <el-option-group label="底部反轉">
            <el-option v-for="item in bottomPatterns" :key="item.key" :label="item.name" :value="item.key" />
          </el-option-group>
          <el-option-group label="整理突破">
            <el-option v-for="item in continuationPatterns" :key="item.key" :label="item.name" :value="item.key" />
          </el-option-group>
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
      參考停損統一用「頸線下 1 ATR」以便比較；實際下單前仍應看圖確認結構、隔日跳空與產業持倉。型態與成長條件只負責篩選，不會改變分數。
    </el-alert>

    <BreakoutDecisionTable :items="shown" :loading="loading" height="calc(100vh - 432px)"
                           :empty-text="analysisError || (analysisStockId ? `${analysisStockId} 近 ${recent} 日沒有已確認的多方型態突破` : '目前條件沒有突破候選')" />
    <div class="method">{{ method }}｜目前顯示 {{ shown.length }} 檔</div>
    <BreakoutDecisionGuide />
  </div>
</template>

<style scoped>
.decision-page { max-width: 1900px; margin: 0 auto; }
.toolbar { margin-bottom: 10px; }
.toolbar-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.subtitle, .muted { color: #909399; }
.filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stock-analyzer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.server-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.filter-label { color: #606266; font-size: 13px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-bottom: 10px; }
.summary { cursor: pointer; }
.summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 13px 18px; }
.summary b { font-size: 25px; }
.summary.priority { border-left: 4px solid #67c23a; }
.summary.watch { border-left: 4px solid #e6a23c; }
.summary.skip { border-left: 4px solid #909399; }
.summary.meta { border-left: 4px solid #409eff; cursor: default; }
.notice { margin-bottom: 10px; }
.method { color: #909399; font-size: 12px; margin-top: 7px; text-align: right; }
@media (max-width: 900px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
