<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getPriceActionDecisions } from '../api'
import WatchlistAddButton from '../components/WatchlistAddButton.vue'

const loading = ref(false)
const items = ref([])
const asOf = ref('')
const method = ref('')
const secType = ref('stock')
const minAmt = ref(20000000)
const lookback = ref(5)
const expiry = ref(5)
const minScore = ref(0)
const conclusion = ref('')
const signalState = ref('')
const direction = ref('')

const CONCLUSIONS = {
  priority: { label: '優先評估', type: 'success' },
  waiting: { label: '等待確認', type: 'warning' },
  watch: { label: '觸發觀察', type: 'primary' },
  skip: { label: '略過', type: 'info' },
}

const STATES = {
  waiting: { label: '等待突破', type: 'warning' },
  triggered: { label: '已觸發', type: 'success' },
  invalid: { label: '觸發前失效', type: 'danger' },
  failed: { label: '觸發後停損', type: 'danger' },
  expired: { label: '訊號過期', type: 'info' },
}

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

async function load() {
  loading.value = true
  try {
    const data = await getPriceActionDecisions({
      security_type: secType.value,
      min_amt: minAmt.value,
      lookback: lookback.value,
      expiry: expiry.value,
      limit: 300,
    })
    items.value = data.items || []
    asOf.value = data.as_of || ''
    method.value = data.method || ''
  } catch (e) {
    ElMessage.error('裸 K 決策載入失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function toggleConclusion(key) {
  conclusion.value = conclusion.value === key ? '' : key
}

onMounted(load)

const num = (v, digits = 1) => v == null ? '—' : Number(v).toFixed(digits)
const pct = (v) => v == null ? '—' : `${Number(v).toFixed(1)}%`
const money = (v) => v == null ? '—' : Number(v).toLocaleString('zh-TW', { maximumFractionDigits: 0 })
const conclusionOf = (row) => CONCLUSIONS[row.decision?.conclusion] || CONCLUSIONS.skip
const stateOf = (row) => STATES[row.decision?.status] || { label: row.decision?.status || '—', type: 'info' }
const directionName = (v) => v === 'bull' ? '多方' : v === 'bear' ? '空方' : '中性'
</script>

<template>
  <div class="price-action-page">
    <el-card shadow="never" class="toolbar">
      <div class="toolbar-row">
        <div>
          <h2>裸 K 型態決策</h2>
          <div class="subtitle">從結構、位置、K 棒品質、確認與風險五層判斷；訊號形成不等於立即買進。</div>
        </div>
        <div class="filters">
          <el-select v-model="secType" style="width: 112px" @change="load">
            <el-option label="只看個股" value="stock" />
            <el-option label="只看 ETF" value="etf" />
          </el-select>
          <el-select v-model="minAmt" style="width: 145px" @change="load">
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

    <el-alert type="info" :closable="false" show-icon class="notice">
      <template #title>決策分數只使用還原後 OHLC；均額只用來排除流動性太低的標的，不參與評分</template>
      「等待突破」須等後續 K 棒穿越訊號高／低點；若先碰訊號反側，會直接判定失效，避免偷看未來資料。
    </el-alert>

    <el-table :data="shown" v-loading="loading" stripe border height="calc(100vh - 360px)" row-key="stock_id">
      <el-table-column type="index" label="#" width="48" fixed />
      <el-table-column label="決策" width="112" fixed>
        <template #default="{ row }">
          <el-tag :type="conclusionOf(row).type" effect="dark">{{ conclusionOf(row).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分數" width="82" sortable :sort-method="(a, b) => a.decision.score - b.decision.score" fixed>
        <template #default="{ row }"><span class="score">{{ row.decision.score }}</span></template>
      </el-table-column>
      <el-table-column label="股票" width="150" fixed>
        <template #default="{ row }">
          <router-link :to="`/stock/${row.stock_id}`" class="stock-link">{{ row.stock_id }} {{ row.name }}</router-link>
          <div class="muted small">{{ row.industry || '—' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="訊號" min-width="175">
        <template #default="{ row }">
          <div class="signal-title" :class="row.decision.direction">{{ directionName(row.decision.direction) }}・{{ row.decision.pattern_name }}</div>
          <el-tag :type="stateOf(row).type" size="small" effect="plain">{{ stateOf(row).label }}</el-tag>
          <span class="small muted"> {{ row.decision.signal_date }}（{{ row.decision.age }} 根前）</span>
          <div v-if="row.decision.trigger_date" class="small">觸發日 {{ row.decision.trigger_date }}</div>
        </template>
      </el-table-column>
      <el-table-column label="結構與位置" min-width="190">
        <template #default="{ row }">
          <div><b>{{ row.decision.structure_name }}</b><span class="muted">・區間 {{ pct(row.decision.range_pos) }}</span></div>
          <div class="location-tags">
            <el-tag v-for="tag in row.decision.locations" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
          </div>
          <div class="small muted">支撐 {{ num(row.decision.support, 2) }}／壓力 {{ num(row.decision.resistance, 2) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="五層評分" min-width="265">
        <template #default="{ row }">
          <div class="parts">
            <span>構 {{ row.decision.parts.structure }}/30</span>
            <span>位 {{ row.decision.parts.location }}/25</span>
            <span>K {{ row.decision.parts.pattern }}/20</span>
            <span>確 {{ row.decision.parts.confirmation }}/15</span>
            <span>險 {{ row.decision.parts.risk }}/10</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="交易計畫" width="200">
        <template #default="{ row }">
          <div>觸發 {{ num(row.decision.trigger, 2) }}・進場 {{ num(row.decision.entry, 2) }}</div>
          <div>停損 {{ num(row.decision.stop, 2) }}（<b :class="{ danger: row.decision.risk_pct > 8 }">{{ pct(row.decision.risk_pct) }}</b>）</div>
          <div>目標 {{ num(row.decision.target, 2) }}・R/R <b>{{ num(row.decision.rr, 2) }}</b></div>
          <div class="small muted">{{ row.decision.target_source }}・現價 {{ num(row.decision.current, 2) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="檢查結果" min-width="280">
        <template #default="{ row }">
          <div v-if="!row.decision.blockers.length" class="pass">可依觸發條件執行</div>
          <div v-for="item in row.decision.blockers" :key="item" class="blocker">• {{ item }}</div>
          <div v-for="item in row.decision.notes" :key="item" class="note">• {{ item }}</div>
        </template>
      </el-table-column>
      <el-table-column label="流動性" width="115">
        <template #default="{ row }"><span class="muted">均額</span><br>{{ money(row.amt20) }}</template>
      </el-table-column>
      <el-table-column label="追蹤" width="75" fixed="right">
        <template #default="{ row }"><WatchlistAddButton :row="row" /></template>
      </el-table-column>
      <template #empty>
        <el-empty description="目前條件沒有裸 K 候選" />
      </template>
    </el-table>
    <div class="method">{{ method }}｜目前顯示 {{ shown.length }}／{{ items.length }} 檔</div>
  </div>
</template>

<style scoped>
.price-action-page { max-width: 1900px; margin: 0 auto; }
.toolbar { margin-bottom: 10px; }
.toolbar-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.subtitle, .muted { color: #909399; }
.small { font-size: 12px; }
.filters, .client-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.client-filters { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeef5; }
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
.score { font-size: 21px; font-weight: 750; }
.stock-link { color: #337ecc; font-weight: 650; text-decoration: none; }
.signal-title { font-weight: 650; margin-bottom: 5px; }
.signal-title.bull { color: #f56c6c; }
.signal-title.bear { color: #529b2e; }
.location-tags { display: flex; gap: 4px; flex-wrap: wrap; margin: 4px 0; }
.parts { display: flex; gap: 4px; flex-wrap: wrap; }
.parts span { background: #f2f6fc; border-radius: 4px; padding: 3px 6px; font-size: 12px; }
.danger, .blocker { color: #f56c6c; }
.pass { color: #529b2e; font-weight: 600; }
.note { color: #a77700; }
.method { color: #909399; font-size: 12px; margin-top: 7px; text-align: right; }
@media (max-width: 1050px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
