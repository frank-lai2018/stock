<script>
// 突破決策表格：突破決策頁與自選股「突破決策」檢視共用。
// decision 為 null 的列（自選股裡近期沒有已確認的多方突破）在「型態」欄顯示 reason，基本面照常顯示。
export const STATUS = {
  priority: { label: '優先評估', type: 'success' },
  watch: { label: '等待／觀察', type: 'warning' },
  skip: { label: '略過', type: 'info' },
}
</script>

<script setup>
import WatchlistAddButton from './WatchlistAddButton.vue'

defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  height: { type: String, default: undefined },         // 不給＝跟著內容長高（自選股）；決策頁固定高度
  emptyText: { type: String, default: '目前條件沒有突破候選' },
  actionLabel: { type: String, default: '追蹤' },       // 最後一欄；內容用 #action slot 換掉（預設★加入自選）
})

const pct = (v) => v == null ? '—' : `${Number(v).toFixed(1)}%`
const num = (v, n = 1) => v == null ? '—' : Number(v).toFixed(n)
const money = (v) => v == null ? '—' : Number(v).toLocaleString('zh-TW', { maximumFractionDigits: 0 })
const statusOf = (row) => STATUS[row.decision?.status] || STATUS.skip
const scoreOf = (row) => row.decision?.score ?? -1
</script>

<template>
  <el-table :data="items" v-loading="loading" stripe border :height="height" row-key="stock_id">
    <el-table-column type="index" label="#" width="48" fixed />
    <el-table-column label="結論" width="105" fixed>
      <template #default="{ row }">
        <el-tag v-if="row.decision" :type="statusOf(row).type" effect="dark">{{ statusOf(row).label }}</el-tag>
        <el-tag v-else type="info" effect="plain">無突破</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="總分" width="92" sortable :sort-method="(a, b) => scoreOf(a) - scoreOf(b)" fixed>
      <template #default="{ row }">
        <span v-if="row.decision" class="score">{{ row.decision.score }}</span>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="股票" width="145" fixed>
      <template #default="{ row }">
        <router-link :to="`/stock/${row.stock_id}`" class="stock-link">{{ row.stock_id }} {{ row.name }}</router-link>
        <div class="muted small">{{ row.industry || '—' }}</div>
      </template>
    </el-table-column>
    <el-table-column label="型態" min-width="120">
      <template #default="{ row }">
        <template v-if="row.decision">{{ row.pattern_name }}</template>
        <span v-else class="muted small">{{ row.reason || '近期沒有已確認的多方型態突破' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="五面向" min-width="255">
      <template #default="{ row }">
        <div v-if="row.decision" class="parts">
          <span>趨 {{ row.decision.parts.trend }}/30</span>
          <span>破 {{ row.decision.parts.breakout }}/25</span>
          <span>盈 {{ row.decision.parts.fundamental }}/20</span>
          <span>籌 {{ row.decision.parts.quality }}/10</span>
          <span>險 {{ row.decision.parts.risk }}/15</span>
        </div>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="突破品質" width="145">
      <template #default="{ row }">
        <template v-if="row.decision">
          <div>量比 <b>{{ num(row.breakout?.vol_ratio, 2) }}</b></div>
          <div>離頸線 <b :class="{ danger: row.decision.extension_pct > 5 }">{{ pct(row.decision.extension_pct) }}</b></div>
        </template>
        <div>RS <b>{{ row.rs_rating ?? '—' }}</b></div>
      </template>
    </el-table-column>
    <el-table-column label="風險計畫" width="170">
      <template #default="{ row }">
        <template v-if="row.decision">
          <div>參考進場 {{ num(row.decision.entry, 2) }}</div>
          <div>停損 {{ num(row.decision.stop, 2) }}（{{ pct(row.decision.risk_pct) }}）</div>
          <div>目標 {{ num(row.decision.target, 2) }}・R/R <b>{{ num(row.decision.rr, 2) }}</b></div>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="型態20日回測" width="150">
      <template #default="{ row }">
        <template v-if="row.decision?.backtest_20d">
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
        <template v-if="row.decision">
          <div v-if="!row.decision.blockers.length" class="pass">硬條件全數通過</div>
          <div v-for="x in row.decision.blockers" :key="x" class="blocker">• {{ x }}</div>
          <div v-for="x in row.decision.notes" :key="x" class="note">• {{ x }}</div>
        </template>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column :label="actionLabel" width="82" fixed="right">
      <template #default="{ row }"><slot name="action" :row="row"><WatchlistAddButton :row="row" /></slot></template>
    </el-table-column>
    <template #empty>
      <el-empty :description="emptyText" />
    </template>
  </el-table>
</template>

<style scoped>
.muted { color: #909399; }
.small { font-size: 12px; margin-top: 3px; }
.score { font-size: 22px; font-weight: 750; color: #303133; }
.stock-link { color: #337ecc; font-weight: 650; text-decoration: none; }
.parts { display: flex; gap: 4px; flex-wrap: wrap; }
.parts span { background: #f2f6fc; border-radius: 4px; padding: 2px 5px; font-size: 12px; }
.danger, .blocker { color: #f56c6c; }
.pass { color: #529b2e; font-weight: 600; }
.note { color: #a77700; }
</style>
