<script>
// 裸 K 決策表格：裸K決策頁與自選股「裸K決策」檢視共用。
// decision 為 null 的列（自選股裡近期沒有訊號）只顯示股票與 reason，其他欄灰色「—」。
export const CONCLUSIONS = {
  priority: { label: '優先評估', type: 'success' },
  waiting: { label: '等待確認', type: 'warning' },
  watch: { label: '觸發觀察', type: 'primary' },
  skip: { label: '略過', type: 'info' },
}

export const STATES = {
  waiting: { label: '等待突破', type: 'warning' },
  triggered: { label: '已觸發', type: 'success' },
  invalid: { label: '觸發前失效', type: 'danger' },
  failed: { label: '觸發後停損', type: 'danger' },
  expired: { label: '訊號過期', type: 'info' },
}
</script>

<script setup>
import WatchlistAddButton from './WatchlistAddButton.vue'

defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  height: { type: String, default: undefined },         // 不給＝跟著內容長高（自選股）；決策頁固定高度
  emptyText: { type: String, default: '目前沒有候選' },
  noChartText: { type: String, default: '未限制' },     // 沒有波段型態時顯示
  actionLabel: { type: String, default: '追蹤' },       // 最後一欄；內容用 #action slot 換掉（預設★加入自選）
})

const num = (v, digits = 1) => v == null ? '—' : Number(v).toFixed(digits)
const pct = (v) => v == null ? '—' : `${Number(v).toFixed(1)}%`
const money = (v) => v == null ? '—' : Number(v).toLocaleString('zh-TW', { maximumFractionDigits: 0 })
const conclusionOf = (row) => CONCLUSIONS[row.decision?.conclusion] || CONCLUSIONS.skip
const stateOf = (row) => STATES[row.decision?.status] || { label: row.decision?.status || '—', type: 'info' }
const directionName = (v) => v === 'bull' ? '多方' : v === 'bear' ? '空方' : '中性'
const scoreOf = (row) => row.decision?.score ?? -1
</script>

<template>
  <el-table :data="items" v-loading="loading" stripe border :height="height" row-key="stock_id">
    <el-table-column type="index" label="#" width="48" fixed />
    <el-table-column label="決策" width="112" fixed>
      <template #default="{ row }">
        <el-tag v-if="row.decision" :type="conclusionOf(row).type" effect="dark">{{ conclusionOf(row).label }}</el-tag>
        <el-tag v-else type="info" effect="plain">無訊號</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="分數" width="82" sortable :sort-method="(a, b) => scoreOf(a) - scoreOf(b)" fixed>
      <template #default="{ row }">
        <span v-if="row.decision" class="score">{{ row.decision.score }}</span>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="股票" width="150" fixed>
      <template #default="{ row }">
        <router-link :to="`/stock/${row.stock_id}`" class="stock-link">{{ row.stock_id }} {{ row.name }}</router-link>
        <div class="muted small">{{ row.industry || '—' }}</div>
      </template>
    </el-table-column>
    <el-table-column label="訊號" min-width="175">
      <template #default="{ row }">
        <template v-if="row.decision">
          <div class="signal-title" :class="row.decision.direction">{{ directionName(row.decision.direction) }}・{{ row.decision.pattern_name }}</div>
          <el-tag :type="stateOf(row).type" size="small" effect="plain">{{ stateOf(row).label }}</el-tag>
          <span class="small muted"> {{ row.decision.signal_date }}（{{ row.decision.age }} 根前）</span>
          <div v-if="row.decision.trigger_date" class="small">觸發日 {{ row.decision.trigger_date }}</div>
        </template>
        <span v-else class="muted small">{{ row.reason || '近期沒有裸 K 訊號' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="波段型態" width="155">
      <template #default="{ row }">
        <template v-if="row.chart_pattern_name">
          <el-tag type="danger" effect="plain">{{ row.chart_pattern_name }}</el-tag>
          <div class="small muted chart-meta">突破日 {{ row.chart_breakout?.breakout_date || '—' }}</div>
          <div class="small muted">頸線 {{ num(row.chart_breakout?.neckline, 2) }}</div>
        </template>
        <span v-else class="muted">{{ row.decision ? noChartText : '—' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="結構與位置" min-width="190">
      <template #default="{ row }">
        <template v-if="row.decision">
          <div><b>{{ row.decision.structure_name }}</b><span class="muted">・區間 {{ pct(row.decision.range_pos) }}</span></div>
          <div class="location-tags">
            <el-tag v-for="tag in row.decision.locations" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
          </div>
          <div class="small muted">支撐 {{ num(row.decision.support, 2) }}／壓力 {{ num(row.decision.resistance, 2) }}</div>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="五層評分" min-width="265">
      <template #default="{ row }">
        <div v-if="row.decision" class="parts">
          <span>構 {{ row.decision.parts.structure }}/30</span>
          <span>位 {{ row.decision.parts.location }}/25</span>
          <span>K {{ row.decision.parts.pattern }}/20</span>
          <span>確 {{ row.decision.parts.confirmation }}/15</span>
          <span>險 {{ row.decision.parts.risk }}/10</span>
        </div>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="交易計畫" width="200">
      <template #default="{ row }">
        <template v-if="row.decision">
          <div>觸發 {{ num(row.decision.trigger, 2) }}・進場 {{ num(row.decision.entry, 2) }}</div>
          <div>停損 {{ num(row.decision.stop, 2) }}（<b :class="{ danger: row.decision.risk_pct > 8 }">{{ pct(row.decision.risk_pct) }}</b>）</div>
          <div>目標 {{ num(row.decision.target, 2) }}・R/R <b>{{ num(row.decision.rr, 2) }}</b></div>
          <div class="small muted">{{ row.decision.target_source }}・現價 {{ num(row.decision.current, 2) }}</div>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="成長過濾" width="185">
      <template #default="{ row }">
        <template v-if="row.fundamental_trend">
          <div>單季 EPS <b>{{ num(row.eps, 2) }}</b></div>
          <div>月營收連增 <b>{{ row.fundamental_trend?.revenue_month_streak ?? 0 }}</b> 月</div>
          <div>季營收連增 <b>{{ row.fundamental_trend?.revenue_quarter_streak ?? 0 }}</b> 季</div>
          <div>毛利率 {{ pct(row.gross_margin) }}・連增 <b>{{ row.fundamental_trend?.gross_margin_quarter_streak ?? 0 }}</b> 季</div>
          <div class="small muted">營收月 {{ row.fundamental_trend?.revenue_month || '—' }}</div>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="檢查結果" min-width="280">
      <template #default="{ row }">
        <template v-if="row.decision">
          <div v-if="!row.decision.blockers.length" class="pass">可依觸發條件執行</div>
          <div v-for="item in row.decision.blockers" :key="item" class="blocker">• {{ item }}</div>
          <div v-for="item in row.decision.notes" :key="item" class="note">• {{ item }}</div>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="流動性" width="115">
      <template #default="{ row }"><span class="muted">均額</span><br>{{ money(row.amt20) }}</template>
    </el-table-column>
    <el-table-column :label="actionLabel" width="75" fixed="right">
      <template #default="{ row }"><slot name="action" :row="row"><WatchlistAddButton :row="row" /></slot></template>
    </el-table-column>
    <template #empty>
      <el-empty :description="emptyText" />
    </template>
  </el-table>
</template>

<style scoped>
.muted { color: #909399; }
.small { font-size: 12px; }
.score { font-size: 21px; font-weight: 750; }
.stock-link { color: #337ecc; font-weight: 650; text-decoration: none; }
.signal-title { font-weight: 650; margin-bottom: 5px; }
.signal-title.bull { color: #f56c6c; }
.signal-title.bear { color: #529b2e; }
.location-tags { display: flex; gap: 4px; flex-wrap: wrap; margin: 4px 0; }
.chart-meta { margin-top: 5px; }
.parts { display: flex; gap: 4px; flex-wrap: wrap; }
.parts span { background: #f2f6fc; border-radius: 4px; padding: 3px 6px; font-size: 12px; }
.danger, .blocker { color: #f56c6c; }
.pass { color: #529b2e; font-weight: 600; }
.note { color: #a77700; }
</style>
