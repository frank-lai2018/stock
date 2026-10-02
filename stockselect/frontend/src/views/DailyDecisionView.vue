<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getDailyDecision } from '../api'
import DecisionHistoryPanel from '../components/DecisionHistoryPanel.vue'

const router = useRouter()
const loading = ref(false)
const result = ref({ items: [], summary: {}, calibration: [], holdings: { items: [], industry_counts: {} } })
const view = ref('selected')
const activeTab = ref('today')

const form = reactive({
  // momentum：趨勢模板＋大盤站上 60 日線＋RS 排序、8% 停損 20 日到期；classic：原始規則（保留對照）
  mode: 'momentum',
  capital: 1000000,
  risk_per_trade_pct: 0.75,
  max_new_positions: 3,
  max_industry_positions: 2,
  max_position_pct: 25,
  lot_size: 1000,
  min_amt: 20000000,
  recent: 3,
  lookback: 5,
  expiry: 5,
  eps_min: null,
  revenue_yoy_min: null,
  gross_margin_chg_min: null,
  limit: 200,
})

const shown = computed(() => {
  const items = result.value.items || []
  if (view.value === 'selected') return items.filter((x) => x.selected)
  if (view.value === 'ready') return items.filter((x) => x.state === 'ready')
  if (view.value === 'momentum') return items.filter((x) => x.state === 'ready' && x.momentum_ok)
  if (view.value === 'consensus') return items.filter((x) => x.consensus_count > 1)
  return items
})

// 依 backend/backtest_decision_center.py（2024-07～2026-09，已扣 0.6% 成本）；規則改了請重跑並更新這段
const BACKTEST_NOTE = '回測 2024-07～2026-09：動能模式每筆平均淨報酬 +1.78%（勝率 38.5%），原始規則 −0.44%（勝率 31.4%）；相對大盤只多 0.35%、統計上不顯著，主要是避開原始規則的負期望值。'
const isMomentum = computed(() => form.mode === 'momentum')
// 大盤濾網：動能模式只在加權指數站上 60 日線時開新倉
const market = computed(() => result.value.market || null)
const marketAlert = computed(() => {
  const m = market.value
  if (!m || m.above == null) return { type: 'info', title: '大盤資料不足，暫不套用大盤濾網' }
  if (m.above) return { type: 'success', title: `加權指數站上 ${m.ma_days} 日線：可開新倉` }
  return { type: 'error', title: `加權指數跌破 ${m.ma_days} 日線：動能模式暫停開新倉` }
})

async function load() {
  loading.value = true
  try {
    const params = { ...form }
    for (const key of ['eps_min', 'revenue_yoy_min', 'gross_margin_chg_min']) {
      if (params[key] == null || params[key] === '') delete params[key]
    }
    result.value = await getDailyDecision(params)
  } catch (e) {
    ElMessage.error('今日決策載入失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const money = (v) => v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US')
const num = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
const pct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`
const strategyType = (key) => key === 'breakout' ? 'danger' : 'primary'
const stateMeta = {
  ready: { label: '可執行', type: 'success' },
  waiting: { label: '等待確認', type: 'warning' },
  watch: { label: '觀察', type: 'info' },
}
const confidenceLabel = { low: '低', medium: '中', high: '高' }

function historicalText(row) {
  const h = row.historical_reference
  if (!h) return '決策校準累積中'
  if (h.source === 'pattern_20d') {
    return `型態先驗 n=${h.n}｜20日正報酬 ${pct(h.positive_rate)}｜超額 ${h.avg_excess_pct >= 0 ? '+' : ''}${pct(h.avg_excess_pct)}`
  }
  return `分數校準 n=${h.n}｜先到目標 ${pct(h.target_hit_rate)}｜期望 ${h.avg_r >= 0 ? '+' : ''}${num(h.avg_r, 2)}R`
}

function calibrationName(key) { return key === 'breakout' ? '型態突破' : '裸 K' }

onMounted(load)
</script>

<template>
  <div class="decision-center">
    <el-tabs v-model="activeTab" class="decision-tabs">
      <el-tab-pane label="今日決策" name="today">
    <el-card shadow="never" class="toolbar">
      <div class="title-row">
        <div>
          <h2>今日決策中心</h2>
          <div v-if="isMomentum" class="muted">突破與裸 K 負責找買點，動能負責篩選與排序：趨勢模板成立、大盤站上 60 日線才開新倉，依 RS 挑最強的。</div>
          <div v-else class="muted">把突破與裸 K 合併成一張可執行清單；同時檢查持股產業重疊與單筆風險。</div>
        </div>
        <div class="title-actions">
          <el-radio-group v-model="form.mode" size="large" @change="load">
            <el-radio-button value="momentum">動能模式</el-radio-button>
            <el-radio-button value="classic">原始規則</el-radio-button>
          </el-radio-group>
          <el-button type="primary" size="large" :loading="loading" @click="load">重新計算</el-button>
        </div>
      </div>

      <div class="filters">
        <label>可投入資金</label>
        <el-input-number v-model="form.capital" :min="10000" :step="100000" controls-position="right" />
        <label>每檔風險</label>
        <el-input-number v-model="form.risk_per_trade_pct" :min="0.1" :max="5" :step="0.1" :precision="2" controls-position="right" style="width: 120px" />
        <span class="unit">%</span>
        <label>最多新倉</label>
        <el-input-number v-model="form.max_new_positions" :min="1" :max="10" controls-position="right" style="width: 100px" />
        <label>同產業上限</label>
        <el-input-number v-model="form.max_industry_positions" :min="1" :max="10" controls-position="right" style="width: 100px" />
        <label>單檔資金上限</label>
        <el-input-number v-model="form.max_position_pct" :min="5" :max="100" :step="5" controls-position="right" style="width: 105px" />
        <span class="unit">%</span>
        <el-select v-model="form.lot_size" style="width: 105px">
          <el-option label="整張優先" :value="1000" />
          <el-option label="可用零股" :value="1" />
        </el-select>
      </div>

      <div class="filters secondary">
        <label>流動性</label>
        <el-select v-model="form.min_amt" style="width: 145px">
          <el-option label="均額 2千萬+" :value="20000000" />
          <el-option label="均額 5千萬+" :value="50000000" />
          <el-option label="均額 1億+" :value="100000000" />
        </el-select>
        <label>單季 EPS ≥</label>
        <el-input-number v-model="form.eps_min" :step="0.5" :precision="2" controls-position="right" style="width: 120px" />
        <label>營收 YoY ≥</label>
        <el-input-number v-model="form.revenue_yoy_min" :step="5" :precision="1" controls-position="right" style="width: 115px" />
        <span class="unit">%</span>
        <label>毛利率季增 ≥</label>
        <el-input-number v-model="form.gross_margin_chg_min" :step="0.5" :precision="1" controls-position="right" style="width: 115px" />
        <span class="unit">百分點</span>
        <label>突破觀察</label>
        <el-select v-model="form.recent" style="width: 105px">
          <el-option label="近 3 日" :value="3" />
          <el-option label="近 2 週" :value="10" />
        </el-select>
      </div>
    </el-card>

    <div class="summary-grid" :class="{ six: isMomentum }">
      <el-card shadow="never" class="summary selected" @click="view = 'selected'">
        <span>本次入選</span><b>{{ result.summary?.selected ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary ready" @click="view = 'ready'">
        <span>可執行候選</span><b>{{ result.summary?.ready ?? 0 }}</b>
      </el-card>
      <el-card v-if="isMomentum" shadow="never" class="summary momentum" @click="view = 'momentum'">
        <span>通過動能篩選</span><b>{{ result.summary?.ready_momentum ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary consensus" @click="view = 'consensus'">
        <span>雙策略共識</span><b>{{ result.summary?.consensus ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary" @click="view = 'all'">
        <span>全部候選</span><b>{{ result.count ?? 0 }}</b>
      </el-card>
      <el-card shadow="never" class="summary meta">
        <span>剩餘可用資金</span><b class="money">{{ money(result.summary?.remaining_capital) }}</b>
      </el-card>
    </div>

    <el-alert v-if="isMomentum" :type="marketAlert.type" :closable="false" show-icon class="notice">
      <template #title>{{ marketAlert.title }}</template>
      <template v-if="market?.close != null">
        加權指數 {{ money(market.close) }}｜{{ market.ma_days }} 日線 {{ money(market.ma) }}
        <template v-if="market.gap_pct != null">（{{ market.gap_pct >= 0 ? '+' : '' }}{{ num(market.gap_pct, 2) }}%）</template>
        ・資料日 {{ market.date }}
      </template>
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="notice">
      <template #title>入選代表通過目前規則與資金限制，不是自動買進指令</template>
      <template v-if="isMomentum">
        動能模式：可執行訊號＋趨勢模板成立（多頭排列、RS ≥ 70）＋大盤站上 60 日線才開新倉，依 RS 評等排序；
        停損為進場價下 8%、不設目標，第 20 個交易日收盤出場。部位以進場到停損的價差反推；同產業上限會先計入交易帳中的未平倉持股。
        <div class="backtest-note">{{ BACKTEST_NOTE }}</div>
      </template>
      <template v-else>
        部位以進場到停損的價差反推；同產業上限會先計入交易帳中的未平倉持股。若分數校準未滿 30 筆，只顯示參考，不影響名次。
      </template>
    </el-alert>

    <div class="list-head">
      <el-radio-group v-model="view" size="small">
        <el-radio-button value="selected">本次入選</el-radio-button>
        <el-radio-button value="ready">全部可執行</el-radio-button>
        <el-radio-button v-if="isMomentum" value="momentum">通過動能篩選</el-radio-button>
        <el-radio-button value="consensus">雙策略共識</el-radio-button>
        <el-radio-button value="all">全部候選</el-radio-button>
      </el-radio-group>
      <span class="muted">資料日 {{ result.as_of || '—' }}｜掃描 {{ result.scanned || 0 }} 檔｜顯示 {{ shown.length }} 檔</span>
    </div>

    <el-table v-loading="loading" :data="shown" stripe border height="calc(100vh - 470px)"
              empty-text="目前條件沒有候選" style="cursor: pointer"
              @row-click="(row) => router.push(`/stock/${row.stock_id}`)">
      <el-table-column label="決策" width="96" fixed>
        <template #default="{ row }">
          <el-tag v-if="row.selected" type="success" effect="dark">本次入選</el-tag>
          <el-tag v-else :type="stateMeta[row.state]?.type" effect="plain">{{ stateMeta[row.state]?.label }}</el-tag>
          <div v-if="row.held" class="held">目前持有</div>
        </template>
      </el-table-column>
      <el-table-column label="股票" width="145" fixed>
        <template #default="{ row }">
          <b>{{ row.stock_id }} {{ row.name }}</b>
          <div class="muted small">{{ row.industry }}</div>
          <div class="small">收 {{ num(row.close, 2) }}・RS {{ num(row.rs_rating, 0) }}</div>
          <el-tag v-if="row.trend_template" size="small" type="danger" effect="plain">趨勢模板</el-tag>
          <span v-else-if="isMomentum" class="muted small">趨勢模板未成立</span>
        </template>
      </el-table-column>
      <el-table-column label="策略共識" min-width="190">
        <template #default="{ row }">
          <div v-for="s in row.strategies" :key="s.key" class="strategy-line">
            <el-tag :type="strategyType(s.key)" size="small" effect="plain">{{ s.label }}</el-tag>
            <b>{{ num(s.score, 1) }}</b>
            <span>{{ s.pattern_name }}</span>
          </div>
          <div v-if="row.consensus_count > 1" class="consensus-text">+{{ row.consensus_bonus }} 分共識</div>
        </template>
      </el-table-column>
      <el-table-column label="決策分" width="100" align="center" sortable prop="decision_score">
        <template #default="{ row }">
          <div class="big-score">{{ num(row.decision_score, 1) }}</div>
          <div v-if="row.calibration_adjustment" class="small" :class="row.calibration_adjustment > 0 ? 'up' : 'down'">
            校準 {{ row.calibration_adjustment > 0 ? '+' : '' }}{{ row.calibration_adjustment }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="歷史依據" min-width="240">
        <template #default="{ row }">
          <div>{{ historicalText(row) }}</div>
          <div v-if="row.calibration" class="muted small">
            95%區間 {{ pct(row.calibration.target_hit_ci_low) }}～{{ pct(row.calibration.target_hit_ci_high) }}・信賴度 {{ confidenceLabel[row.calibration.confidence] }}
          </div>
          <div v-else class="muted small">型態先驗與分數校準分開呈現，避免誤把兩者當成同一件事</div>
        </template>
      </el-table-column>
      <el-table-column label="基本面確認" width="155">
        <template #default="{ row }">
          <div>單季 EPS {{ num(row.eps, 2) }}</div>
          <div>EPS YoY {{ pct(row.eps_yoy) }}</div>
          <div>營收 YoY {{ pct(row.rev_yoy) }}</div>
          <div>毛利季增 {{ pct(row.gross_margin_chg) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="交易計畫" width="170">
        <template #default="{ row }">
          <template v-if="row.position_plan?.valid">
            <div>進場 {{ num(row.position_plan.entry, 2) }}</div>
            <div>停損 <b class="down">{{ num(row.position_plan.stop, 2) }}</b></div>
            <template v-if="row.position_plan.target != null">
              <div>目標 <b class="up">{{ num(row.position_plan.target, 2) }}</b></div>
              <div>R/R {{ num(row.position_plan.rr, 2) }}</div>
            </template>
            <div v-else class="small muted">不設目標・{{ row.position_plan.exit_rule }}</div>
          </template>
          <span v-else class="down">{{ row.position_plan?.reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="建議部位" width="180">
        <template #default="{ row }">
          <template v-if="row.position_plan?.valid">
            <div><b>{{ money(row.position_plan.suggested_shares) }}</b> 股（{{ row.position_plan.order_mode }}）</div>
            <div>約 {{ money(row.position_plan.position_value) }} 元・{{ pct(row.position_plan.capital_pct) }}</div>
            <div class="small muted">停損風險 {{ money(row.position_plan.risk_amount) }} 元・{{ pct(row.position_plan.actual_risk_pct, 2) }}</div>
          </template>
        </template>
      </el-table-column>
      <el-table-column label="取捨理由" min-width="220">
        <template #default="{ row }">
          <span :class="row.selected ? 'up' : 'muted'">{{ row.selection_reason }}</span>
        </template>
      </el-table-column>
    </el-table>

    <el-collapse class="calibration-panel">
      <el-collapse-item name="calibration">
        <template #title><b>分數校準明細與目前持股限制</b></template>
        <el-alert type="info" :closable="false" class="notice">
          每個交易日只保存當日畫面上已觸發的候選；往後 20 個交易日以「先碰停損／先碰目標／到期收盤」結算，已扣 0.6% 來回成本。同根同碰時保守算停損。
          動能模式與原始規則分開累積；動能模式不設目標，只會停損或到期，「先到目標」一欄會是 0。
        </el-alert>
        <el-table :data="result.calibration || []" size="small" border empty-text="尚無已到期的分數校準樣本；系統會從今天開始累積">
          <el-table-column label="策略" width="100"><template #default="{ row }">{{ calibrationName(row.strategy) }}</template></el-table-column>
          <el-table-column prop="score_bucket" label="分數區間" width="100" />
          <el-table-column prop="n" label="樣本" width="80" />
          <el-table-column label="先到目標" width="110"><template #default="{ row }">{{ pct(row.target_hit_rate) }}</template></el-table-column>
          <el-table-column label="95% 區間" width="150"><template #default="{ row }">{{ pct(row.target_hit_ci_low) }}～{{ pct(row.target_hit_ci_high) }}</template></el-table-column>
          <el-table-column label="正期望比例" width="120"><template #default="{ row }">{{ pct(row.positive_rate) }}</template></el-table-column>
          <el-table-column label="平均 R" width="100"><template #default="{ row }">{{ num(row.avg_r, 2) }}R</template></el-table-column>
          <el-table-column label="信賴度" width="90"><template #default="{ row }">{{ confidenceLabel[row.confidence] }}</template></el-table-column>
          <el-table-column label="觀察期間"><template #default="{ row }">{{ row.first_date }}～{{ row.last_date }}</template></el-table-column>
        </el-table>
        <div class="holdings-line">
          <b>交易帳未平倉：</b>
          <span v-if="!result.holdings?.items?.length" class="muted">目前沒有持股，產業上限從零開始。</span>
          <el-tag v-for="(n, industry) in (result.holdings?.industry_counts || {})" :key="industry" size="small" effect="plain">
            {{ industry }} {{ n }} 檔
          </el-tag>
        </div>
      </el-collapse-item>
    </el-collapse>

    <div class="method">{{ result.method }}｜模型 {{ result.settings?.model_version || '—' }}</div>
      </el-tab-pane>
      <el-tab-pane label="決策追蹤／歷史紀錄" name="history" lazy>
        <DecisionHistoryPanel :mode="form.mode" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.decision-center { max-width: 1900px; margin: 0 auto; }
.decision-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.decision-tabs :deep(.el-tabs__item) { font-size: 16px; font-weight: 700; }
.toolbar { margin-bottom: 10px; }
.title-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 24px; }
.muted { color: #909399; }
.small { font-size: 12px; }
.filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.filters.secondary { border-top: 1px solid #ebeef5; padding-top: 12px; }
.filters label { color: #606266; font-size: 13px; font-weight: 600; }
.unit { color: #909399; font-size: 12px; margin-left: -5px; }
.summary-grid { display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 10px; margin-bottom: 10px; }
.summary-grid.six { grid-template-columns: repeat(6, minmax(130px, 1fr)); }
.summary.momentum { border-left: 4px solid #f56c6c; }
.title-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.backtest-note { margin-top: 4px; color: #8a6d3b; }
.summary { cursor: pointer; }
.summary :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: baseline; padding: 12px 16px; }
.summary b { font-size: 24px; }
.summary b.money { font-size: 18px; }
.summary.selected { border-left: 4px solid #67c23a; }
.summary.ready { border-left: 4px solid #409eff; }
.summary.consensus { border-left: 4px solid #e6a23c; }
.summary.meta { border-left: 4px solid #606266; cursor: default; }
.notice { margin-bottom: 10px; }
.list-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 10px 0; flex-wrap: wrap; }
.strategy-line { display: grid; grid-template-columns: 72px 38px 1fr; align-items: center; gap: 5px; margin: 3px 0; }
.consensus-text { color: #e6a23c; font-size: 12px; margin-top: 4px; }
.big-score { font-size: 21px; font-weight: 750; }
.held { color: #e6a23c; font-size: 11px; margin-top: 4px; }
.up { color: #EA4C4C; }
.down { color: #3F9E5A; }
.calibration-panel { margin-top: 12px; }
.holdings-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.method { color: #909399; font-size: 12px; margin-top: 8px; text-align: right; }
@media (max-width: 1100px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
