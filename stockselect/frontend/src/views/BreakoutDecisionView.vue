<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getBreakoutRanking } from '../api'
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

    <BreakoutDecisionTable :items="shown" :loading="loading" height="calc(100vh - 375px)"
                           :empty-text="analysisError || (analysisStockId ? `${analysisStockId} 近 ${recent} 日沒有已確認的多方型態突破` : '目前條件沒有突破候選')" />
    <div class="method">{{ method }}｜目前顯示 {{ shown.length }} 檔</div>
    <BreakoutDecisionGuide />
  </div>
</template>

<style scoped>
.decision-page { max-width: 1800px; margin: 0 auto; }
.toolbar { margin-bottom: 10px; }
.toolbar-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.subtitle, .muted { color: #909399; }
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
.method { color: #909399; font-size: 12px; margin-top: 7px; text-align: right; }
@media (max-width: 900px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
