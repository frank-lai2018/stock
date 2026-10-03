<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getDailyDecisionHistory } from '../api'

// 模式與成交版本分開累積；條件切換只顯示最後一次請求的結果。
const props = defineProps({
  mode: { type: String, default: 'momentum' },
  gate: { type: String, default: 'trend_template' },
})
const router = useRouter()
const loading = ref(false)
const result = ref({ items: [], summary: {}, dates: [] })
const dateRange = ref([])
const form = reactive({
  stock_id: '',
  strategy: '',
  outcome_status: '',
  selected_only: false,
  legacy: false,
  limit: 500,
})

let sequence = 0
async function load() {
  const id = ++sequence
  loading.value = true
  try {
    const params = { ...form, mode: props.mode, gate: props.gate }
    for (const key of ['stock_id', 'strategy', 'outcome_status']) {
      if (!params[key]) delete params[key]
    }
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const data = await getDailyDecisionHistory(params)
    if (id === sequence) result.value = data
  } catch (e) {
    if (id === sequence) ElMessage.error('決策歷史載入失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    if (id === sequence) loading.value = false
  }
}

function reset() {
  form.stock_id = ''
  form.strategy = ''
  form.outcome_status = ''
  form.selected_only = false
  dateRange.value = []
  load()
}

const num = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
const money = (v) => v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US')
const pct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`
const strategyName = (key) => ({ breakout: '型態突破', price_action: '裸 K', consolidation: '整理突破' }[key] || key)
const decisionName = (status) => ({ priority: '優先', watch: '觀察', waiting: '等待' }[status] || status || '—')
const decisionType = (status) => ({ priority: 'success', watch: 'info', waiting: 'warning' }[status] || 'info')
const outcomeMeta = {
  pending: { label: '觀察中', type: 'warning' },
  win: { label: '先達目標', type: 'success' },
  loss: { label: '先到停損', type: 'danger' },
  timeout: { label: '到期出場', type: 'info' },
  trend: { label: '趨勢出場', type: 'info' },
  skipped: { label: '未成交', type: 'info' },
}

const shownR = (row) => row.outcome_status === 'pending' ? row.current_r : row.outcome_r
const rClass = (row) => Number(shownR(row)) >= 0 ? 'up' : 'down'
const GATE_NAMES = { trend_template: '趨勢模板', breakout: '型態突破', consolidation: '整理突破' }
const modeName = (m, g) => m === 'classic' ? '原始規則' : `${m === 'trend_hold' ? '趨勢持有' : '動能模式'}・${GATE_NAMES[g] || GATE_NAMES.trend_template}`

watch(() => [props.mode, props.gate], () => { form.legacy = false; load() })
onMounted(load)
</script>

<template>
  <div class="history-panel">
    <el-card shadow="never" class="history-toolbar">
      <div class="history-title">
        <div>
          <h2>決策追蹤／歷史紀錄 <el-tag size="small" effect="plain">{{ modeName(props.mode, props.gate) }}</el-tag></h2>
          <div class="muted">保存盤後參考計畫與隔日模擬成交；20 日與趨勢持有版本分開累積，舊版紀錄獨立保留。</div>
        </div>
        <el-button type="primary" :loading="loading" @click="load">更新追蹤結果</el-button>
      </div>
      <div class="history-filters">
        <el-input v-model="form.stock_id" placeholder="股票代號" clearable style="width: 125px" @keyup.enter="load" />
        <el-select v-model="form.strategy" placeholder="全部策略" clearable style="width: 130px">
          <el-option label="型態突破" value="breakout" />
          <el-option label="裸 K" value="price_action" />
          <el-option label="整理突破" value="consolidation" />
        </el-select>
        <el-select v-model="form.outcome_status" placeholder="全部結果" clearable style="width: 130px">
          <el-option label="觀察中" value="pending" />
          <el-option label="先達目標" value="win" />
          <el-option label="先到停損" value="loss" />
          <el-option label="到期出場" value="timeout" />
          <el-option label="趨勢出場" value="trend" />
          <el-option label="未成交" value="skipped" />
        </el-select>
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
                        start-placeholder="開始觀察日" end-placeholder="結束觀察日" style="width: 250px" />
        <el-checkbox v-model="form.selected_only">只看當時入選</el-checkbox>
        <el-checkbox v-model="form.legacy" :disabled="props.mode === 'trend_hold' || props.gate === 'consolidation'" @change="load">查看舊版封存</el-checkbox>
        <el-button @click="load">查詢</el-button>
        <el-button text @click="reset">清除</el-button>
        <span class="muted">最新行情 {{ result.as_of || '—' }}｜符合 {{ result.count || 0 }} 筆</span>
      </div>
    </el-card>

    <div class="history-summary">
      <el-card shadow="never"><span>觀察中</span><b>{{ result.summary?.pending || 0 }}</b></el-card>
      <el-card shadow="never" class="win"><span>先達目標</span><b>{{ result.summary?.win || 0 }}</b></el-card>
      <el-card shadow="never" class="loss"><span>先到停損</span><b>{{ result.summary?.loss || 0 }}</b></el-card>
      <el-card shadow="never"><span>到期出場</span><b>{{ result.summary?.timeout || 0 }}</b></el-card>
      <el-card shadow="never"><span>趨勢出場</span><b>{{ result.summary?.trend || 0 }}</b></el-card>
      <el-card shadow="never"><span>未成交</span><b>{{ result.summary?.skipped || 0 }}</b></el-card>
      <el-card shadow="never"><span>已結算平均</span><b>{{ num(result.summary?.avg_r, 2) }}R</b></el-card>
    </div>

    <el-alert type="info" :closable="false" class="history-notice">
      <template v-if="form.legacy">舊版沿用當時的成交假設與已保存結果，待結算紀錄已封存，與新版績效分開。</template>
      <template v-else>訊號隔日開盤模擬成交；跳空依可交易價格，鎖跌停延後賣出。扣 0.6% 成本與每邊 0.1% 滑價，報酬採股利再投資口徑。每天保留第一份入選設定，之後調整畫面不會覆蓋歷史；夜間預設淨值 100 萬。這裡是訊號追蹤，每檔可有多次觀察；連續資金與持股限制的績效請看策略研究。</template>
    </el-alert>

    <el-table v-loading="loading" :data="result.items || []" stripe border
              height="calc(100vh - 430px)" empty-text="目前沒有符合條件的歷史紀錄"
              style="cursor: pointer" @row-click="(row) => router.push(`/stock/${row.stock_id}`)">
      <el-table-column label="觀察日" width="110" fixed>
        <template #default="{ row }">{{ row.observed_date }}</template>
      </el-table-column>
      <el-table-column label="股票" width="145" fixed>
        <template #default="{ row }">
          <b>{{ row.stock_id }} {{ row.name }}</b>
          <div class="muted small">{{ row.industry }}</div>
          <el-tag v-if="row.is_selected === true" type="success" size="small" effect="dark">當時入選</el-tag>
          <el-tag v-else-if="row.is_selected === false" type="info" size="small" effect="plain">未入選</el-tag>
          <span v-else class="muted small">舊紀錄</span>
        </template>
      </el-table-column>
      <el-table-column label="原始決策" min-width="190">
        <template #default="{ row }">
          <div>
            <el-tag size="small" effect="plain">{{ strategyName(row.strategy) }}</el-tag>
            <el-tag :type="decisionType(row.decision_status)" size="small" class="tag-gap">{{ decisionName(row.decision_status) }}</el-tag>
          </div>
          <div><b>{{ row.pattern_name || row.pattern || '—' }}</b></div>
          <div class="small">策略分 {{ num(row.score, 1) }}・決策分 {{ num(row.decision_score, 1) }}</div>
          <div v-if="row.consensus_count > 1" class="small consensus">雙策略共識</div>
        </template>
      </el-table-column>
      <el-table-column label="當時交易計畫" width="170">
        <template #default="{ row }">
          <div>參考進場 {{ num(row.entry, 2) }}</div>
          <div>停損 <b class="down">{{ num(row.stop, 2) }}</b></div>
          <div v-if="row.target != null">目標 <b class="up">{{ num(row.target, 2) }}</b></div>
          <div v-else class="small muted">不設目標・最多 {{ row.horizon }} 日</div>
          <div v-if="row.actual_entry != null">模擬成交 {{ num(row.actual_entry, 2) }}</div>
          <div v-if="row.entry_date" class="small muted">進場日 {{ row.entry_date }}</div>
          <div v-if="row.suggested_shares != null" class="small muted">
            {{ money(row.suggested_shares) }} 股・約 {{ money(row.position_value) }} 元
          </div>
        </template>
      </el-table-column>
      <el-table-column label="追蹤結果" width="155" align="center">
        <template #default="{ row }">
          <el-tag :type="outcomeMeta[row.outcome_status]?.type" effect="dark">
            {{ outcomeMeta[row.outcome_status]?.label || row.outcome_status }}
          </el-tag>
          <div v-if="row.outcome_date" class="small">{{ row.outcome_date }}・第 {{ row.outcome_days }} 日</div>
          <div v-else class="small">{{ row.progress_days }}/{{ row.horizon }} 個交易日</div>
          <div v-if="row.execution_reason" class="small muted">{{ row.execution_reason }}</div>
        </template>
      </el-table-column>
      <el-table-column label="報酬進度" width="150" align="center">
        <template #default="{ row }">
          <div class="result-r" :class="rClass(row)">{{ num(shownR(row), 2) }}R</div>
          <div class="small muted">最新 {{ num(row.latest_close, 2) }}</div>
          <div class="small muted">行情日 {{ row.latest_date || '—' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="當時取捨理由" min-width="240">
        <template #default="{ row }">
          <span v-if="row.selection_reason">{{ row.selection_reason }}</span>
          <span v-else class="muted">舊版紀錄未保存取捨理由</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.history-toolbar { margin-bottom: 10px; }
.history-title { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
.history-title h2 { margin: 0 0 4px; font-size: 22px; }
.history-filters { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-top: 14px; }
.history-summary { display: grid; grid-template-columns: repeat(7, minmax(100px, 1fr)); gap: 10px; margin-bottom: 10px; }
.history-summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 12px 16px; }
.history-summary b { font-size: 22px; }
.history-summary .win { border-left: 4px solid #67c23a; }
.history-summary .loss { border-left: 4px solid #f56c6c; }
.history-notice { margin-bottom: 10px; }
.muted { color: #909399; }
.small { font-size: 12px; }
.tag-gap { margin-left: 5px; }
.consensus { color: #e6a23c; font-weight: 700; }
.result-r { font-size: 20px; font-weight: 750; }
.up { color: #EA4C4C; }
.down { color: #3F9E5A; }
@media (max-width: 1100px) { .history-summary { grid-template-columns: repeat(2, 1fr); } }
</style>
